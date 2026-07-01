"""
FitzHugh-Nagumo cardiac cell model — Phase 3-A.

A 2-variable simplified excitable-media model (2 ODEs) that captures the
essential qualitative features of a cardiac action potential:

* **Excitability threshold** — all-or-nothing response to suprathreshold stimuli.
* **Stable resting state** — the system returns to a unique fixed point.
* **Refractory period** — emergent from the recovery variable *w* dynamics;
  no separate refractory parameter required.
* **Qualitatively correct AP shape** — fast upstroke, slower repolarisation.

The model is NOT quantitatively accurate for specific ionic currents
(use ten Tusscher / Courtemanche in Phase 5 for that), but is numerically
cheap, non-stiff, and ideal for 1-D cable propagation (Phase 3-B).

Equations (dimensionless time τ)
---------------------------------
::

    dv/dτ = v - v³/3 - w + I_ext
    dw/dτ = ε · (v + a - b·w)

    Physical time:  dt_s = dτ · τ_s

Physical parameters
--------------------
τ_s = 0.025 s  (25 ms per dimensionless time unit).

Resting state (nullcline intersection, a=0.7, b=0.8)
  v_rest ≈ −1.200,  w_rest ≈ −0.625

AP peak ≈ +2.0,  APD ≈ 300–375 ms at default τ_s.

References
----------
FitzHugh R. (1961). Impulses and physiological states in theoretical models
  of nerve membrane. *Biophys. J.*, 1:445–466.
Nagumo J. et al. (1962). An active pulse transmission line simulating nerve axon.
  *Proc. IRE*, 50:2061–2070.
"""

from __future__ import annotations

import numpy as np

from cardiac_sim.core.cell_models.base_cell import BaseCellModel


class FitzHughNagumoCell(BaseCellModel):
    """
    FitzHugh-Nagumo cardiac cell model.

    State vector ``[v, w]``
        * ``v`` — fast membrane-potential analogue (dimensionless).
          Rest ≈ −1.2;  peak ≈ +2.0.
        * ``w`` — slow recovery variable (dimensionless).
          Rest ≈ −0.625.

    Parameters
    ----------
    a, b, eps:
        Classical FHN parameters.  Defaults produce a stable resting point
        and a full action potential upon suprathreshold stimulation.
    tau_s:
        Physical time scale [s].  1 dimensionless unit = ``tau_s`` seconds.
        Default 0.025 s gives APD ≈ 300–375 ms.
    """

    #: Resting state (nullcline intersection for default a, b, eps)
    V_REST: float = -1.200
    W_REST: float = -0.625

    #: Peak v during action potential
    V_PEAK: float = 2.0

    def __init__(
        self,
        a: float = 0.700,
        b: float = 0.800,
        eps: float = 0.080,
        tau_s: float = 0.025,
    ) -> None:
        super().__init__()
        self.a = a
        self.b = b
        self.eps = eps
        self.tau_s = tau_s   # seconds per dimensionless time unit

    # ------------------------------------------------------------------
    # AbstractCellModel interface
    # ------------------------------------------------------------------

    def get_initial_state(self) -> np.ndarray:
        """Return the resting state ``[v_rest, w_rest]``."""
        return np.array([self.V_REST, self.W_REST], dtype=np.float64)

    def compute_derivatives(self, t: float, state: np.ndarray) -> np.ndarray:
        """
        Compute ``d[v, w]/dt`` in physical units (s⁻¹).

        Parameters
        ----------
        t:
            Absolute simulation time [s] — used to evaluate the stimulus.
        state:
            Shape ``(2,)`` — ``[v, w]``.
        """
        v, w = state
        I = self.total_external_current(t)

        # Dimensionless FHN equations
        dv_dim = v - (v ** 3) / 3.0 - w + I
        dw_dim = self.eps * (v + self.a - self.b * w)

        # Scale to physical time
        inv_tau = 1.0 / self.tau_s
        return np.array([dv_dim * inv_tau, dw_dim * inv_tau], dtype=np.float64)

    def get_state_variable_names(self) -> tuple[str, ...]:
        return ("v", "w")

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def stimulate(
        self,
        t_start: float,
        duration: float = 0.010,
        amplitude: float = 1.5,
    ) -> None:
        """
        Apply a suprathreshold depolarising pulse.

        FHN-specific defaults are larger than the base class defaults because
        the excitation threshold in this parameterisation requires
        ``amplitude × (duration / τ_s) ≥ 0.4`` dimensionless units to
        reliably cross the threshold from rest.

        With ``amplitude=1.5, duration=0.010 s``:
        ΔV_dim ≈ 1.5 × (0.010/0.025) = 0.6  →  threshold crossed ✓
        """
        super().stimulate(t_start, duration, amplitude)

    def membrane_voltage_mv(self, state: np.ndarray) -> float:
        """Convert the fast variable *v* to millivolts (approx.)."""
        return self.v_to_mv(state[0], self.V_REST, self.V_PEAK)
