"""Interface customtkinter (thème sombre, fenêtre unique non redimensionnable)."""
from __future__ import annotations

import logging
import queue
import sys
import time
import tkinter as tk
from types import TracebackType
from typing import Any

import customtkinter as ctk

from presence import (
    DEFAULT_FORM,
    TIMER_LABELS,
    Connect,
    Disconnect,
    PresenceWorker,
    Status,
    StatusEvent,
    TimerMode,
    TimerResolver,
    Update,
    build_payload,
    validate,
)
from storage import APP_NAME, ProfileStore, is_autostart_enabled, set_autostart

try:  # la barre système est optionnelle : l'appli reste utilisable sans
    import pystray
    from PIL import Image, ImageDraw
except ImportError:  # pragma: no cover
    pystray = None  # type: ignore[assignment]

log = logging.getLogger(__name__)

STATUS_COLORS: dict[Status, str] = {
    Status.DISCONNECTED: "#747f8d",
    Status.CONNECTING: "#faa61a",
    Status.CONNECTED: "#3ba55d",
    Status.NOT_FOUND: "#f0883e",
    Status.ERROR: "#ed4245",
}
STATUS_TITLES: dict[Status, str] = {
    Status.DISCONNECTED: "Déconnecté",
    Status.CONNECTING: "Connexion…",
    Status.CONNECTED: "Connecté",
    Status.NOT_FOUND: "Discord introuvable",
    Status.ERROR: "Erreur",
}
ERROR_COLOR = "#ed4245"
OK_COLOR = "#3ba55d"
DEBOUNCE_MS = 800
POLL_MS = 150
LABEL_TO_MODE = {label: mode.value for mode, label in TIMER_LABELS.items()}
MODE_TO_LABEL = {mode.value: label for mode, label in TIMER_LABELS.items()}
TEXT_KEYS = [k for k in DEFAULT_FORM if k != "timer_mode"]


def _make_tray_image() -> "Image.Image":
    """Génère l'icône de la barre système (carré arrondi bleu + disque blanc)."""
    image = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.rounded_rectangle((4, 4, 60, 60), radius=14, fill=(88, 101, 242, 255))
    draw.ellipse((20, 20, 44, 44), fill=(255, 255, 255, 255))
    return image


class App(ctk.CTk):
    """Fenêtre principale."""

    def __init__(self, store: ProfileStore, start_minimized: bool = False) -> None:
        ctk.set_appearance_mode("dark")
        ctk.set_default_color_theme("blue")
        super().__init__()
        self.title("Discord Presence Studio")
        self.geometry("600x660")
        self.resizable(False, False)

        self.store = store
        self.events: "queue.Queue[StatusEvent]" = queue.Queue()
        self.ui_actions: "queue.Queue[str]" = queue.Queue()  # actions venant du thread tray
        self.worker = PresenceWorker(self.events)
        self.worker.start()
        self.resolver = TimerResolver()

        self._wanted = False              # l'utilisateur veut être connecté
        self._active_client_id = ""
        self._loading = False             # True pendant le chargement d'un profil
        self._debounce_job: str | None = None
        self._tray: Any = None
        self._closing = False

        self.vars: dict[str, tk.StringVar] = {k: tk.StringVar(value=v) for k, v in DEFAULT_FORM.items()}
        self.vars["timer_mode"].set(MODE_TO_LABEL[DEFAULT_FORM["timer_mode"]])
        self.err_labels: dict[str, ctk.CTkLabel] = {}
        self.entries: dict[str, ctk.CTkEntry] = {}
        self.tray_var = tk.BooleanVar(value=store.settings["minimize_to_tray"] and pystray is not None)
        self.autostart_var = tk.BooleanVar(value=is_autostart_enabled())

        self._build_profile_bar()
        self._build_tabs()
        self._build_bottom_bar()
        self._refresh_profiles()
        self._load_initial_profile()
        self._update_timer_entry_state()
        self._refresh_buttons()

        for var in self.vars.values():
            var.trace_add("write", self._on_form_change)

        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.bind("<Unmap>", self._on_unmap)
        self.after(POLL_MS, self._poll)

        if start_minimized:
            self.after(150, self._startup_minimized)

    # ------------------------------------------------------------------ #
    # Construction de l'interface
    # ------------------------------------------------------------------ #
    def _build_profile_bar(self) -> None:
        bar = ctk.CTkFrame(self)
        bar.pack(fill="x", padx=16, pady=(14, 6))
        ctk.CTkLabel(bar, text="Profil").pack(side="left", padx=(12, 8), pady=10)
        self.profile_box = ctk.CTkComboBox(bar, values=[""], width=240, command=self._on_profile_selected)
        self.profile_box.pack(side="left", pady=10)
        ctk.CTkButton(bar, text="Enregistrer", width=100, command=self._on_save_profile).pack(side="left", padx=8)
        ctk.CTkButton(
            bar, text="Supprimer", width=90, fg_color="#4f545c", hover_color="#ed4245", command=self._on_delete_profile
        ).pack(side="left")
        self.profile_msg = ctk.CTkLabel(self, text="", anchor="w", font=ctk.CTkFont(size=11))
        self.profile_msg.pack(fill="x", padx=20)

    def _build_tabs(self) -> None:
        tabs = ctk.CTkTabview(self, height=420)
        tabs.pack(fill="both", expand=True, padx=16, pady=(4, 6))
        for name in ("Présence", "Images", "Boutons", "Timer & groupe", "Paramètres"):
            tabs.add(name)

        t = tabs.tab("Présence")
        self._add_field(t, "Client ID (portail développeur)", "client_id", "ex. 123456789012345678")
        self._add_field(t, "Nom de l'application (mémo, voir note)", "app_name", "Nom défini dans le portail")
        self._add_field(t, "Details (ligne 1)", "details", "ex. En train de coder")
        self._add_field(t, "State (ligne 2)", "state", "ex. Projet secret")
        ctk.CTkLabel(
            t,
            text="Le nom affiché après « Joue à » est celui de l'application dans le portail.",
            font=ctk.CTkFont(size=11),
            text_color="#9aa0a6",
            anchor="w",
        ).pack(fill="x")

        t = tabs.tab("Images")
        self._add_field(t, "Grande image – clé de l'asset", "large_image", "clé uploadée dans Art Assets")
        self._add_field(t, "Grande image – texte au survol", "large_text")
        self._add_field(t, "Petite image – clé de l'asset", "small_image", "clé uploadée dans Art Assets")
        self._add_field(t, "Petite image – texte au survol", "small_text")

        t = tabs.tab("Boutons")
        self._add_field(t, "Bouton 1 – libellé", "btn1_label", "max 32 caractères")
        self._add_field(t, "Bouton 1 – URL", "btn1_url", "https://…")
        self._add_field(t, "Bouton 2 – libellé", "btn2_label", "max 32 caractères")
        self._add_field(t, "Bouton 2 – URL", "btn2_url", "https://…")
        ctk.CTkLabel(
            t,
            text="Vos propres boutons ne sont pas visibles sur votre client (visibles par les autres).",
            font=ctk.CTkFont(size=11),
            text_color="#9aa0a6",
            anchor="w",
        ).pack(fill="x")

        t = tabs.tab("Timer & groupe")
        ctk.CTkLabel(t, text="Timer", anchor="w").pack(fill="x")
        self.timer_menu = ctk.CTkOptionMenu(
            t,
            values=list(TIMER_LABELS.values()),
            variable=self.vars["timer_mode"],
            command=lambda _v: self._update_timer_entry_state(),
        )
        self.timer_menu.pack(fill="x", pady=(0, 10))
        self._add_field(t, "Durée / décalage (HH:MM)", "timer_hhmm", "01:30")
        self._add_party_field(t)

        t = tabs.tab("Paramètres")
        ctk.CTkCheckBox(
            t,
            text="Minimiser dans la barre système",
            variable=self.tray_var,
            command=self._on_tray_toggle,
        ).pack(anchor="w", pady=(8, 4))
        ctk.CTkCheckBox(
            t,
            text="Démarrer avec Windows (lancement réduit + connexion auto)",
            variable=self.autostart_var,
            command=self._on_autostart_toggle,
        ).pack(anchor="w", pady=4)
        self.settings_msg = ctk.CTkLabel(t, text="", anchor="w", wraplength=500, justify="left")
        self.settings_msg.pack(fill="x", pady=(8, 0))

    def _add_field(self, parent: Any, label: str, key: str, placeholder: str = "") -> None:
        """Ajoute label + message d'erreur inline + champ de saisie."""
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x", pady=(0, 6))
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text=label, anchor="w").pack(side="left")
        err = ctk.CTkLabel(head, text="", text_color=ERROR_COLOR, anchor="e", font=ctk.CTkFont(size=11))
        err.pack(side="right")
        entry = ctk.CTkEntry(box, textvariable=self.vars[key], placeholder_text=placeholder)
        entry.pack(fill="x")
        self.err_labels[key] = err
        self.entries[key] = entry

    def _add_party_field(self, parent: Any) -> None:
        box = ctk.CTkFrame(parent, fg_color="transparent")
        box.pack(fill="x", pady=(0, 6))
        head = ctk.CTkFrame(box, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(head, text="Taille de groupe (x / y, optionnel)", anchor="w").pack(side="left")
        err = ctk.CTkLabel(head, text="", text_color=ERROR_COLOR, anchor="e", font=ctk.CTkFont(size=11))
        err.pack(side="right")
        row = ctk.CTkFrame(box, fg_color="transparent")
        row.pack(fill="x")
        ctk.CTkEntry(row, textvariable=self.vars["party_cur"], width=80, placeholder_text="x").pack(side="left")
        ctk.CTkLabel(row, text="/").pack(side="left", padx=8)
        ctk.CTkEntry(row, textvariable=self.vars["party_max"], width=80, placeholder_text="y").pack(side="left")
        self.err_labels["party"] = err

    def _build_bottom_bar(self) -> None:
        bar = ctk.CTkFrame(self)
        bar.pack(fill="x", padx=16, pady=(0, 14))
        self.status_dot = ctk.CTkLabel(bar, text="●", font=ctk.CTkFont(size=20), width=24)
        self.status_dot.pack(side="left", padx=(12, 4), pady=10)
        self.status_label = ctk.CTkLabel(bar, text="", anchor="w", width=210)
        self.status_label.pack(side="left")
        self.btn_disconnect = ctk.CTkButton(
            bar, text="Déconnecter", width=100, fg_color="#4f545c", hover_color="#ed4245", command=self._on_disconnect
        )
        self.btn_disconnect.pack(side="right", padx=(6, 12))
        self.btn_update = ctk.CTkButton(bar, text="Mettre à jour", width=110, command=self._on_update)
        self.btn_update.pack(side="right", padx=6)
        self.btn_connect = ctk.CTkButton(bar, text="Connecter", width=100, command=self._on_connect)
        self.btn_connect.pack(side="right", padx=6)
        self._set_status(Status.DISCONNECTED, "Non connecté")

    # ------------------------------------------------------------------ #
    # Formulaire
    # ------------------------------------------------------------------ #
    def _collect(self) -> dict[str, str]:
        """Lit le formulaire (le timer est converti de son libellé vers sa valeur stable)."""
        values = {k: self.vars[k].get() for k in TEXT_KEYS}
        values["timer_mode"] = LABEL_TO_MODE.get(self.vars["timer_mode"].get(), TimerMode.NONE.value)
        return values

    def _apply(self, values: dict[str, str]) -> None:
        """Remplit le formulaire sans déclencher de mise à jour en direct."""
        self._loading = True
        try:
            merged = {**DEFAULT_FORM, **values}
            for key in TEXT_KEYS:
                self.vars[key].set(merged[key])
            self.vars["timer_mode"].set(MODE_TO_LABEL.get(merged["timer_mode"], MODE_TO_LABEL["none"]))
        finally:
            self._loading = False
        self._show_errors(validate(self._collect(), require_client_id=False))
        self._update_timer_entry_state()

    def _show_errors(self, errors: dict[str, str]) -> None:
        """Affiche les messages inline et colore les champs en erreur."""
        for key, label in self.err_labels.items():
            label.configure(text=errors.get(key, ""))
            entry = self.entries.get(key)
            if entry is not None:
                entry.configure(border_color=ERROR_COLOR if key in errors else ["#979da2", "#565b5e"])

    def _update_timer_entry_state(self) -> None:
        mode = LABEL_TO_MODE.get(self.vars["timer_mode"].get(), "none")
        needs_time = mode in (TimerMode.CUSTOM.value, TimerMode.COUNTDOWN.value)
        self.entries["timer_hhmm"].configure(state="normal" if needs_time else "disabled")

    def _on_form_change(self, *_: Any) -> None:
        if self._loading:
            return
        self._show_errors(validate(self._collect(), require_client_id=False))
        self._update_timer_entry_state()
        if self._wanted:  # debounce : on attend 800 ms sans frappe avant d'envoyer
            if self._debounce_job is not None:
                self.after_cancel(self._debounce_job)
            self._debounce_job = self.after(DEBOUNCE_MS, self._live_update)

    def _live_update(self) -> None:
        """Mise à jour en direct (sans reconnexion). Le worker applique le rate limit de 15 s."""
        self._debounce_job = None
        values = self._collect()
        if not self._wanted or values["client_id"].strip() != self._active_client_id:
            return  # un changement de Client ID exige un clic explicite sur « Mettre à jour »
        if validate(values):
            return
        self.worker.send(Update(build_payload(values, self.resolver)))

    # ------------------------------------------------------------------ #
    # Connexion
    # ------------------------------------------------------------------ #
    def _on_connect(self) -> None:
        values = self._collect()
        errors = validate(values)
        self._show_errors(errors)
        if errors:
            return
        self._wanted = True
        self._active_client_id = values["client_id"].strip()
        self.resolver.reset()
        self.worker.send(Connect(self._active_client_id, build_payload(values, self.resolver)))
        self._refresh_buttons()

    def _on_update(self) -> None:
        if not self._wanted:
            return
        values = self._collect()
        errors = validate(values)
        self._show_errors(errors)
        if errors:
            return
        payload = build_payload(values, self.resolver)
        client_id = values["client_id"].strip()
        if client_id != self._active_client_id:
            self._active_client_id = client_id
            self.worker.send(Connect(client_id, payload))
        else:
            self.worker.send(Update(payload))

    def _on_disconnect(self) -> None:
        self._wanted = False
        self.worker.send(Disconnect())
        self._refresh_buttons()

    def _refresh_buttons(self) -> None:
        self.btn_connect.configure(state="disabled" if self._wanted else "normal")
        self.btn_update.configure(state="normal" if self._wanted else "disabled")
        self.btn_disconnect.configure(state="normal" if self._wanted else "disabled")

    def _set_status(self, status: Status, message: str) -> None:
        self.status_dot.configure(text_color=STATUS_COLORS[status])
        self.status_label.configure(text=f"{STATUS_TITLES[status]} – {message}" if message else STATUS_TITLES[status])

    def _poll(self) -> None:
        """Consomme les événements du worker et les actions de la barre système."""
        if self._closing:
            return
        try:
            while True:
                event = self.events.get_nowait()
                self._set_status(event.status, event.message)
                if event.fatal:
                    self._wanted = False
                    self._refresh_buttons()
        except queue.Empty:
            pass
        try:
            while True:
                action = self.ui_actions.get_nowait()
                if action == "show":
                    self._restore()
                elif action == "quit":
                    self._quit()
                    return
        except queue.Empty:
            pass
        self.after(POLL_MS, self._poll)

    # ------------------------------------------------------------------ #
    # Profils
    # ------------------------------------------------------------------ #
    def _refresh_profiles(self) -> None:
        names = self.store.names()
        self.profile_box.configure(values=names or [""])

    def _load_initial_profile(self) -> None:
        name = self.store.last_profile
        if name in self.store.profiles:
            self.profile_box.set(name)
            self._apply(self.store.profiles[name])

    def _profile_message(self, text: str, ok: bool = True) -> None:
        self.profile_msg.configure(text=text, text_color=OK_COLOR if ok else ERROR_COLOR)

    def _on_profile_selected(self, name: str) -> None:
        values = self.store.profiles.get(name)
        if values is None:
            return
        self._apply(values)
        self.store.set_last(name)
        self._profile_message(f"Profil « {name} » chargé.")
        if self._wanted:
            self._debounce_job = self.after(0, self._live_update)

    def _on_save_profile(self) -> None:
        name = self.profile_box.get().strip()
        if not name:
            self._profile_message("Saisissez un nom de profil dans la liste.", ok=False)
            return
        if len(name) > 64:
            self._profile_message("Nom de profil trop long (64 max).", ok=False)
            return
        try:
            self.store.put(name, self._collect())
        except OSError as exc:
            log.exception("Échec d'enregistrement du profil")
            self._profile_message(f"Écriture impossible : {exc}", ok=False)
            return
        self._refresh_profiles()
        self.profile_box.set(name)
        self._profile_message(f"Profil « {name} » enregistré.")

    def _on_delete_profile(self) -> None:
        name = self.profile_box.get().strip()
        if name not in self.store.profiles:
            self._profile_message("Aucun profil sélectionné.", ok=False)
            return
        try:
            self.store.delete(name)
        except OSError as exc:
            self._profile_message(f"Écriture impossible : {exc}", ok=False)
            return
        self._refresh_profiles()
        self.profile_box.set("")
        self._profile_message(f"Profil « {name} » supprimé.")

    # ------------------------------------------------------------------ #
    # Paramètres, barre système, fermeture
    # ------------------------------------------------------------------ #
    def _settings_message(self, text: str, ok: bool = True) -> None:
        self.settings_msg.configure(text=text, text_color=OK_COLOR if ok else ERROR_COLOR)

    def _on_tray_toggle(self) -> None:
        enabled = self.tray_var.get()
        if enabled and pystray is None:
            self.tray_var.set(False)
            self._settings_message("pystray / Pillow non installés.", ok=False)
            return
        try:
            self.store.set_setting("minimize_to_tray", enabled)
            self._settings_message("")
        except OSError as exc:
            self._settings_message(f"Écriture impossible : {exc}", ok=False)

    def _on_autostart_toggle(self) -> None:
        enabled = self.autostart_var.get()
        try:
            set_autostart(enabled)
            self._settings_message("Démarrage avec Windows " + ("activé." if enabled else "désactivé."))
        except OSError as exc:
            log.exception("Échec de la modification de la clé Run")
            self.autostart_var.set(not enabled)
            self._settings_message(f"Impossible de modifier le registre : {exc}", ok=False)

    def _tray_enabled(self) -> bool:
        return pystray is not None and self.tray_var.get()

    def _on_unmap(self, event: tk.Event) -> None:
        """Minimiser la fenêtre → barre système (si l'option est cochée)."""
        if event.widget is self and self._tray_enabled() and self.state() == "iconic":
            self.after(10, self._hide_to_tray)

    def _on_close(self) -> None:
        if self._tray_enabled():
            self._hide_to_tray()
        else:
            self._quit()

    def _hide_to_tray(self) -> None:
        self.withdraw()
        if self._tray is not None:
            return
        menu = pystray.Menu(
            pystray.MenuItem("Ouvrir", lambda icon, item: self.ui_actions.put("show"), default=True),
            pystray.MenuItem("Quitter", lambda icon, item: self.ui_actions.put("quit")),
        )
        self._tray = pystray.Icon(APP_NAME, _make_tray_image(), "Discord Presence Studio", menu)
        self._tray.run_detached()

    def _stop_tray(self) -> None:
        tray, self._tray = self._tray, None
        if tray is not None:
            try:
                tray.stop()
            except Exception:  # noqa: BLE001
                log.exception("Arrêt de l'icône système")

    def _restore(self) -> None:
        self._stop_tray()
        self.deiconify()
        self.lift()
        self.focus_force()

    def _startup_minimized(self) -> None:
        """Lancement via --minimized : connexion auto si le profil est valide, puis réduction."""
        values = self._collect()
        if not validate(values):
            self._on_connect()
        if self._tray_enabled():
            self._hide_to_tray()
        else:
            self.iconify()

    def _quit(self) -> None:
        if self._closing:
            return
        self._closing = True
        self._stop_tray()
        self.worker.shutdown()
        self.destroy()

    def report_callback_exception(
        self, exc: type[BaseException], val: BaseException, tb: TracebackType | None
    ) -> None:
        """Journalise les exceptions des callbacks Tk (pas de console en --noconsole)."""
        log.error("Exception dans un callback Tk", exc_info=(exc, val, tb))
