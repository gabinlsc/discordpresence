"""Point d'entrée : logs rotatifs, arguments, lancement de l'UI."""
from __future__ import annotations

import argparse
import logging
import sys
from logging.handlers import RotatingFileHandler
from types import TracebackType

from storage import ProfileStore, app_dir


def setup_logging() -> None:
    """Logs rotatifs dans %APPDATA%/<app>/app.log (3 × 512 Ko)."""
    handler = RotatingFileHandler(app_dir() / "app.log", maxBytes=512 * 1024, backupCount=3, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    root.addHandler(handler)

    def excepthook(exc: type[BaseException], val: BaseException, tb: TracebackType | None) -> None:
        logging.getLogger("main").critical("Exception non gérée", exc_info=(exc, val, tb))

    sys.excepthook = excepthook


def main() -> None:
    parser = argparse.ArgumentParser(description="Discord Presence Studio")
    parser.add_argument("--minimized", action="store_true", help="démarrer réduit (utilisé au démarrage de Windows)")
    args = parser.parse_args()

    setup_logging()
    logging.getLogger("main").info("Démarrage (minimized=%s)", args.minimized)

    from ui import App  # import tardif : le logging est déjà configuré

    App(ProfileStore(), start_minimized=args.minimized).mainloop()


if __name__ == "__main__":
    main()
