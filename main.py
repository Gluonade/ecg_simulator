"""
ECG Simulator — application entry point.

Usage
-----
    python main.py

Or, if using the project virtual environment directly:

    .venv/bin/python main.py
"""

from __future__ import annotations

import logging
import sys

from PyQt6.QtWidgets import QApplication

from cardiac_sim.gui.main_window import MainWindow


def _configure_logging() -> None:
    logging.basicConfig(
        level=logging.DEBUG,
        format="%(asctime)s [%(levelname)-8s] %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )


def main() -> int:
    _configure_logging()

    app = QApplication(sys.argv)
    app.setApplicationName("ECG Simulator")
    app.setOrganizationName("cardiac_sim")

    window = MainWindow()
    window.show()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
