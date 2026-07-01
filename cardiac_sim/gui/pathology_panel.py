"""
PathologyPanel — dockable plugin selector.

Discovers all :class:`~cardiac_sim.core.interfaces.AbstractPathologyPlugin`
subclasses from the ``cardiac_sim.plugins`` package at runtime (no
hard-coded registration list) and provides a simple two-list UI:

* **Available** — all discovered plugins.
* **Active** — plugins currently applied to the simulation engine.

Emits :attr:`pathology_applied` and :attr:`pathology_removed` signals
which :class:`~cardiac_sim.gui.main_window.MainWindow` forwards to the
simulation engine.
"""

from __future__ import annotations

import importlib
import inspect
import logging
import pkgutil
from pathlib import Path

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QDockWidget,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from cardiac_sim.core.interfaces import AbstractPathologyPlugin

logger = logging.getLogger(__name__)


class PathologyPanel(QDockWidget):
    """
    Dockable pathology plugin selector.

    Signals
    -------
    pathology_applied
        Emitted when the user activates a plugin.
        Payload: the :class:`~cardiac_sim.core.interfaces.AbstractPathologyPlugin` instance.
    pathology_removed
        Emitted when the user deactivates a plugin.
        Payload: ``str`` — the plugin's display name.
    """

    pathology_applied: pyqtSignal = pyqtSignal(object)   # AbstractPathologyPlugin
    pathology_removed: pyqtSignal = pyqtSignal(str)       # display name

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__("Pathologies", parent)
        self._available: dict[str, AbstractPathologyPlugin] = {}
        self._active: dict[str, AbstractPathologyPlugin] = {}
        self._setup_ui()
        self._discover_plugins()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        container = QWidget()
        layout = QVBoxLayout(container)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(4)

        layout.addWidget(QLabel("Available:"))
        self._available_list = QListWidget()
        self._available_list.setToolTip("Select a pathology, then click Apply.")
        layout.addWidget(self._available_list)

        self._apply_btn = QPushButton("Apply →")
        self._apply_btn.setEnabled(False)
        self._apply_btn.clicked.connect(self._on_apply)
        layout.addWidget(self._apply_btn)

        layout.addWidget(QLabel("Active:"))
        self._active_list = QListWidget()
        layout.addWidget(self._active_list)

        self._remove_btn = QPushButton("← Remove")
        self._remove_btn.setEnabled(False)
        self._remove_btn.clicked.connect(self._on_remove)
        layout.addWidget(self._remove_btn)

        self._available_list.itemSelectionChanged.connect(
            lambda: self._apply_btn.setEnabled(
                bool(self._available_list.selectedItems())
            )
        )
        self._active_list.itemSelectionChanged.connect(
            lambda: self._remove_btn.setEnabled(
                bool(self._active_list.selectedItems())
            )
        )

        self.setWidget(container)

    # ------------------------------------------------------------------
    # Plugin discovery
    # ------------------------------------------------------------------

    def _discover_plugins(self) -> None:
        """
        Scan ``cardiac_sim.plugins`` for :class:`AbstractPathologyPlugin` subclasses.

        Each module in the package is imported; all concrete subclasses are
        instantiated and registered.  Import errors for individual modules
        are logged as warnings and do not abort discovery.
        """
        try:
            import cardiac_sim.plugins as plugin_pkg

            pkg_path = Path(plugin_pkg.__file__).parent
            for _finder, mod_name, _is_pkg in pkgutil.iter_modules([str(pkg_path)]):
                full_name = f"cardiac_sim.plugins.{mod_name}"
                try:
                    module = importlib.import_module(full_name)
                    for _name, cls in inspect.getmembers(module, inspect.isclass):
                        if (
                            issubclass(cls, AbstractPathologyPlugin)
                            and cls is not AbstractPathologyPlugin
                            and not inspect.isabstract(cls)
                        ):
                            plugin: AbstractPathologyPlugin = cls()
                            name = plugin.get_display_name()
                            if name not in self._available:
                                self._available[name] = plugin
                                item = QListWidgetItem(name)
                                item.setToolTip(plugin.get_description())
                                self._available_list.addItem(item)
                                logger.info("Plugin discovered: %s", name)
                except Exception as exc:  # noqa: BLE001
                    logger.warning("Failed to load plugin module %s: %s", full_name, exc)

        except Exception as exc:  # noqa: BLE001
            logger.warning("Plugin discovery failed: %s", exc)

        if not self._available:
            placeholder = QListWidgetItem("(No plugins installed yet)")
            placeholder.setFlags(placeholder.flags() & ~placeholder.flags())  # non-selectable
            self._available_list.addItem(placeholder)
            logger.info("No pathology plugins found in cardiac_sim.plugins.")

    # ------------------------------------------------------------------
    # Button handlers
    # ------------------------------------------------------------------

    def _on_apply(self) -> None:
        selected = self._available_list.currentItem()
        if selected is None:
            return
        name = selected.text()
        plugin = self._available.get(name)
        if plugin is None or name in self._active:
            return
        self._active[name] = plugin
        self._active_list.addItem(QListWidgetItem(name))
        self.pathology_applied.emit(plugin)
        logger.info("Pathology applied: %s", name)

    def _on_remove(self) -> None:
        selected = self._active_list.currentItem()
        if selected is None:
            return
        name = selected.text()
        if name not in self._active:
            return
        del self._active[name]
        row = self._active_list.row(selected)
        self._active_list.takeItem(row)
        self.pathology_removed.emit(name)
        logger.info("Pathology removed: %s", name)
