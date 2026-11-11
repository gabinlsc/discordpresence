"""Logique Rich Presence : validation, construction du payload et thread d'IO.

Tout l'IO pypresence (IPC local avec le client Discord desktop) est confiné dans
``PresenceWorker`` (un thread dédié). L'UI lui parle via une queue de commandes
et reçoit des ``StatusEvent`` via une queue d'événements : l'UI ne bloque jamais.
"""
from __future__ import annotations

import asyncio
import logging
import queue
import re
import threading
import time
from dataclasses import dataclass
from enum import Enum
from typing import Any
from urllib.parse import urlsplit

from pypresence import Presence
from pypresence.exceptions import DiscordNotFound, InvalidID, PyPresenceException

log = logging.getLogger(__name__)

MAX_LEN = 128                 # longueur max des champs texte
MIN_TEXT_LEN = 2              # Discord refuse details/state/tooltips d'1 caractère
BUTTON_LABEL_MAX = 32         # limite Discord pour le libellé d'un bouton
URL_MAX = 512
MIN_UPDATE_INTERVAL = 15.0    # rate limit Discord : 1 update / 15 s
BACKOFF_START = 2.0
BACKOFF_MAX = 60.0
TICK = 0.25                   # période de réveil du worker (s)


class TimerMode(str, Enum):
    """Mode d'affichage du timer."""

    NONE = "none"
    LAUNCH = "launch"
    CUSTOM = "custom"
    COUNTDOWN = "countdown"


TIMER_LABELS: dict[TimerMode, str] = {
    TimerMode.NONE: "Aucun",
    TimerMode.LAUNCH: "Temps écoulé depuis le lancement",
    TimerMode.CUSTOM: "Temps écoulé custom (HH:MM)",
    TimerMode.COUNTDOWN: "Compte à rebours (HH:MM)",
}

# Valeurs par défaut d'un profil / du formulaire (toutes des chaînes).
DEFAULT_FORM: dict[str, str] = {
    "client_id": "",
    "app_name": "",
    "details": "",
    "state": "",
    "large_image": "",
    "large_text": "",
    "small_image": "",
    "small_text": "",
    "btn1_label": "",
    "btn1_url": "",
    "btn2_label": "",
    "btn2_url": "",
    "timer_mode": TimerMode.NONE.value,
    "timer_hhmm": "00:00",
    "party_cur": "",
    "party_max": "",
}

_HHMM_RE = re.compile(r"^(\d{1,3}):([0-5]\d)$", re.ASCII)
_CLIENT_ID_RE = re.compile(r"^\d{17,20}$", re.ASCII)


# --------------------------------------------------------------------------- #
# Validation & construction du payload
# --------------------------------------------------------------------------- #
def parse_hhmm(text: str) -> int | None:
    """Convertit 'HH:MM' en secondes ; None si le format est invalide."""
    match = _HHMM_RE.match(text.strip())
    if match is None:
        return None
    return int(match.group(1)) * 3600 + int(match.group(2)) * 60


def is_valid_http_url(url: str) -> bool:
    """True si ``url`` est une URL http(s) absolue, sans espace."""
    if not url or len(url) > URL_MAX or any(c.isspace() for c in url):
        return False
    try:
        parts = urlsplit(url)
        return parts.scheme in ("http", "https") and bool(parts.hostname)
    except ValueError:
        return False


def _check_length(form: dict[str, str], key: str, errors: dict[str, str], min_len: int = 0) -> None:
    """Vérifie max 128 caractères (et un minimum si le champ n'est pas vide)."""
    length = len(form[key].strip())
    if length > MAX_LEN:
        errors[key] = f"Maximum {MAX_LEN} caractères ({length})."
    elif 0 < length < min_len:
        errors[key] = f"Minimum {min_len} caractères."


def _check_button(form: dict[str, str], idx: int, errors: dict[str, str]) -> None:
    label_key, url_key = f"btn{idx}_label", f"btn{idx}_url"
    label, url = form[label_key].strip(), form[url_key].strip()
    if not label and not url:
        return
    if not label:
        errors[label_key] = "Libellé requis."
    elif len(label) > BUTTON_LABEL_MAX:
        errors[label_key] = f"Maximum {BUTTON_LABEL_MAX} caractères ({len(label)})."
    if not url:
        errors[url_key] = "URL requise."
    elif not is_valid_http_url(url):
        errors[url_key] = "URL invalide (http:// ou https:// attendu)."


def validate(form: dict[str, str], require_client_id: bool = True) -> dict[str, str]:
    """Valide le formulaire. Retourne ``{champ: message}`` (vide si tout est OK)."""
    errors: dict[str, str] = {}

    client_id = form["client_id"].strip()
    if not client_id:
        if require_client_id:
            errors["client_id"] = "Client ID requis."
    elif not _CLIENT_ID_RE.match(client_id):
        errors["client_id"] = "Client ID invalide (17 à 20 chiffres)."

    for key in ("app_name", "large_image", "small_image"):
        _check_length(form, key, errors)
    for key in ("details", "state", "large_text", "small_text"):
        _check_length(form, key, errors, MIN_TEXT_LEN)

    _check_button(form, 1, errors)
    _check_button(form, 2, errors)

    mode = form["timer_mode"]
    if mode in (TimerMode.CUSTOM.value, TimerMode.COUNTDOWN.value):
        seconds = parse_hhmm(form["timer_hhmm"])
        if seconds is None:
            errors["timer_hhmm"] = "Format HH:MM attendu (ex. 01:30)."
        elif mode == TimerMode.COUNTDOWN.value and seconds == 0:
            errors["timer_hhmm"] = "Durée supérieure à 00:00 requise."

    cur, mx = form["party_cur"].strip(), form["party_max"].strip()
    if cur or mx:
        if not cur or not mx:
            errors["party"] = "Renseignez les deux valeurs (x / y)."
        elif not (cur.isascii() and cur.isdigit() and mx.isascii() and mx.isdigit()):
            errors["party"] = "Nombres entiers requis."
        elif not 1 <= int(cur) <= int(mx) <= 999:
            errors["party"] = "Attendu : 1 ≤ x ≤ y ≤ 999."
    return errors


@dataclass(frozen=True)
class PresencePayload:
    """Contenu immuable d'une activité Rich Presence."""

    name: str = ""
    details: str = ""
    state: str = ""
    large_image: str = ""
    large_text: str = ""
    small_image: str = ""
    small_text: str = ""
    party: tuple[int, int] | None = None
    buttons: tuple[tuple[str, str], ...] = ()
    start: int | None = None
    end: int | None = None

    def to_kwargs(self) -> dict[str, Any]:
        """Arguments pour ``Presence.update`` (les champs vides sont omis)."""
        kwargs: dict[str, Any] = {}
        for name in ("name", "details", "state", "large_image", "large_text", "small_image", "small_text"):
            value: str = getattr(self, name)
            if value:
                kwargs[name] = value
        if self.party is not None:
            kwargs["party_size"] = [self.party[0], self.party[1]]
        if self.buttons:
            kwargs["buttons"] = [{"label": label, "url": url} for label, url in self.buttons]
        if self.start is not None:
            kwargs["start"] = self.start
        if self.end is not None:
            kwargs["end"] = self.end
        return kwargs


class TimerResolver:
    """Calcule start/end en gardant une ancre stable tant que la config du timer ne change pas.

    Sans cela, chaque mise à jour relancerait le timer custom / le compte à rebours.
    """

    def __init__(self, launch_ts: int | None = None) -> None:
        self._launch_ts = launch_ts if launch_ts is not None else int(time.time())
        self._key: tuple[str, str] | None = None
        self._anchor = self._launch_ts

    def reset(self) -> None:
        """Force une nouvelle ancre (ex. à la (re)connexion)."""
        self._key = None

    def resolve(self, mode: str, hhmm: str) -> tuple[int | None, int | None]:
        """Retourne ``(start, end)`` en secondes epoch pour le mode demandé."""
        key = (mode, hhmm.strip())
        if key != self._key:
            self._key = key
            self._anchor = int(time.time())
        if mode == TimerMode.LAUNCH.value:
            return self._launch_ts, None
        seconds = parse_hhmm(hhmm) or 0
        if mode == TimerMode.CUSTOM.value:
            return self._anchor - seconds, None
        if mode == TimerMode.COUNTDOWN.value:
            return None, self._anchor + seconds
        return None, None


def build_payload(form: dict[str, str], resolver: TimerResolver) -> PresencePayload:
    """Construit le payload à partir d'un formulaire DÉJÀ validé."""
    buttons: list[tuple[str, str]] = []
    for idx in (1, 2):
        label, url = form[f"btn{idx}_label"].strip(), form[f"btn{idx}_url"].strip()
        if label and url:
            buttons.append((label, url))
    cur, mx = form["party_cur"].strip(), form["party_max"].strip()
    party = (int(cur), int(mx)) if cur and mx else None
    start, end = resolver.resolve(form["timer_mode"], form["timer_hhmm"])
    return PresencePayload(
        name=form["app_name"].strip(),
        details=form["details"].strip(),
        state=form["state"].strip(),
        large_image=form["large_image"].strip(),
        large_text=form["large_text"].strip(),
        small_image=form["small_image"].strip(),
        small_text=form["small_text"].strip(),
        party=party,
        buttons=tuple(buttons),
        start=start,
        end=end,
    )


# --------------------------------------------------------------------------- #
# Protocole UI <-> worker
# --------------------------------------------------------------------------- #
class Status(Enum):
    """États affichés par l'indicateur de l'UI."""

    DISCONNECTED = "disconnected"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    NOT_FOUND = "not_found"
    ERROR = "error"


@dataclass(frozen=True)
class StatusEvent:
    """Événement worker → UI. ``fatal`` : le worker a cessé toute tentative."""

    status: Status
    message: str
    fatal: bool = False


@dataclass(frozen=True)
class Connect:
    """Commande : (re)connecter avec ce Client ID et publier ce payload."""

    client_id: str
    payload: PresencePayload


@dataclass(frozen=True)
class Update:
    """Commande : publier un nouveau payload (soumis au rate limit)."""

    payload: PresencePayload


@dataclass(frozen=True)
class Disconnect:
    """Commande : effacer l'activité et fermer la connexion."""


@dataclass(frozen=True)
class Shutdown:
    """Commande : arrêter le thread."""


Command = Connect | Update | Disconnect | Shutdown


class PresenceWorker(threading.Thread):
    """Thread dédié à l'IO pypresence : connexion, retry avec backoff, updates limitées."""

    def __init__(self, events: "queue.Queue[StatusEvent]") -> None:
        super().__init__(name="presence-io", daemon=True)
        self._commands: "queue.Queue[Command]" = queue.Queue()
        self._events = events
        self._loop: asyncio.AbstractEventLoop | None = None
        self._rpc: Presence | None = None
        self._client_id = ""
        self._wanted = False
        self._payload: PresencePayload | None = None
        self._last_sent: PresencePayload | None = None
        self._last_update_at = 0.0
        self._backoff = BACKOFF_START
        self._next_attempt = 0.0
        self._last_wait_shown = -1

    # -- API publique (appelée depuis le thread UI) -------------------------- #
    def send(self, command: Command) -> None:
        """Poste une commande au worker (non bloquant)."""
        self._commands.put(command)

    def shutdown(self) -> None:
        """Demande l'arrêt et attend brièvement la fin du thread."""
        self._commands.put(Shutdown())
        if self.is_alive():
            self.join(timeout=3.0)

    # -- Boucle principale --------------------------------------------------- #
    def run(self) -> None:
        self._loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self._loop)
        try:
            while True:
                try:
                    command: Command | None = self._commands.get(timeout=TICK)
                except queue.Empty:
                    command = None
                if isinstance(command, Shutdown):
                    break
                if command is not None:
                    self._dispatch(command)
                self._tick()
        except Exception:  # filet de sécurité : ne jamais mourir silencieusement
            log.exception("Erreur fatale dans le worker")
            self._emit(Status.ERROR, "Erreur interne (voir les logs).", fatal=True)
        finally:
            self._close_rpc(clear=True)
            self._loop.close()

    def _emit(self, status: Status, message: str, fatal: bool = False) -> None:
        self._events.put(StatusEvent(status, message, fatal))

    # -- Commandes ----------------------------------------------------------- #
    def _dispatch(self, command: Command) -> None:
        if isinstance(command, Connect):
            self._wanted = True
            self._payload = command.payload
            if self._rpc is not None and command.client_id != self._client_id:
                self._close_rpc(clear=False)
            self._client_id = command.client_id
            if self._rpc is None:
                self._backoff = BACKOFF_START
                self._next_attempt = 0.0
        elif isinstance(command, Update):
            self._payload = command.payload
        elif isinstance(command, Disconnect):
            self._wanted = False
            self._close_rpc(clear=True)
            self._emit(Status.DISCONNECTED, "Déconnecté.")

    # -- Cycle de vie de la connexion --------------------------------------- #
    def _tick(self) -> None:
        if not self._wanted:
            return
        now = time.monotonic()
        if self._rpc is None:
            if now >= self._next_attempt:
                self._try_connect(now)
            return
        if self._payload is None or self._payload == self._last_sent:
            return
        wait = MIN_UPDATE_INTERVAL - (now - self._last_update_at)
        if wait <= 0:
            self._push(now)
        else:
            secs = int(wait) + 1
            if secs != self._last_wait_shown:  # évite de saturer l'UI d'événements
                self._last_wait_shown = secs
                self._emit(Status.CONNECTED, f"Connecté – mise à jour dans {secs} s")

    def _schedule_retry(self, now: float) -> float:
        """Programme la prochaine tentative (backoff exponentiel) et retourne le délai."""
        delay = self._backoff
        self._next_attempt = now + delay
        self._backoff = min(self._backoff * 2, BACKOFF_MAX)
        return delay

    def _try_connect(self, now: float) -> None:
        self._emit(Status.CONNECTING, "Connexion à Discord…")
        rpc: Presence | None = None
        try:
            rpc = Presence(self._client_id, loop=self._loop)
            rpc.connect()
        except InvalidID:
            self._abort_rpc(rpc)
            self._wanted = False
            self._emit(Status.ERROR, "Client ID refusé par Discord.", fatal=True)
            log.error("Client ID invalide : %s", self._client_id)
            return
        except DiscordNotFound:
            self._abort_rpc(rpc)
            delay = self._schedule_retry(now)
            self._emit(Status.NOT_FOUND, f"Discord introuvable – nouvel essai dans {delay:.0f} s")
            log.warning("Discord introuvable, retry dans %.0f s", delay)
            return
        except (PyPresenceException, OSError, asyncio.TimeoutError) as exc:
            self._abort_rpc(rpc)
            delay = self._schedule_retry(now)
            self._emit(Status.ERROR, f"Erreur : {exc or type(exc).__name__} – retry dans {delay:.0f} s")
            log.warning("Échec de connexion : %r", exc)
            return
        self._rpc = rpc
        self._backoff = BACKOFF_START
        self._last_sent = None
        self._last_update_at = float("-inf")  # première publication immédiate
        self._last_wait_shown = -1
        self._emit(Status.CONNECTED, "Connecté.")
        log.info("Connecté à Discord (client_id=%s)", self._client_id)

    def _push(self, now: float) -> None:
        assert self._rpc is not None and self._payload is not None
        payload = self._payload
        kwargs = payload.to_kwargs()
        try:
            if kwargs:
                self._rpc.update(**kwargs)
            else:
                self._rpc.clear()
        except (BrokenPipeError, ConnectionError, OSError) as exc:
            # Discord a été fermé : on repart en boucle de reconnexion.
            log.warning("Connexion perdue : %r", exc)
            self._abort_rpc(self._rpc)
            self._rpc = None
            delay = self._schedule_retry(now)
            self._emit(Status.NOT_FOUND, f"Connexion perdue – nouvel essai dans {delay:.0f} s")
            return
        except PyPresenceException as exc:
            # Si la connexion est fermée côté pipe, reconnecter ; sinon erreur de payload.
            name = type(exc).__name__
            self._last_update_at = now
            if name in ("PipeClosed", "InvalidPipe", "ConnectionTimeout", "ResponseTimeout"):
                self._abort_rpc(self._rpc)
                self._rpc = None
                delay = self._schedule_retry(now)
                self._emit(Status.NOT_FOUND, f"Connexion perdue – nouvel essai dans {delay:.0f} s")
            else:
                self._last_sent = payload  # ne pas réessayer en boucle le même payload
                self._emit(Status.ERROR, f"Discord a refusé la mise à jour : {exc}")
            log.warning("Échec d'update : %r", exc)
            return
        self._last_sent = payload
        self._last_update_at = now
        self._last_wait_shown = -1
        self._emit(Status.CONNECTED, f"Connecté – mis à jour à {time.strftime('%H:%M:%S')}")
        log.info("Presence mise à jour")

    def _abort_rpc(self, rpc: Presence | None) -> None:
        """Ferme un client sans lever d'exception."""
        if rpc is None:
            return
        try:
            rpc.close()
        except Exception:  # noqa: BLE001 - fermeture best-effort
            pass

    def _close_rpc(self, clear: bool) -> None:
        """Ferme la connexion courante (en effaçant éventuellement l'activité)."""
        rpc, self._rpc = self._rpc, None
        self._last_sent = None
        if rpc is None:
            return
        if clear:
            try:
                rpc.clear()
            except Exception:  # noqa: BLE001
                pass
        self._abort_rpc(rpc)
