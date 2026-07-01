"""
Bundle Branch and Fascicular Block Plugins — Phase 2.

Clinical mechanism → parameter mapping
---------------------------------------
* LBBB  — ``left_branch_conductance = 0``  → LV activated retrogradely via
  RV working-myocardium paths (wide QRS, no septal q waves, L-axis)
* RBBB  — ``right_branch_conductance = 0`` → RV activated retrogradely via
  LV_ANT (wide QRS, rSR' V1, broad S in I/V6)
* LAHB  — ``left_anterior_conductance = 0`` → LV_ANT activated from LV_INF
  (left-axis deviation, narrow QRS)
* LPHB  — ``left_posterior_conductance = 0`` → LV_INF activated from LV_ANT
  (right-axis deviation, narrow QRS)

All wide-QRS morphologies emerge from the retrograde working-myocardium
edges in the conduction graph (added in Phase 2) combined with the
refractory period mechanism that keeps those paths normally silent.
"""

from __future__ import annotations

from cardiac_sim.core.interfaces import AbstractPathologyPlugin, PluginParameter
from cardiac_sim.core.parameter_model import SimulationParameters


class LBBB(AbstractPathologyPlugin):
    """Left Bundle Branch Block."""

    def get_display_name(self) -> str:
        return "LBBB"

    def get_description(self) -> str:
        return (
            "Left bundle branch block: LV activates retrogradely via RV. "
            "Wide QRS (> 120 ms), no septal q waves, left-axis deviation."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return []

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.his_bundle.left_branch_conductance = 0.0
        return p


class RBBB(AbstractPathologyPlugin):
    """Right Bundle Branch Block."""

    def get_display_name(self) -> str:
        return "RBBB"

    def get_description(self) -> str:
        return (
            "Right bundle branch block: RV activates retrogradely via LV. "
            "Wide QRS (> 120 ms), rSR' in V1, broad S in I/V6."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return []

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.his_bundle.right_branch_conductance = 0.0
        return p


class LeftAnteriorHemiblock(AbstractPathologyPlugin):
    """Left Anterior Hemiblock (LAHB)."""

    def get_display_name(self) -> str:
        return "Left Anterior Hemiblock"

    def get_description(self) -> str:
        return (
            "Left anterior fascicle block: LV_ANT activates late via LV_INF. "
            "Left-axis deviation (axis < −30°), narrow QRS."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return []

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.his_bundle.left_anterior_conductance = 0.0
        return p


class LeftPosteriorHemiblock(AbstractPathologyPlugin):
    """Left Posterior Hemiblock (LPHB)."""

    def get_display_name(self) -> str:
        return "Left Posterior Hemiblock"

    def get_description(self) -> str:
        return (
            "Left posterior fascicle block: LV_INF activates late via LV_ANT. "
            "Right-axis deviation (axis > +110°), narrow QRS."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return []

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.his_bundle.left_posterior_conductance = 0.0
        return p
