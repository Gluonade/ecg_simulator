"""
Ventricular Extrasystole (VES / PVC) Plugin — Phase 2.

Enables a single ventricular ectopic focus that fires at a fixed coupling
interval after each preceding SA node activation, producing bigeminy.

Mechanism
---------
The ``EctopicFocusParameters.enabled`` flag is set in ``SimulationParameters``.
The engine reads this flag in ``_fire_sa_node`` and schedules ``_fire_ectopic``
at ``coupling_interval_ms`` after each SA firing.

The ectopic beat uses a fixed cell-to-cell activation spread starting from
``LV_LAT``, bypassing the Purkinje network entirely → wide QRS (≈ 140 ms),
no preceding P wave.

Phase 2 limitations
-------------------
* The VES morphology is an approximation (the same node dipole directions
  are used as for sinus beats, but activated in slow retrograde order).
* True compensatory pause depends on whether the following sinus beat finds
  the ventricles refractory — this emerges naturally from the 250 ms
  refractory periods already modelled.
* More accurate VES morphology (negative concordance in V1–V6 for LV-origin
  VES) and multiple foci are Phase 4.
"""

from __future__ import annotations

from cardiac_sim.core.interfaces import AbstractPathologyPlugin, PluginParameter
from cardiac_sim.core.parameter_model import SimulationParameters


class VentricularExtrasystole(AbstractPathologyPlugin):
    """
    Ventricular Extrasystole (VES / PVC) — bigeminy pattern.

    Fires one ectopic beat from the lateral LV wall at a fixed coupling
    interval after each sinus beat, producing a wide QRS with no P wave.
    """

    def __init__(self) -> None:
        self._coupling_ms: float = 600.0

    def get_display_name(self) -> str:
        return "Ventricular Extrasystole (Bigeminy)"

    def get_description(self) -> str:
        return (
            "Repeating PVC originating from LV lateral wall, firing "
            f"{self._coupling_ms:.0f} ms after each sinus beat."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return [
            PluginParameter(
                name="coupling_ms",
                display_name="Coupling Interval",
                description="Time from sinus beat to ectopic fire",
                default_value=600.0,
                min_value=300.0,
                max_value=1200.0,
                unit="ms",
            )
        ]

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.ectopic_focus.enabled = True
        p.ectopic_focus.coupling_interval_ms = self._coupling_ms
        p.ectopic_focus.repetitive = True
        return p
