"""
Core abstract interfaces for the ECG Simulator.

All concrete components must implement these interfaces.
No module may depend on a concrete implementation — only on the interfaces
defined here. This enforces strict OOP design and makes every layer
independently testable and swappable.
"""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from cardiac_sim.core.parameter_model import SimulationParameters

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Data transfer objects
# ---------------------------------------------------------------------------

#: Standard 12-lead names in clinical order.
LEAD_NAMES: tuple[str, ...] = (
    "I", "II", "III", "aVR", "aVL", "aVF",
    "V1", "V2", "V3", "V4", "V5", "V6",
)


@dataclass
class ECGSample:
    """One time-point of 12-lead ECG data emitted by the simulation engine."""

    timestamp: float
    """Simulation time in seconds."""

    leads: np.ndarray
    """Shape (12,) — voltage at each standard lead [mV]."""


@dataclass
class SimulationState:
    """Snapshot of the simulation's observable state, safe to pass across threads."""

    time: float = 0.0
    """Current simulation time [s]."""

    heart_rate: float = 60.0
    """SA node / atrial rate [bpm]. 0.0 when there is no organised atrial
    rhythm (e.g. atrial fibrillation) — see :attr:`atrial_rhythm`."""

    atrial_rhythm: str = "Sinus"
    """Atrial rhythm label, independent of the ventricular rate.
    'Sinus' for organised P waves, 'Fibrillation' for AF (no discrete atrial
    rate). Lets the UI distinguish "no atrial rate" from a real 0 bpm and
    avoids the atrial readout collapsing onto the ventricular rate."""

    ventricular_rate: float = 60.0
    """Actual ventricular rate based on QRS complexes [bpm]."""

    cardiac_axis_degrees: float = 0.0
    """Cardiac electrical axis in degrees (-180 to +180). 0 before first analysis."""

    cardiac_axis_classification: str = "Undetermined"
    """Lagetyp classification (e.g., 'Indifferenztyp', 'Linkstyp', etc.)."""

    pr_interval_ms: float | None = None
    """PR interval in milliseconds. None if not measurable."""

    qrs_duration_ms: float | None = None
    """QRS duration in milliseconds. None if not measurable."""

    qt_interval_ms: float | None = None
    """QT interval in milliseconds. None if not measurable."""

    is_running: bool = False


# ---------------------------------------------------------------------------
# Cell-model interface (Phase 3+)
# ---------------------------------------------------------------------------

class AbstractCellModel(ABC):
    """
    Interface for single-cell electrophysiology models.

    Concrete subclasses (FitzHugh-Nagumo, ten Tusscher, etc.) implement
    the ODE right-hand side for one cell type. The solver drives them via
    :meth:`compute_derivatives`.
    """

    @abstractmethod
    def get_initial_state(self) -> np.ndarray:
        """Return the initial state vector (membrane voltage + gating vars)."""

    @abstractmethod
    def compute_derivatives(self, t: float, state: np.ndarray) -> np.ndarray:
        """
        Compute ``dState/dt`` at time *t* for the given *state* vector.

        Parameters
        ----------
        t:
            Current simulation time [s].
        state:
            State vector (length matches :meth:`get_initial_state`).

        Returns
        -------
        np.ndarray
            Derivative vector of the same shape as *state*.
        """

    @abstractmethod
    def get_state_variable_names(self) -> tuple[str, ...]:
        """Return names of all state variables (for debugging / display)."""


# ---------------------------------------------------------------------------
# Solver interface (Phase 3+)
# ---------------------------------------------------------------------------

class AbstractSolver(ABC):
    """Interface for numerical ODE solvers."""

    @abstractmethod
    def step(
        self,
        model: AbstractCellModel,
        state: np.ndarray,
        t: float,
        dt: float,
    ) -> np.ndarray:
        """
        Advance *state* by one time step *dt*.

        Parameters
        ----------
        model:
            The cell model providing :meth:`~AbstractCellModel.compute_derivatives`.
        state:
            Current state vector.
        t:
            Current time [s].
        dt:
            Time step [s].

        Returns
        -------
        np.ndarray
            New state vector at ``t + dt``.
        """


# ---------------------------------------------------------------------------
# ECG forward-model interface
# ---------------------------------------------------------------------------

class AbstractECGForwardModel(ABC):
    """
    Interface for ECG forward models.

    Maps spatial activation vectors (dipoles) from cardiac zones to
    body-surface voltages at the standard 12 electrode positions.
    """

    @abstractmethod
    def compute_leads(self, activation_vectors: np.ndarray) -> np.ndarray:
        """
        Compute 12-lead ECG voltages from spatial dipole vectors.

        Parameters
        ----------
        activation_vectors:
            Shape ``(N, 3)`` — one 3-D dipole vector per active cardiac zone.

        Returns
        -------
        np.ndarray
            Shape ``(12,)`` — voltage at each standard lead [mV].
        """


# ---------------------------------------------------------------------------
# Pathology plugin interface
# ---------------------------------------------------------------------------

@dataclass
class PluginParameter:
    """Descriptor for one tunable parameter exposed by a pathology plugin."""

    name: str
    display_name: str
    description: str
    default_value: float
    min_value: float
    max_value: float
    unit: str = ""
    value: float = field(init=False)

    def __post_init__(self) -> None:
        self.value = self.default_value


class AbstractPathologyPlugin(ABC):
    """
    Interface for pathology plugins.

    A plugin receives the current :class:`~cardiac_sim.core.parameter_model.SimulationParameters`,
    returns a *modified copy* (never mutates in place), and exposes
    its own tunable parameters to the GUI.

    Plugins are discovered automatically from the ``cardiac_sim.plugins``
    package at runtime — no registration step is needed.
    """

    @abstractmethod
    def get_display_name(self) -> str:
        """Short human-readable name shown in the GUI list."""

    @abstractmethod
    def get_description(self) -> str:
        """One-sentence clinical description of the modelled pathology."""

    @abstractmethod
    def get_parameters(self) -> list[PluginParameter]:
        """Return the list of tunable parameters this plugin exposes."""

    @abstractmethod
    def apply(self, params: "SimulationParameters") -> "SimulationParameters":
        """
        Return a modified copy of *params* with this pathology applied.

        **Must not mutate the input object.**
        """


class AbstractStatefulPathologyPlugin(AbstractPathologyPlugin):
    """
    Pathology plugin that maintains internal state across heartbeats.

    Called by the engine **once per SA node activation** via
    :meth:`on_beat`, which may return beat-specific parameter changes
    (e.g., progressively lengthening AV delay for Wenckebach).

    The ``apply`` method should return *params* unchanged (or with any
    permanent baseline modifications); the beat-level modifications
    happen in ``on_beat`` only.
    """

    @abstractmethod
    def on_beat(
        self,
        params: "SimulationParameters",
        beat_id: int,
    ) -> "SimulationParameters":
        """
        Called just before each SA node activation.

        May return params with beat-specific modifications (e.g., a dropped
        beat for Wenckebach).  Must return a copy; must not mutate *params*.

        Parameters
        ----------
        params:
            Current parameter set after all static plugins have been applied.
        beat_id:
            Monotonically increasing beat counter (starts at 0).
        """

    def reset(self) -> None:
        """Reset internal state.  Called when the engine is reset or when
        this plugin is removed.  Default implementation is a no-op."""


# ---------------------------------------------------------------------------
# Simulation engine interface
# ---------------------------------------------------------------------------

class SimulationEngine(ABC):
    """
    Abstract simulation engine — the pure-physics core.

    The engine must **never** hold a reference to any Qt object.
    All output flows through :class:`~cardiac_sim.core.simulation_worker.SimulationWorker`
    via Qt signals. Thread safety is the responsibility of each concrete
    implementation.

    Implementations for Phase 0 through Phase 5
    ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~
    * Phase 0: :class:`~cardiac_sim.simulation.engine.NullSimulationEngine`
      (flat-line, validates the GUI/threading pipeline)
    * Phase 1: ``ConductionGraphEngine`` (discrete activation graph)
    * Phase 3+: ``ODEPropagationEngine`` (FitzHugh-Nagumo / ten Tusscher)
    """

    @abstractmethod
    def initialize(self, params: "SimulationParameters") -> None:
        """Set up or reset the engine with *params*. Called before :meth:`step`."""

    @abstractmethod
    def step(self, dt: float) -> ECGSample:
        """
        Advance the simulation by *dt* seconds.

        Must be **thread-safe** — called from the worker thread concurrently
        with :meth:`apply_pathology` from the GUI thread.

        Returns
        -------
        ECGSample
            The ECG voltages at the new time point.
        """

    @abstractmethod
    def get_state(self) -> SimulationState:
        """Return a snapshot of the current simulation state (thread-safe)."""

    @abstractmethod
    def apply_pathology(self, plugin: AbstractPathologyPlugin) -> None:
        """Apply *plugin* to the current parameter set (thread-safe)."""

    @abstractmethod
    def remove_pathology(self, plugin_name: str) -> None:
        """Remove the named pathology from the active parameter set (thread-safe)."""
