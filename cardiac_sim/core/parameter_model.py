"""
Simulation parameter model.

All physiological and numerical configuration is represented here as a
hierarchy of dataclasses.  :class:`SimulationParameters` is the single
source of truth passed to and returned from simulation engines and
pathology plugins.

Design rules
------------
* Every concrete field has a physiologically meaningful default.
* ``SimulationParameters.copy()`` returns a deep copy — plugins must use
  this and never mutate the original.
* JSON serialisation/deserialisation is built-in via ``save()`` / ``load()``.
"""

from __future__ import annotations

import copy
import json
import logging
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Numerical / infrastructure parameters
# ---------------------------------------------------------------------------

@dataclass
class SolverParameters:
    """Configuration for the numerical solver and display pipeline."""

    sample_rate_hz: float = 500.0
    """Simulation output sample rate [Hz].  Clinical ECG standard is 500 Hz."""

    display_window_sec: float = 10.0
    """Width of the scrolling ECG display window [s]."""

    simulation_mode: str = "fast"
    """``'fast'`` (Euler, large dt) or ``'accurate'`` (RK45, small dt)."""

    dt_fast: float = 1.0 / 500.0
    """ODE time step in fast mode [s] — matches output sample rate."""

    dt_accurate: float = 2e-5
    """ODE time step in accurate mode [s] = 0.02 ms; required for stiff models."""


# ---------------------------------------------------------------------------
# Physiological sub-parameters
# ---------------------------------------------------------------------------

@dataclass
class SANodeParameters:
    """Sinoatrial node electrophysiology."""

    cycle_length_ms: float = 857.0
    """RR interval at rest [ms] ≈ 70 bpm."""

    funny_current_amplitude: float = 1.0
    """Relative If (HCN-channel) amplitude [0–2]; 1 = physiological baseline."""

    autonomic_tone: float = 0.0
    """Net autonomic drive [-1 = maximal parasympathetic, +1 = maximal sympathetic]."""


@dataclass
class AVNodeParameters:
    """Atrioventricular node conduction."""

    conduction_delay_ms: float = 100.0
    """AV node intrinsic delay [ms].  Total PR interval adds atrial + AV delays.
    Default 100 ms → total PR ≈ 195 ms (normal range 120–200 ms)."""

    refractory_period_ms: float = 300.0
    """AV node effective refractory period [ms]."""

    conductance: float = 1.0
    """Relative conductance [0 = complete block (AV block III°), 1 = normal]."""


@dataclass
class HisBundleParameters:
    """His bundle and bundle branch parameters."""

    his_delay_ms: float = 20.0
    """Conduction delay through the His bundle [ms]."""

    left_branch_conductance: float = 1.0
    """Left bundle branch conductance [0 = LBBB, 1 = normal]."""

    right_branch_conductance: float = 1.0
    """Right bundle branch conductance [0 = RBBB, 1 = normal]."""

    left_anterior_conductance: float = 1.0
    """Left anterior fascicle conductance [0 = LAHB, 1 = normal]."""

    left_posterior_conductance: float = 1.0
    """Left posterior fascicle conductance [0 = LPHB, 1 = normal]."""


@dataclass
class PurkinjeParameters:
    """Purkinje fibre network parameters."""

    conduction_velocity: float = 1.0
    """Normalised conduction velocity [0–2]; 1 = physiological (~3.5 m/s)."""

    refractory_period_ms: float = 250.0
    """Purkinje fibre effective refractory period [ms]."""


@dataclass
class VentricularParameters:
    """Ventricular myocardium parameters."""

    conduction_velocity: float = 1.0
    """Normalised conduction velocity [0–2]; 1 ≈ 0.5 m/s for working myocardium."""

    apd_gradient: float = 1.0
    """Action potential duration base-to-apex gradient [0–2]; drives T-wave axis."""

    refractory_period_ms: float = 250.0
    """Ventricular effective refractory period [ms]."""


@dataclass
class AtrialParameters:
    """Atrial myocardium parameters."""

    conduction_velocity: float = 1.0
    """Normalised conduction velocity [0–2]; 1 ≈ 0.7 m/s."""

    refractory_period_ms: float = 200.0
    """Atrial effective refractory period [ms]."""

    heterogeneity: float = 0.0
    """Spatial electrophysiological heterogeneity [0–1].
    High values increase AF vulnerability by shortening refractory periods
    non-uniformly across the atrial tissue."""


@dataclass
class HRVParameters:
    """Heart rate variability parameters.

    Phase 1 placeholder: simple sinusoidal modulation of SA node cycle length.
    Replaced by physiologically coupled oscillators in Phase 6.
    """

    enabled: bool = True

    lf_amplitude_ms: float = 15.0
    """LF-band (sympathovagal) HRV amplitude [ms]."""

    hf_amplitude_ms: float = 10.0
    """HF-band (respiratory sinus arrhythmia) HRV amplitude [ms]."""

    respiratory_rate_hz: float = 0.25
    """Respiratory rate [Hz] ≈ 15 breaths/min."""


@dataclass
class BodySurfaceParameters:
    """Body-surface / forward model parameters."""

    torso_conductivity: float = 1.0
    """Normalised torso conductivity [0–2]; scales ECG amplitudes globally."""

    cardiac_axis_deg: float = 60.0
    """Mean electrical axis [degrees]; normal range 0–90°."""

    cardiac_position: str = "normal"
    """Gross cardiac orientation: ``'normal'``, ``'horizontal'``, or ``'vertical'``."""


@dataclass
class EscapePacemakerParameters:
    """
    Junctional / ventricular escape pacemaker.

    Fires when no ventricular activation has occurred within
    ``escape_interval_ms``.  Used automatically by :class:`AVBlockIII`.
    """

    enabled: bool = False
    escape_interval_ms: float = 1500.0
    """Escape interval [ms] → 40 bpm junctional escape at default."""

    origin: str = "LV_LAT"
    """Escape origin node.  ``'HIS'`` = junctional (narrow QRS, supra-Hisian
    block).  ``'LV_LAT'`` = ventricular (wide/bizarre QRS, infra-Hisian
    block, default for AVBlockIII plugin)."""


@dataclass
class AtrialFibParameters:
    """
    Atrial fibrillation pacemaker model.

    When enabled, the SA node is suppressed and replaced by:

    * Rapid, irregular QRS complexes via ``mean_rr_ms``
      (exponentially distributed inter-beat intervals).
    * A continuous f-wave baseline added to the ECG as a superposition
      of sinusoids near ``atrial_rate_hz``.
    """

    enabled: bool = False
    atrial_rate_hz: float = 5.5
    """Dominant f-wave frequency [Hz] ≈ 330 bpm."""

    mean_rr_ms: float = 700.0
    """Mean ventricular RR interval in AF [ms] ≈ 86 bpm."""

    f_wave_amplitude_mv: float = 0.08
    """Peak f-wave amplitude [mV] (typical AF: 0.05–0.15 mV)."""


@dataclass
class EctopicFocusParameters:
    """
    Single ventricular ectopic focus (Phase 2).

    When ``enabled``, the engine fires an ectopic beat from a fixed
    ventricular origin node at ``coupling_interval_ms`` after each
    preceding SA node firing.

    .. note::
        This is a simplified Phase 2 model.  Full ectopic focus support
        (multiple foci, atrial ectopics, triggered activity) is Phase 4+.
    """

    enabled: bool = False
    coupling_interval_ms: float = 600.0
    """Time from preceding SA fire to ectopic firing [ms]."""

    repetitive: bool = True
    """If True, fires every beat (bigeminy).  If False, one-shot."""


# ---------------------------------------------------------------------------
# Top-level container
# ---------------------------------------------------------------------------

@dataclass
class SimulationParameters:
    """
    Top-level parameter container — the single object passed to/from plugins and engines.

    Usage
    -----
    Always call :meth:`copy` before modifying a parameter set inside a plugin::

        def apply(self, params: SimulationParameters) -> SimulationParameters:
            p = params.copy()
            p.av_node.conductance = 0.0   # AV block III°
            return p
    """

    solver: SolverParameters = field(default_factory=SolverParameters)
    sa_node: SANodeParameters = field(default_factory=SANodeParameters)
    av_node: AVNodeParameters = field(default_factory=AVNodeParameters)
    his_bundle: HisBundleParameters = field(default_factory=HisBundleParameters)
    purkinje: PurkinjeParameters = field(default_factory=PurkinjeParameters)
    ventricle: VentricularParameters = field(default_factory=VentricularParameters)
    atrium: AtrialParameters = field(default_factory=AtrialParameters)
    hrv: HRVParameters = field(default_factory=HRVParameters)
    body_surface: BodySurfaceParameters = field(default_factory=BodySurfaceParameters)
    ectopic_focus: EctopicFocusParameters = field(default_factory=EctopicFocusParameters)
    escape_pacemaker: EscapePacemakerParameters = field(default_factory=EscapePacemakerParameters)
    atrial_fib: AtrialFibParameters = field(default_factory=AtrialFibParameters)

    def copy(self) -> SimulationParameters:
        """Return a deep copy of this parameter set."""
        return copy.deepcopy(self)

    # ------------------------------------------------------------------
    # Serialisation
    # ------------------------------------------------------------------

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SimulationParameters:
        return cls(
            solver=SolverParameters(**data.get("solver", {})),
            sa_node=SANodeParameters(**data.get("sa_node", {})),
            av_node=AVNodeParameters(**data.get("av_node", {})),
            his_bundle=HisBundleParameters(**data.get("his_bundle", {})),
            purkinje=PurkinjeParameters(**data.get("purkinje", {})),
            ventricle=VentricularParameters(**data.get("ventricle", {})),
            atrium=AtrialParameters(**data.get("atrium", {})),
            hrv=HRVParameters(**data.get("hrv", {})),
            body_surface=BodySurfaceParameters(**data.get("body_surface", {})),
            ectopic_focus=EctopicFocusParameters(**data.get("ectopic_focus", {})),
            escape_pacemaker=EscapePacemakerParameters(**data.get("escape_pacemaker", {})),
            atrial_fib=AtrialFibParameters(**data.get("atrial_fib", {})),
        )

    def save(self, path: Path) -> None:
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        logger.debug("Parameters saved to %s", path)

    @classmethod
    def load(cls, path: Path) -> SimulationParameters:
        data = json.loads(path.read_text(encoding="utf-8"))
        logger.debug("Parameters loaded from %s", path)
        return cls.from_dict(data)
