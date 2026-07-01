"""
ParameterPanel — dockable editor for SimulationParameters.

Displays all numeric fields of every physiological sub-parameter dataclass
as labelled spin-boxes grouped by subsystem.  Changes are emitted
immediately via ``parameters_changed`` so the simulation engine can be
updated without stopping.
"""

from __future__ import annotations

import logging
from dataclasses import fields

from PyQt6.QtCore import pyqtSignal
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDockWidget,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QLabel,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from cardiac_sim.core.parameter_model import SimulationParameters

logger = logging.getLogger(__name__)

# Map group display names → attribute name on SimulationParameters
_GROUP_ATTRS: dict[str, str] = {
    "SA Node": "sa_node",
    "AV Node": "av_node",
    "His Bundle": "his_bundle",
    "Purkinje": "purkinje",
    "Ventricle": "ventricle",
    "Atrium": "atrium",
    "HRV": "hrv",
    "Escape Pacemaker": "escape_pacemaker",
}


class ParameterPanel(QDockWidget):
    """
    Dockable parameter editor.

    Emits :attr:`parameters_changed` (carrying a deep-copied
    :class:`~cardiac_sim.core.parameter_model.SimulationParameters`) whenever
    the user changes a value.

    Parameters
    ----------
    params:
        Initial parameter set to display.
    parent:
        Optional Qt parent widget.
    """

    parameters_changed: pyqtSignal = pyqtSignal(object)   # SimulationParameters

    def __init__(
        self,
        params: SimulationParameters,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__("Parameters", parent)
        self._params = params.copy()
        # Maps (group_display_name, field_name) → widget (QDoubleSpinBox, QCheckBox, QComboBox)
        self._widgets: dict[tuple[str, str], QWidget] = {}
        self._setup_ui()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        container = QWidget()
        outer = QVBoxLayout(container)
        outer.setContentsMargins(4, 4, 4, 4)
        outer.setSpacing(6)

        for display_name, attr in _GROUP_ATTRS.items():
            sub = getattr(self._params, attr)
            group = QGroupBox(display_name)
            form = QFormLayout(group)
            form.setContentsMargins(6, 6, 6, 6)
            form.setSpacing(4)

            for f in fields(sub):
                value = getattr(sub, f.name)
                label_text = f.name.replace("_", " ").title()
                key = (display_name, f.name)

                # Boolean fields → QCheckBox
                if isinstance(value, bool):
                    checkbox = QCheckBox()
                    checkbox.setChecked(bool(value))
                    checkbox.stateChanged.connect(
                        lambda state, k=key: self._on_checkbox_changed(k, state)
                    )
                    self._widgets[key] = checkbox
                    form.addRow(QLabel(label_text), checkbox)

                # String fields → QComboBox (special case for 'origin')
                elif isinstance(value, str):
                    if f.name == "origin":
                        combo = QComboBox()
                        combo.addItems(["HIS", "LV_LAT"])
                        combo.setCurrentText(value)
                        combo.currentTextChanged.connect(
                            lambda text, k=key: self._on_combo_changed(k, text)
                        )
                        self._widgets[key] = combo
                        form.addRow(QLabel(label_text), combo)
                    # Other strings skipped for now
                    continue

                # Numeric fields → QDoubleSpinBox
                elif isinstance(value, (int, float)):
                    spin = QDoubleSpinBox()
                    spin.setRange(-1_000_000.0, 1_000_000.0)
                    spin.setDecimals(3)
                    spin.setSingleStep(0.1)
                    spin.setValue(float(value))
                    spin.setSizePolicy(
                        QSizePolicy.Policy.Expanding,
                        QSizePolicy.Policy.Fixed,
                    )
                    self._widgets[key] = spin
                    spin.valueChanged.connect(
                        lambda v, k=key: self._on_value_changed(k, v)
                    )
                    form.addRow(QLabel(label_text), spin)

            outer.addWidget(group)

        outer.addStretch()
        scroll.setWidget(container)
        self.setWidget(scroll)

    # ------------------------------------------------------------------
    # Slots / callbacks
    # ------------------------------------------------------------------

    def _on_checkbox_changed(self, key: tuple[str, str], state: int) -> None:
        """Handle boolean field changes from checkboxes."""
        display_name, field_name = key
        attr = _GROUP_ATTRS.get(display_name)
        if attr is None:
            return
        sub = getattr(self._params, attr)
        if hasattr(sub, field_name):
            setattr(sub, field_name, bool(state))
            self.parameters_changed.emit(self._params.copy())

    def _on_combo_changed(self, key: tuple[str, str], text: str) -> None:
        """Handle string field changes from combo boxes."""
        display_name, field_name = key
        attr = _GROUP_ATTRS.get(display_name)
        if attr is None:
            return
        sub = getattr(self._params, attr)
        if hasattr(sub, field_name):
            setattr(sub, field_name, text)
            self.parameters_changed.emit(self._params.copy())

    def _on_value_changed(self, key: tuple[str, str], value: float) -> None:
        display_name, field_name = key
        attr = _GROUP_ATTRS.get(display_name)
        if attr is None:
            return
        sub = getattr(self._params, attr)
        if hasattr(sub, field_name):
            setattr(sub, field_name, value)
            self.parameters_changed.emit(self._params.copy())

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def set_parameters(self, params: SimulationParameters) -> None:
        """
        Synchronise the panel's widgets with *params* without emitting signals.

        Called on reset to keep the UI consistent with the engine state.
        """
        self._params = params.copy()
        for (display_name, field_name), widget in self._widgets.items():
            attr = _GROUP_ATTRS.get(display_name)
            if attr is None:
                continue
            sub = getattr(self._params, attr)
            if hasattr(sub, field_name):
                value = getattr(sub, field_name)
                widget.blockSignals(True)

                if isinstance(widget, QCheckBox):
                    widget.setChecked(bool(value))
                elif isinstance(widget, QComboBox):
                    widget.setCurrentText(str(value))
                elif isinstance(widget, QDoubleSpinBox):
                    widget.setValue(float(value))

                widget.blockSignals(False)
