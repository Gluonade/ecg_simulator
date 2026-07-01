"""
AV Conduction Block Plugins — Phase 2.

All four grades of AV block, plus Wenckebach periodicity.

Clinical mechanism → parameter mapping
---------------------------------------
* AV block I°  — prolonged AV node delay (fixed PR > 200 ms)
* AV block II° Mobitz I (Wenckebach) — progressive PR lengthening ending in
  a dropped QRS; stateful (beat-level override)
* AV block II° Mobitz II — sudden dropped beat at a fixed P:QRS ratio;
  stateful (beat-level override)
* AV block III° (complete) — AV conductance = 0; ventricular nodes never
  activated → P waves with no QRS (ventricular escape not modelled in Phase 2)
"""

from __future__ import annotations

from cardiac_sim.core.interfaces import (
    AbstractPathologyPlugin,
    AbstractStatefulPathologyPlugin,
    PluginParameter,
)
from cardiac_sim.core.parameter_model import SimulationParameters


# ---------------------------------------------------------------------------
# AV Block I°
# ---------------------------------------------------------------------------

class AVBlockI(AbstractPathologyPlugin):
    """AV Block I°: prolonged but not dropped AV conduction."""

    def __init__(self) -> None:
        self._delay_ms: float = 240.0   # default: PR ≈ 315 ms (> 200 ms)

    def get_display_name(self) -> str:
        return "AV Block I°"

    def get_description(self) -> str:
        return "Prolonged PR interval (> 200 ms) due to delayed AV node conduction."

    def get_parameters(self) -> list[PluginParameter]:
        return [
            PluginParameter(
                name="delay_ms",
                display_name="AV Delay",
                description="AV node conduction delay",
                default_value=240.0,
                min_value=200.0,
                max_value=500.0,
                unit="ms",
            )
        ]

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.av_node.conduction_delay_ms = self._delay_ms
        p.av_node.conductance = 1.0
        return p


# ---------------------------------------------------------------------------
# AV Block II° Mobitz I (Wenckebach)
# ---------------------------------------------------------------------------

class AVBlockMobitzI(AbstractStatefulPathologyPlugin):
    """
    AV Block II° Mobitz I (Wenckebach).

    Default: 4:3 block — 4 P waves, 3 conducted QRS complexes.
    The PR interval lengthens by ``increment_ms`` per beat until the
    ``cycle_length``-th beat is dropped, then resets.
    """

    def __init__(self) -> None:
        self._base_delay_ms: float = 160.0   # PR after each reset
        self._increment_ms: float = 60.0     # PR increment per beat
        self._cycle_length: int = 4          # beats per Wenckebach cycle (incl. drop)
        self._position: int = 0

    def get_display_name(self) -> str:
        return "AV Block II° Mobitz I (Wenckebach)"

    def get_description(self) -> str:
        return (
            "Progressive PR lengthening ending in a dropped QRS "
            f"({self._cycle_length - 1}:{self._cycle_length - 1} conduction ratio)."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return [
            PluginParameter("base_delay_ms", "Base AV Delay",
                            "AV delay after each reset", 160.0, 120.0, 300.0, "ms"),
            PluginParameter("increment_ms", "PR Increment",
                            "PR lengthening per beat", 60.0, 20.0, 120.0, "ms"),
        ]

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        # No permanent baseline change — Wenckebach is applied beat-by-beat.
        return params

    def on_beat(self, params: SimulationParameters, beat_id: int) -> SimulationParameters:
        p = params.copy()
        pos = self._position % self._cycle_length
        if pos == self._cycle_length - 1:
            # Dropped beat: complete block for this one SA firing
            p.av_node.conductance = 0.0
        else:
            p.av_node.conduction_delay_ms = self._base_delay_ms + pos * self._increment_ms
            p.av_node.conductance = 1.0
        self._position += 1
        return p

    def reset(self) -> None:
        self._position = 0


# ---------------------------------------------------------------------------
# AV Block II° Mobitz II
# ---------------------------------------------------------------------------

class AVBlockMobitzII(AbstractStatefulPathologyPlugin):
    """
    AV Block II° Mobitz II.

    Default: 3:2 block — every third P wave is not conducted.
    The PR interval of conducted beats remains constant (unlike Wenckebach).
    """

    def __init__(self) -> None:
        self._p_waves: int = 3       # P waves per group
        self._qrs_waves: int = 2     # conducted QRS per group (must be < p_waves)
        self._position: int = 0

    def get_display_name(self) -> str:
        return "AV Block II° Mobitz II"

    def get_description(self) -> str:
        return (
            f"Fixed {self._p_waves}:{self._qrs_waves} AV block with constant PR "
            f"on conducted beats and sudden non-conduction."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return [
            PluginParameter("p_waves", "P Waves / Group",
                            "Total P waves per block cycle", 3.0, 2.0, 8.0, ""),
            PluginParameter("qrs_waves", "QRS / Group",
                            "Conducted QRS per cycle", 2.0, 1.0, 7.0, ""),
        ]

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        return params

    def on_beat(self, params: SimulationParameters, beat_id: int) -> SimulationParameters:
        p = params.copy()
        pos = self._position % self._p_waves
        if pos >= self._qrs_waves:
            p.av_node.conductance = 0.0
        else:
            p.av_node.conductance = 1.0
        self._position += 1
        return p

    def reset(self) -> None:
        self._position = 0


# ---------------------------------------------------------------------------
# AV Block III° (Complete)
# ---------------------------------------------------------------------------

class AVBlockIII(AbstractPathologyPlugin):
    """
    AV Block III° (complete AV dissociation).

    The AV node conductance is set to zero permanently.  All P waves are
    blocked; ventricular nodes receive no supraventricular activation
    → flat ventricular line.  (A ventricular escape pacemaker will be added
    in Phase 4.)
    """

    def get_display_name(self) -> str:
        return "AV Block III° (Complete)"

    def get_description(self) -> str:
        return (
            "Complete AV dissociation: P waves present, no QRS. "
            "Ventricular escape pacemaker not yet modelled (Phase 4)."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return []

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.av_node.conductance = 0.0
        # Ventricular escape pacemaker (infra-Hisian level): wide, bizarre QRS
        # at ~40 bpm, independent of the blocked SA-to-AV path.
        # Use origin="LV_LAT" for wide ventricular QRS as expected clinically.
        p.escape_pacemaker.enabled = True
        p.escape_pacemaker.escape_interval_ms = 1500.0
        p.escape_pacemaker.origin = "LV_LAT"
        return p
