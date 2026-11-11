"""Persistance : profils JSON, paramètres, répertoire AppData et démarrage avec Windows."""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

from presence import DEFAULT_FORM

log = logging.getLogger(__name__)

APP_NAME = "DiscordPresenceStudio"
RUN_KEY = r"Software\Microsoft\Windows\CurrentVersion\Run"
DEFAULT_SETTINGS: dict[str, bool] = {"minimize_to_tray": False}


def app_dir() -> Path:
    """Retourne (et crée) ``%APPDATA%/<APP_NAME>`` (repli ~/.config hors Windows)."""
    base = os.environ.get("APPDATA")
    root = Path(base) if base else Path.home() / ".config"
    path = root / APP_NAME
    path.mkdir(parents=True, exist_ok=True)
    return path


class ProfileStore:
    """Lecture/écriture atomique de ``profiles.json`` (profils, dernier profil, paramètres)."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or app_dir() / "profiles.json"
        self.profiles: dict[str, dict[str, str]] = {}
        self.last_profile: str = ""
        self.settings: dict[str, bool] = dict(DEFAULT_SETTINGS)
        self._load()

    # -- Lecture ------------------------------------------------------------- #
    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw: Any = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                raise ValueError("racine JSON invalide")
        except (OSError, ValueError) as exc:
            backup = self.path.with_suffix(".json.bak")
            log.error("profiles.json illisible (%r) – sauvegarde vers %s", exc, backup)
            try:
                self.path.replace(backup)
            except OSError:
                pass
            return
        profiles = raw.get("profiles", {})
        if isinstance(profiles, dict):
            for name, values in profiles.items():
                if isinstance(name, str) and isinstance(values, dict):
                    self.profiles[name] = self._sanitize(values)
        last = raw.get("last_profile", "")
        self.last_profile = last if isinstance(last, str) and last in self.profiles else ""
        settings = raw.get("settings", {})
        if isinstance(settings, dict):
            for key in DEFAULT_SETTINGS:
                if isinstance(settings.get(key), bool):
                    self.settings[key] = settings[key]

    @staticmethod
    def _sanitize(values: dict[str, Any]) -> dict[str, str]:
        """Ne garde que les clés connues, en chaînes, complétées par les défauts."""
        clean = dict(DEFAULT_FORM)
        for key in DEFAULT_FORM:
            if isinstance(values.get(key), str):
                clean[key] = values[key]
        return clean

    # -- Écriture ------------------------------------------------------------ #
    def save(self) -> None:
        """Écrit le fichier de façon atomique (fichier temporaire + remplacement)."""
        data = {
            "last_profile": self.last_profile,
            "profiles": self.profiles,
            "settings": self.settings,
        }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self.path)
        except OSError:
            Path(tmp_name).unlink(missing_ok=True)
            raise

    # -- Profils ------------------------------------------------------------- #
    def names(self) -> list[str]:
        """Noms de profils triés."""
        return sorted(self.profiles, key=str.lower)

    def put(self, name: str, values: dict[str, str]) -> None:
        """Crée/écrase un profil et le marque comme dernier utilisé."""
        self.profiles[name] = self._sanitize(values)
        self.last_profile = name
        self.save()

    def delete(self, name: str) -> None:
        """Supprime un profil."""
        self.profiles.pop(name, None)
        if self.last_profile == name:
            self.last_profile = ""
        self.save()

    def set_last(self, name: str) -> None:
        """Mémorise le dernier profil chargé."""
        self.last_profile = name
        self.save()

    def set_setting(self, key: str, value: bool) -> None:
        """Modifie et persiste un paramètre booléen."""
        self.settings[key] = value
        self.save()


# --------------------------------------------------------------------------- #
# Démarrage avec Windows (HKCU\...\Run)
# --------------------------------------------------------------------------- #
def _autostart_command() -> str:
    """Ligne de commande lancée au démarrage de session."""
    if getattr(sys, "frozen", False):
        return f'"{sys.executable}" --minimized'
    pythonw = Path(sys.executable).with_name("pythonw.exe")
    interpreter = pythonw if pythonw.exists() else Path(sys.executable)
    return f'"{interpreter}" "{Path(sys.argv[0]).resolve()}" --minimized'


def is_autostart_enabled() -> bool:
    """True si la clé Run existe pour cette application."""
    if sys.platform != "win32":
        return False
    import winreg

    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_READ) as key:
            winreg.QueryValueEx(key, APP_NAME)
            return True
    except FileNotFoundError:
        return False
    except OSError:
        return False


def set_autostart(enabled: bool) -> None:
    """Active/désactive le démarrage avec Windows. Lève ``OSError`` en cas d'échec."""
    if sys.platform != "win32":
        raise OSError("Le démarrage automatique n'est disponible que sous Windows.")
    import winreg

    with winreg.CreateKey(winreg.HKEY_CURRENT_USER, RUN_KEY) as key:
        if enabled:
            winreg.SetValueEx(key, APP_NAME, 0, winreg.REG_SZ, _autostart_command())
        else:
            try:
                winreg.DeleteValue(key, APP_NAME)
            except FileNotFoundError:
                pass
