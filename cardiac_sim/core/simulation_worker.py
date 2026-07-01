"""
SimulationWorker — QThread wrapper around a SimulationEngine.

The worker drives the simulation loop in a background thread and emits
batched ECG data to the GUI via Qt signals.  The GUI thread is never
blocked by simulation computation.

Threading model
---------------
* The main (GUI) thread owns the :class:`~PyQt6.QtWidgets.QMainWindow` and
  all Qt widgets.
* :class:`SimulationWorker` runs in a child ``QThread``.
* Communication from worker → GUI uses Qt signals (thread-safe by design).
* Communication from GUI → worker uses thread-safe methods on the engine
  (the engine guards its state with a ``threading.Lock``).

Performance
-----------
Steps are computed in batches of :data:`_BATCH_SIZE`.  After each batch the
worker emits the accumulated data and sleeps for the remaining wall-clock
time to maintain approximate real-time pacing.  This gives the GUI 10
updates per second at the default 500 Hz / 50-sample batch configuration,
which is more than sufficient for smooth scrolling.
"""

from __future__ import annotations

import logging
import time

import numpy as np
from PyQt6.QtCore import QThread, pyqtSignal

from cardiac_sim.core.interfaces import SimulationEngine, SimulationState
from cardiac_sim.core.parameter_model import SimulationParameters

logger = logging.getLogger(__name__)

#: Number of simulation steps computed per emission to the GUI.
#: 50 steps × (1/500 s/step) = 0.1 s of simulated time per GUI update.
_BATCH_SIZE: int = 50


class SimulationWorker(QThread):
    """
    Background thread that drives a :class:`~cardiac_sim.core.interfaces.SimulationEngine`
    and forwards results to the GUI.

    Signals
    -------
    ecg_data_ready
        Emitted after each completed batch.
        Payload: ``np.ndarray`` of shape ``(batch_size, 12)`` — ECG voltages [mV].
    state_changed
        Emitted when the simulation state changes (start, stop, HR update).
        Payload: :class:`~cardiac_sim.core.interfaces.SimulationState`.
    error_occurred
        Emitted if an unhandled exception terminates the worker loop.
        Payload: ``str`` — exception message.
    """

    ecg_data_ready: pyqtSignal = pyqtSignal(object)   # np.ndarray [batch × 12]
    state_changed: pyqtSignal = pyqtSignal(object)    # SimulationState
    error_occurred: pyqtSignal = pyqtSignal(str)

    def __init__(self, engine: SimulationEngine, parent=None) -> None:
        super().__init__(parent)
        self._engine = engine
        self._params = SimulationParameters()
        self._running = False

    # ------------------------------------------------------------------
    # Public API (called from the GUI thread before/after run())
    # ------------------------------------------------------------------

    def configure(self, params: SimulationParameters) -> None:
        """Configure the underlying engine.  Call this before :meth:`start`."""
        self._params = params
        self._engine.initialize(params)

    def stop_simulation(self) -> None:
        """Request the simulation loop to stop gracefully."""
        self._running = False

    def set_engine(self, engine: SimulationEngine, params: SimulationParameters) -> None:
        """
        Replace the underlying engine.

        Must be called while the worker is **not** running.  The new engine
        is initialised with *params* immediately so it is ready when
        :meth:`start` is next called.
        """
        self._engine = engine
        self._params = params
        self._engine.initialize(params)

    # ------------------------------------------------------------------
    # QThread entry point
    # ------------------------------------------------------------------

    def run(self) -> None:
        """Simulation loop.  Executes in the worker thread."""
        logger.info("SimulationWorker started.")
        self._running = True

        dt = self._params.solver.dt_fast
        batch = np.zeros((_BATCH_SIZE, 12), dtype=np.float32)

        self.state_changed.emit(self._engine.get_state())

        try:
            batch_n = 0
            while self._running:
                t_wall_start = time.perf_counter()

                for i in range(_BATCH_SIZE):
                    sample = self._engine.step(dt)
                    batch[i] = sample.leads

                self.ecg_data_ready.emit(batch.copy())

                # Emit HR / time update every 10 batches (~1 s real time)
                batch_n += 1
                if batch_n % 2 == 0:   # every 2 batches ≈ 200 ms — responsive HR display
                    self.state_changed.emit(self._engine.get_state())

                # Real-time pacing: sleep for any remaining wall-clock time
                # so that the simulation doesn't race ahead.
                elapsed = time.perf_counter() - t_wall_start
                target = _BATCH_SIZE * dt
                remaining = target - elapsed
                if remaining > 0:
                    time.sleep(remaining)

        except Exception as exc:  # noqa: BLE001
            logger.exception("SimulationWorker: unhandled exception — %s", exc)
            self.error_occurred.emit(str(exc))

        finally:
            self._running = False
            state = self._engine.get_state()
            self.state_changed.emit(
                SimulationState(time=state.time, heart_rate=state.heart_rate, is_running=False)
            )
            logger.info("SimulationWorker stopped.")
