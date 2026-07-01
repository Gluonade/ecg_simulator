"""
Aliev-Panfilov cardiac cell model — Phase 3-A.

A 2-variable model designed specifically for cardiac tissue.  Compared to
FitzHugh-Nagumo it reproduces a more physiologically realistic action
potential shape — in particular, the plateau phase characteristic of
ventricular myocytes.

Equations (dimensionless time τ)
---------------------------------
::

    du/dτ = −k · u · (u − a) · (u − 1) − u · v + I_ext
    dv/dτ = (ε₀ + μ₁ · v / (μ₂ + u)) · (−v − k · u · (u − a − 1))

    Physical time:  dt_s = dτ · τ_s

State vector ``[u, v]``
    * ``u`` — membrane potential analogue [0 = rest, 1 = peak depolarisation].
    * ``v`` — recovery / repolarisation variable.

Physical parameters
--------------------
τ_s = 0.0100 s (10 ms per dimensionless time unit).
With standard k, a, ε₀, μ₁, μ₂ parameters:
  AP peak ≈ 0.95–1.0 (u),  APD ≈ 280–350 ms,  refractory ≈ 380–450 ms.

References
----------
Aliev R.R. & Panfilov A.V. (1996). A simple two-variable model of cardiac
  excitation. *Chaos, Solitons & Fractals*, 7(3):293–301.
"""

from __future__ import annotations

import numpy as np

from cardiac_sim.core.cell_models.base_cell import BaseCellModel


class AlievPanfilovCell(BaseCellModel):
    """
    Aliev-Panfilov cardiac cell model.

    State vector ``[u, v]``
        * ``u`` — dimensionless membrane potential; 0 = rest, ≈1 = peak.
        * ``v`` — dimensionless recovery variable; 0 = rest.

    The recovery equation includes an adaptive time-scale factor
    ``ε(u, v) = ε₀ + μ₁·v/(μ₂+u)`` that accelerates repolarisation and
    prolongs the plateau, giving a more realistic ventricular AP shape.

    Parameters
    ----------
    k:
        Excitability gain.  Default 8.0.
    a:
        Excitation threshold.  Default 0.15 (≈15 % of full excursion).
    eps0:
        Minimum recovery time-scale.  Default 0.002.
    mu1, mu2:
        Adaptive time-scale coefficients.  Defaults 0.2 and 0.3.
    tau_s:
        Physical time scale [s].  1 dimensionless unit = ``tau_s`` seconds.
        Default 0.010 s gives APD ≈ 280–350 ms.
    """

    #: Resting state
    U_REST: float = 0.0
    V_REST_AP: float = 0.0   # named V_REST_AP to avoid clash with BaseCellModel.V_REST

    #: Approximate peak of u during AP
    U_PEAK: float = 1.0

    def __init__(
        self,
        k: float = 8.0,
        a: float = 0.15,
        eps0: float = 0.002,
        mu1: float = 0.2,
        mu2: float = 0.3,
        tau_s: float = 0.010,
    ) -> None:
        super().__init__()
        self.k = k
        self.a = a
        self.eps0 = eps0
        self.mu1 = mu1
        self.mu2 = mu2
        self.tau_s = tau_s

    # ------------------------------------------------------------------
    # AbstractCellModel interface
    # ------------------------------------------------------------------

    def get_initial_state(self) -> np.ndarray:
        """Return the resting state ``[0, 0]``."""
        return np.array([self.U_REST, self.V_REST_AP], dtype=np.float64)

    def compute_derivatives(self, t: float, state: np.ndarray) -> np.ndarray:
        """
        Compute ``d[u, v]/dt`` in physical units (s⁻¹).

        Parameters
        ----------
        t:
            Absolute simulation time [s] — used to evaluate the stimulus.
        state:
            Shape ``(2,)`` — ``[u, v]``.
        """
        u, v = state
        I = self.total_external_current(t)

        # Dimensionless Aliev-Panfilov equations
        du_dim = -self.k * u * (u - self.a) * (u - 1.0) - u * v + I

        # Adaptive recovery time scale; guard against u → −∞ numerical noise
        u_safe = max(u, 0.0)
        eps_eff = self.eps0 + self.mu1 * v / (self.mu2 + u_safe + 1e-12)
        dv_dim = eps_eff * (-v - self.k * u * (u - self.a - 1.0))

        # Scale to physical time
        inv_tau = 1.0 / self.tau_s
        return np.array([du_dim * inv_tau, dv_dim * inv_tau], dtype=np.float64)

    def get_state_variable_names(self) -> tuple[str, ...]:
        return ("u", "v")

    # ------------------------------------------------------------------
    # Convenience
    # ------------------------------------------------------------------

    def stimulate(
        self,
        t_start: float,
        duration: float = 0.005,
        amplitude: float = 0.5,
    ) -> None:
        """
        Apply a suprathreshold depolarising pulse.

        Aliev-Panfilov default: ``amplitude=0.5, duration=5 ms``.
        With τ_s=0.010 s:  ΔU_dim ≈ 0.5 × (0.005/0.010) = 0.25 > threshold(0.15) ✓
        """
        super().stimulate(t_start, duration, amplitude)

    def membrane_voltage_mv(self, state: np.ndarray) -> float:
        """
        Convert the membrane variable *u* to millivolts (approx.).

        Maps u = 0 → −85 mV (rest) and u = 1 → +30 mV (peak).
        """
        return self.v_to_mv(state[0], self.U_REST, self.U_PEAK)
