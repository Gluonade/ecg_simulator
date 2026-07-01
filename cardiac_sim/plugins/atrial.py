"""
Atrial Fibrillation Plugin — Phase 2.

Implements AF at the conduction-graph level without requiring 2-D tissue
propagation (which is Phase 4).  The model captures the two key clinical
features:

1. **Irregular RR intervals** — ventricular beats are generated at
   exponentially distributed intervals (Poisson process), mimicking the
   irregular AV node conduction seen in AF.  Each QRS complex uses the
   normal His-Purkinje system, so bundle-branch blocks (if active) still
   produce wide-QRS AF.

2. **Fibrillatory baseline (f-waves)** — a superposition of four sinusoids
   near ``atrial_rate_hz`` with slightly different frequencies and phase
   offsets creates the irregular undulating baseline characteristic of AF.

Phase 2 limitations
-------------------
* True AF requires 2-D atrial tissue with multiple re-entry wavelets
  (Phase 4).  The current model uses a statistical approximation.
* The f-wave morphology is simplified (fixed direction, constant amplitude).
  In reality f-wave size varies per lead and fluctuates over time.
* The QRS morphology in AF is identical to sinus rhythm for the same
  conduction parameters (correct — AF conducts normally through the
  His-Purkinje system).
"""

from __future__ import annotations

from cardiac_sim.core.interfaces import AbstractPathologyPlugin, PluginParameter
from cardiac_sim.core.parameter_model import SimulationParameters


class AtrialFibrillation(AbstractPathologyPlugin):
    """
    Atrial Fibrillation (AF) — Phase 2 conduction-graph model.

    Produces:

    * Absent P waves
    * Irregularly irregular QRS complexes (~80-100 bpm average)
    * Continuous fibrillatory f-wave baseline (≈ 330 bpm, 0.08 mV peak)
    """

    def __init__(self) -> None:
        self._mean_rr_ms: float = 700.0          # ~86 bpm mean ventricular rate
        self._atrial_rate_hz: float = 5.5         # f-wave frequency
        self._f_wave_amp_mv: float = 0.08

    def get_display_name(self) -> str:
        return "Atrial Fibrillation"

    def get_description(self) -> str:
        return (
            "AF: absent P waves, irregularly irregular QRS, f-wave baseline. "
            "QRS morphology unchanged (narrow unless BBB is also active)."
        )

    def get_parameters(self) -> list[PluginParameter]:
        return [
            PluginParameter(
                name="mean_rr_ms",
                display_name="Mean RR",
                description="Mean ventricular interval (controls average HR)",
                default_value=700.0,
                min_value=300.0,
                max_value=1500.0,
                unit="ms",
            ),
            PluginParameter(
                name="atrial_rate_hz",
                display_name="f-wave Rate",
                description="Dominant fibrillation frequency",
                default_value=5.5,
                min_value=4.0,
                max_value=10.0,
                unit="Hz",
            ),
        ]

    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.atrial_fib.enabled = True
        p.atrial_fib.mean_rr_ms = self._mean_rr_ms
        p.atrial_fib.atrial_rate_hz = self._atrial_rate_hz
        p.atrial_fib.f_wave_amplitude_mv = self._f_wave_amp_mv
        # Disable HRV modulation in AF (irregularity comes from the model itself)
        p.hrv.enabled = False
        return p
