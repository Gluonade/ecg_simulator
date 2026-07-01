"""
Sinus Rhythm Plugins — Phase 2.

Adjust the SA node cycle length to simulate sinus bradycardia and
sinus tachycardia.  HRV can be toggled on/off independently.
"""

from __future__ import annotations

from cardiac_sim.core.interfaces import AbstractPathologyPlugin, PluginParameter
from cardiac_sim.core.parameter_model import SimulationParameters


class SinusBradycardia(AbstractPathologyPlugin):
    """Sinus bradycardia: heart rate < 60 bpm."""

    def __init__(self) -> None:
        self._cycle_length_ms: float = 1200.0   # 50 bpm

    def get_display_name(self) -> str:
        return "Sinus Bradycardia"

    def get_description(self) -> str:
        return "Sinus bradycardia: HR < 60 bpm (default 50 bpm)."

    def get_parameters(self) -> list[PluginParameter]:
        return [
            PluginParameter(
                name="cycle_length_ms",
                display_name="Cycle Length",
                description="RR interval",
                default_value=1200.0,
                min_value=1000.0,
                max_value=3000.0,
                unit="ms",
            )
        ]

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.sa_node.cycle_length_ms = self._cycle_length_ms
        return p


class SinusTachycardia(AbstractPathologyPlugin):
    """Sinus tachycardia: heart rate > 100 bpm."""

    def __init__(self) -> None:
        self._cycle_length_ms: float = 500.0   # 120 bpm

    def get_display_name(self) -> str:
        return "Sinus Tachycardia"

    def get_description(self) -> str:
        return "Sinus tachycardia: HR > 100 bpm (default 120 bpm)."

    def get_parameters(self) -> list[PluginParameter]:
        return [
            PluginParameter(
                name="cycle_length_ms",
                display_name="Cycle Length",
                description="RR interval",
                default_value=500.0,
                min_value=300.0,
                max_value=600.0,
                unit="ms",
            )
        ]

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.sa_node.cycle_length_ms = self._cycle_length_ms
        return p


class HRVDisabled(AbstractPathologyPlugin):
    """Disable heart rate variability — perfectly regular rhythm."""

    def get_display_name(self) -> str:
        return "HRV Disabled (Regular Rhythm)"

    def get_description(self) -> str:
        return "Disables all HRV modulation; produces a perfectly metronomic sinus rhythm."

    def get_parameters(self) -> list[PluginParameter]:
        return []

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.hrv.enabled = False
        return p
