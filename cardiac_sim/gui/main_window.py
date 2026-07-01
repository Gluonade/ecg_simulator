"""
MainWindow — top-level application window.

Owns the ECG display, the two dock panels, the toolbar, and the simulation
worker.  Wires all signal/slot connections between the GUI layer and the
simulation back-end.

Design principles
-----------------
* The window never calls engine methods directly — it goes through the
  :class:`~cardiac_sim.core.simulation_worker.SimulationWorker` or
  through thread-safe engine methods.
* Configuration is persisted to ``~/.cardiac_sim/config.json`` on every
  parameter change and on close.
* The concrete engine (:class:`~cardiac_sim.simulation.engine.NullSimulationEngine`)
  is injected here.  In a later phase this becomes a factory/setting choice.
"""

from __future__ import annotations

import logging
from pathlib import Path

from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QAction, QKeySequence
from PyQt6.QtWidgets import (
    QComboBox,
    QLabel,
    QMainWindow,
    QMessageBox,
    QSpinBox,
    QToolBar,
    QWidget,
)

from cardiac_sim.core.interfaces import AbstractPathologyPlugin
from cardiac_sim.core.parameter_model import SimulationParameters
from cardiac_sim.core.simulation_worker import SimulationWorker
from cardiac_sim.gui.ecg_display import ECGDisplay
from cardiac_sim.gui.parameter_panel import ParameterPanel
from cardiac_sim.gui.pathology_panel import PathologyPanel
from cardiac_sim.simulation.graph_engine import ConductionGraphEngine
from cardiac_sim.simulation.cable_engine import ConductionCableEngine

logger = logging.getLogger(__name__)

_CONFIG_PATH = Path.home() / ".cardiac_sim" / "config.json"


class MainWindow(QMainWindow):
    """Main application window."""

    def __init__(self) -> None:
        super().__init__()

        self._params = SimulationParameters()
        self._load_config()

        # Engine + worker
        self._engine = ConductionGraphEngine()
        self._worker = SimulationWorker(self._engine)

        self._setup_ui()
        self._connect_signals()

        self.setWindowTitle("ECG Simulator")
        self.resize(1400, 900)
        self.setMinimumSize(900, 600)

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _setup_ui(self) -> None:
        # Central widget: scrolling ECG display
        self._ecg_display = ECGDisplay(
            sample_rate=self._params.solver.sample_rate_hz,
            window_sec=self._params.solver.display_window_sec,
        )
        self.setCentralWidget(self._ecg_display)

        # Dockable panels (right side)
        self._param_panel = ParameterPanel(self._params)
        self._path_panel = PathologyPanel()

        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._param_panel)
        self.addDockWidget(Qt.DockWidgetArea.RightDockWidgetArea, self._path_panel)
        self.tabifyDockWidget(self._param_panel, self._path_panel)
        self._param_panel.raise_()

        # Toolbar
        self._build_toolbar()

        # Status bar
        self._status_label = QLabel("  Ready — press ▶ Start or Space to begin.")
        self.statusBar().addWidget(self._status_label)

    def _build_toolbar(self) -> None:
        toolbar = QToolBar("Simulation Controls")
        toolbar.setMovable(False)
        toolbar.setIconSize(QSize(20, 20))
        self.addToolBar(toolbar)

        self._act_start = QAction("▶  Start", self)
        self._act_start.setShortcut(QKeySequence(Qt.Key.Key_Space))
        self._act_start.setToolTip("Start simulation  [Space]")

        self._act_stop = QAction("■  Stop", self)
        self._act_stop.setShortcut(QKeySequence(Qt.Key.Key_Escape))
        self._act_stop.setToolTip("Stop simulation  [Esc]")
        self._act_stop.setEnabled(False)

        self._act_reset = QAction("↺  Reset", self)
        self._act_reset.setShortcut(QKeySequence("Ctrl+R"))
        self._act_reset.setToolTip("Reset simulation  [Ctrl+R]")

        toolbar.addAction(self._act_start)
        toolbar.addAction(self._act_stop)
        toolbar.addSeparator()
        toolbar.addAction(self._act_reset)
        toolbar.addSeparator()

        # ── Heart Rate control ─────────────────────────────────────────
        toolbar.addWidget(QLabel("  HR: "))
        self._hr_spin = QSpinBox()
        self._hr_spin.setRange(30, 220)
        self._hr_spin.setSuffix(" bpm")
        self._hr_spin.setValue(70)
        self._hr_spin.setToolTip(
            "Base heart rate (overridden by active pathology plugins)"
        )
        self._hr_spin.valueChanged.connect(self._on_hr_spin_changed)
        toolbar.addWidget(self._hr_spin)

        toolbar.addSeparator()

        # ── Engine selector ───────────────────────────────────────────
        toolbar.addWidget(QLabel("  Engine: "))
        self._engine_combo = QComboBox()
        self._engine_combo.addItems([
            "Phase 1/2  – Conduction Graph",
            "Phase 3     – FHN Cable",
        ])
        self._engine_combo.setToolTip(
            "Phase 1/2: discrete graph engine (fast, all pathologies).\n"
            "Phase 3: 1-D FHN cable engine (emergent refractory, 2× real-time)."
        )
        self._engine_combo.currentIndexChanged.connect(self._on_engine_changed)
        toolbar.addWidget(self._engine_combo)

        toolbar.addSeparator()

        # Engine label (updated when engine switches)
        self._engine_label = QLabel("  Phase 1/2 – Conduction Graph  ")
        toolbar.addWidget(self._engine_label)

    # ------------------------------------------------------------------
    # Signal / slot wiring
    # ------------------------------------------------------------------

    def _connect_signals(self) -> None:
        self._act_start.triggered.connect(self._on_start)
        self._act_stop.triggered.connect(self._on_stop)
        self._act_reset.triggered.connect(self._on_reset)

        # Worker → GUI  (cross-thread; Qt queues these automatically)
        self._worker.ecg_data_ready.connect(self._ecg_display.update_ecg_data)
        self._worker.state_changed.connect(self._on_state_changed)
        self._worker.error_occurred.connect(self._on_worker_error)
        self._worker.finished.connect(self._on_worker_finished)

        # Parameter panel → engine
        self._param_panel.parameters_changed.connect(self._on_parameters_changed)

        # Pathology panel → engine
        self._path_panel.pathology_applied.connect(self._on_pathology_applied)
        self._path_panel.pathology_removed.connect(self._on_pathology_removed)

    # ------------------------------------------------------------------
    # Simulation control
    # ------------------------------------------------------------------

    def _on_start(self) -> None:
        if self._worker.isRunning():
            return
        self._worker.configure(self._params)
        self._worker.start()
        self._act_start.setEnabled(False)
        self._act_stop.setEnabled(True)
        self._status_label.setText("  Simulation running…")
        logger.info("Simulation started.")

    def _on_stop(self) -> None:
        self._worker.stop_simulation()
        self._act_stop.setEnabled(False)
        self._status_label.setText("  Stopping…")
        logger.info("Stop requested.")

    def _on_reset(self) -> None:
        if self._worker.isRunning():
            self._worker.stop_simulation()
            self._worker.wait(2_000)
        self._ecg_display.clear()
        self._params = SimulationParameters()
        self._param_panel.set_parameters(self._params)
        self._engine.initialize(self._params)
        # Bug 2 fix: sync HR spinner to the reset default value.
        # _on_reset creates fresh params (70 bpm default) but never updated
        # the toolbar spinner, leaving a stale value from before the reset.
        default_bpm = round(60_000.0 / max(1.0, self._params.sa_node.cycle_length_ms))
        self._hr_spin.blockSignals(True)
        self._hr_spin.setValue(max(30, min(220, default_bpm)))
        self._hr_spin.blockSignals(False)
        self._act_start.setEnabled(True)
        self._act_stop.setEnabled(False)
        self._status_label.setText(
            f"  Reset — ready  (default HR: {default_bpm} bpm)."
        )
        logger.info("Simulation reset.")

    # ------------------------------------------------------------------
    # Slots
    # ------------------------------------------------------------------

    def _on_state_changed(self, state) -> None:
        if state.is_running:
            sa_hr = state.heart_rate
            vent_hr = state.ventricular_rate
            axis_class = state.cardiac_axis_classification
            axis_deg = state.cardiac_axis_degrees
            t = state.time
            sa_text = f"{sa_hr:.0f} bpm" if sa_hr > 0 else "—"
            vent_text = f"{vent_hr:.0f} bpm" if vent_hr > 0 else "—"
            self._status_label.setText(
                f"  SA: {sa_text}  |  QRS: {vent_text}  |  "
                f"Axis: {axis_deg:.0f}° ({axis_class})  |  t = {t:.1f} s"
            )
        else:
            self._status_label.setText("  Stopped.")

    def _on_worker_finished(self) -> None:
        self._act_start.setEnabled(True)
        self._act_stop.setEnabled(False)

    def _on_worker_error(self, message: str) -> None:
        QMessageBox.critical(self, "Simulation Error", message)
        self._act_start.setEnabled(True)
        self._act_stop.setEnabled(False)
        self._status_label.setText("  Error — see dialog.")
        logger.error("Worker error: %s", message)

    def _on_parameters_changed(self, params: SimulationParameters) -> None:
        self._params = params
        # Update base params WITHOUT clearing active plugins or resetting state
        if self._worker.isRunning():
            self._engine.update_base_parameters(params)
        # Sync HR spinner without triggering valueChanged loop
        bpm = round(60_000.0 / max(1.0, params.sa_node.cycle_length_ms))
        self._hr_spin.blockSignals(True)
        self._hr_spin.setValue(max(30, min(220, bpm)))
        self._hr_spin.blockSignals(False)
        self._save_config()

    def _on_hr_spin_changed(self, bpm: int) -> None:
        """Direct heart rate control via toolbar spinner."""
        if bpm <= 0:
            return
        self._params.sa_node.cycle_length_ms = 60_000.0 / bpm
        if self._worker.isRunning():
            self._engine.update_base_parameters(self._params)
        self._param_panel.set_parameters(self._params)
        self._save_config()

    def _on_pathology_applied(self, plugin: AbstractPathologyPlugin) -> None:
        self._engine.apply_pathology(plugin)
        logger.info("Pathology forwarded to engine: %s", plugin.get_display_name())

    def _on_pathology_removed(self, name: str) -> None:
        self._engine.remove_pathology(name)

    def _on_engine_changed(self, index: int) -> None:
        """
        Switch between the Phase 1/2 graph engine and the Phase 3 cable engine.

        Stops the worker if running, swaps the engine, clears the display,
        and restarts automatically if the simulation was active.
        """
        was_running = self._worker.isRunning()
        if was_running:
            self._worker.stop_simulation()
            self._worker.wait(2_000)

        if index == 0:
            new_engine = ConductionGraphEngine()
            label = "Phase 1/2 – Conduction Graph"
        else:
            new_engine = ConductionCableEngine()
            label = "Phase 3 – FHN Cable (2× real-time)"

        self._engine = new_engine
        self._worker.set_engine(new_engine, self._params)
        self._engine_label.setText(f"  {label}  ")
        self._ecg_display.clear()
        self._status_label.setText(f"  Engine: {label} — ready.")
        logger.info("Engine switched to %s.", label)

        if was_running:
            self._on_start()

    # ------------------------------------------------------------------
    # Config persistence
    # ------------------------------------------------------------------

    def _load_config(self) -> None:
        if _CONFIG_PATH.exists():
            try:
                self._params = SimulationParameters.load(_CONFIG_PATH)
                logger.info("Config loaded from %s", _CONFIG_PATH)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Config load failed (%s) — using defaults.", exc)

    def _save_config(self) -> None:
        try:
            _CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
            self._params.save(_CONFIG_PATH)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Config save failed: %s", exc)

    # ------------------------------------------------------------------
    # Close event
    # ------------------------------------------------------------------

    def closeEvent(self, event) -> None:
        self._save_config()
        if self._worker.isRunning():
            self._worker.stop_simulation()
            self._worker.wait(3_000)
        super().closeEvent(event)
