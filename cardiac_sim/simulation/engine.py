"""
Simulation engines.

Phase 0 — :class:`NullSimulationEngine`
    Produces a flat-line ECG (all leads = 0 mV).  Used to validate the
    GUI / threading pipeline before any electrophysiology is implemented.

Later phases will add:
    * ``ConductionGraphEngine``  (Phase 1 — discrete activation graph)
    * ``ODEPropagationEngine``   (Phase 3 — FitzHugh-Nagumo / ten Tusscher)
"""

from __future__ import annotations

import logging
import threading

import numpy as np

from cardiac_sim.core.interfaces import (
    AbstractPathologyPlugin,
    ECGSample,
    SimulationEngine,
    SimulationState,
)
from cardiac_sim.core.parameter_model import SimulationParameters

logger = logging.getLogger(__name__)


class NullSimulationEngine(SimulationEngine):
    """
    Phase 0 placeholder engine.

    Produces a perfectly flat-line ECG on all 12 leads.  Its only purpose
    is to prove that the GUI display, threading model, and parameter pipeline
    work correctly end-to-end before any physiology is implemented.

    Thread safety
    -------------
    All mutable state is guarded by a :class:`threading.Lock`.  The worker
    thread calls :meth:`step` in a tight loop; the GUI thread may call
    :meth:`apply_pathology` or :meth:`initialize` concurrently.
    """

    def __init__(self) -> None:
        self._params = SimulationParameters()
        self._time: float = 0.0
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # SimulationEngine interface
    # ------------------------------------------------------------------

    def initialize(self, params: SimulationParameters) -> None:
        with self._lock:
            self._params = params.copy()
            self._time = 0.0
        logger.info("NullSimulationEngine initialised.")

    def step(self, dt: float) -> ECGSample:
        with self._lock:
            self._time += dt
            t = self._time
        return ECGSample(
            timestamp=t,
            leads=np.zeros(12, dtype=np.float32),
        )

    def get_state(self) -> SimulationState:
        with self._lock:
            return SimulationState(
                time=self._time,
                heart_rate=0.0,
                is_running=True,
            )

    def apply_pathology(self, plugin: AbstractPathologyPlugin) -> None:
        with self._lock:
            self._params = plugin.apply(self._params)
        logger.info("Pathology applied: %s", plugin.get_display_name())

    def remove_pathology(self, plugin_name: str) -> None:
        logger.info(
            "remove_pathology('%s') called on NullSimulationEngine — no-op.",
            plugin_name,
        )
