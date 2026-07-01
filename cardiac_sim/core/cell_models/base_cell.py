"""
BaseCellModel — shared utilities for 2-variable excitable-media cell models.

Provides stimulus injection, coupling current (for Phase 3-B cable propagation),
and a dimensionless-to-mV voltage mapping used by all concrete cell models.
"""

from __future__ import annotations

from cardiac_sim.core.interfaces import AbstractCellModel


class BaseCellModel(AbstractCellModel):
    """
    Abstract base for 2-variable simplified cardiac cell models.

    Subclasses implement :meth:`get_initial_state`,
    :meth:`compute_derivatives`, and :meth:`get_state_variable_names`.
    This class adds three shared concerns:

    Stimulus injection
        :meth:`stimulate` sets a time-windowed depolarising current.
        ``compute_derivatives`` implementations call
        :meth:`total_external_current` to get the combined
        stimulus + coupling current at any time *t*.

    Coupling current  (Phase 3-B)
        The cable solver sets ``self.coupling_current`` before each
        :meth:`~cardiac_sim.core.interfaces.AbstractSolver.step` call.
        Isolated-cell use (Phase 3-A) leaves it at 0.0.

    Voltage mapping
        :meth:`v_to_mv` converts the model's dimensionless membrane
        variable to millivolts for ECG / display purposes.
    """

    def __init__(self) -> None:
        self._stim_end: float = -1.0        # absolute time when stimulus ends
        self._stim_amplitude: float = 0.0   # dimensionless current amplitude
        self.coupling_current: float = 0.0  # set by cable solver (Phase 3-B)

    # ------------------------------------------------------------------
    # Stimulus API
    # ------------------------------------------------------------------

    def stimulate(
        self,
        t_start: float,
        duration: float = 0.002,
        amplitude: float = 0.5,
    ) -> None:
        """
        Schedule an external depolarising pulse.

        Parameters
        ----------
        t_start:
            Absolute simulation time when the stimulus begins [s].
        duration:
            Duration of the stimulus pulse [s].  Default 2 ms.
        amplitude:
            Dimensionless current amplitude.  Default 0.5 (sufficient
            to trigger an AP in both FHN and Aliev-Panfilov models).
        """
        self._stim_end = t_start + duration
        self._stim_amplitude = amplitude

    def total_external_current(self, t: float) -> float:
        """
        Return the total external current at time *t*.

        Combines the time-gated stimulus with any coupling current
        injected by the cable solver.
        """
        I_stim = self._stim_amplitude if t < self._stim_end else 0.0
        return I_stim + self.coupling_current

    # ------------------------------------------------------------------
    # Utility: dimensionless → physical voltage
    # ------------------------------------------------------------------

    @staticmethod
    def v_to_mv(
        v_norm: float,
        v_rest_norm: float,
        v_peak_norm: float,
    ) -> float:
        """
        Linearly map a dimensionless membrane variable to millivolts.

        Maps ``v_rest_norm → −85 mV`` and ``v_peak_norm → +30 mV``
        (typical ventricular action potential range).

        Parameters
        ----------
        v_norm:
            Current dimensionless membrane variable.
        v_rest_norm:
            Dimensionless value at resting potential.
        v_peak_norm:
            Dimensionless value at peak depolarisation.
        """
        span = v_peak_norm - v_rest_norm
        if abs(span) < 1e-9:
            return -85.0
        frac = (v_norm - v_rest_norm) / span
        return -85.0 + frac * 115.0   # −85 mV to +30 mV = 115 mV span
