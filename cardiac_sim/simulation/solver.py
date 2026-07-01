"""
ODE solvers for cell models (Phase 3+).

Solvers implement :class:`~cardiac_sim.core.interfaces.AbstractSolver` and
are injected into cell-model-based engines.  Choose based on the stiffness
of the target cell model:

* :class:`EulerSolver`   — explicit Euler, O(dt).  Fast; use for non-stiff
  simplified models (FitzHugh-Nagumo) in fast-mode only.
* :class:`RK4Solver`     — classical 4th-order Runge-Kutta, O(dt⁴).
  Accurate for non-stiff systems; good default for FitzHugh-Nagumo.
* :class:`AdaptiveRK45Solver` — Dormand-Prince RK45 with error control.
  Required for stiff models (ten Tusscher, Courtemanche).

None of these solvers are used in Phase 0; they are here to satisfy the
architecture contract defined in the interfaces.
"""

from __future__ import annotations

import numpy as np

from cardiac_sim.core.interfaces import AbstractCellModel, AbstractSolver


class EulerSolver(AbstractSolver):
    """Explicit Euler: ``state += dt * f(t, state)``."""

    def step(
        self,
        model: AbstractCellModel,
        state: np.ndarray,
        t: float,
        dt: float,
    ) -> np.ndarray:
        return state + dt * model.compute_derivatives(t, state)


class RK4Solver(AbstractSolver):
    """Classical 4th-order Runge-Kutta."""

    def step(
        self,
        model: AbstractCellModel,
        state: np.ndarray,
        t: float,
        dt: float,
    ) -> np.ndarray:
        k1 = model.compute_derivatives(t, state)
        k2 = model.compute_derivatives(t + dt * 0.5, state + dt * 0.5 * k1)
        k3 = model.compute_derivatives(t + dt * 0.5, state + dt * 0.5 * k2)
        k4 = model.compute_derivatives(t + dt, state + dt * k3)
        return state + (dt / 6.0) * (k1 + 2.0 * k2 + 2.0 * k3 + k4)


class AdaptiveRK45Solver(AbstractSolver):
    """
    Dormand-Prince RK45 with step-size control.

    Intended for stiff ion-channel ODE systems (ten Tusscher, Courtemanche).
    The *dt* argument serves as the **maximum** allowed step size; the solver
    may sub-step internally to meet the error tolerance.

    Parameters
    ----------
    rtol, atol:
        Relative and absolute error tolerances.
    """

    def __init__(self, rtol: float = 1e-6, atol: float = 1e-8) -> None:
        self._rtol = rtol
        self._atol = atol

    def step(
        self,
        model: AbstractCellModel,
        state: np.ndarray,
        t: float,
        dt: float,
    ) -> np.ndarray:
        # Dormand-Prince coefficients
        c2, c3, c4, c5 = 1 / 5, 3 / 10, 4 / 5, 8 / 9
        a21 = 1 / 5
        a31, a32 = 3 / 40, 9 / 40
        a41, a42, a43 = 44 / 45, -56 / 15, 32 / 9
        a51, a52, a53, a54 = 19372 / 6561, -25360 / 2187, 64448 / 6561, -212 / 729
        a61, a62, a63, a64, a65 = (
            9017 / 3168, -355 / 33, 46732 / 5247, 49 / 176, -5103 / 18656
        )
        e1, e3, e4, e5, e6, e7 = (
            71 / 57600, -71 / 16695, 71 / 1920, -17253 / 339200, 22 / 525, -1 / 40
        )

        h = dt
        t_end = t + dt
        y = state.copy()
        t_cur = t

        while t_cur < t_end:
            h = min(h, t_end - t_cur)
            f1 = model.compute_derivatives(t_cur, y)
            f2 = model.compute_derivatives(t_cur + c2 * h, y + h * a21 * f1)
            f3 = model.compute_derivatives(t_cur + c3 * h, y + h * (a31 * f1 + a32 * f2))
            f4 = model.compute_derivatives(
                t_cur + c4 * h, y + h * (a41 * f1 + a42 * f2 + a43 * f3)
            )
            f5 = model.compute_derivatives(
                t_cur + c5 * h,
                y + h * (a51 * f1 + a52 * f2 + a53 * f3 + a54 * f4),
            )
            f6 = model.compute_derivatives(
                t_cur + h,
                y + h * (a61 * f1 + a62 * f2 + a63 * f3 + a64 * f4 + a65 * f5),
            )
            y_new = y + h * (
                (35 / 384) * f1
                + (500 / 1113) * f3
                + (125 / 192) * f4
                - (2187 / 6784) * f5
                + (11 / 84) * f6
            )
            f7 = model.compute_derivatives(t_cur + h, y_new)
            err = h * (e1 * f1 + e3 * f3 + e4 * f4 + e5 * f5 + e6 * f6 + e7 * f7)
            scale = self._atol + self._rtol * np.maximum(np.abs(y), np.abs(y_new))
            err_norm = float(np.sqrt(np.mean((err / scale) ** 2)))

            if err_norm <= 1.0:
                y = y_new
                t_cur += h
                if err_norm == 0.0:
                    factor = 5.0
                else:
                    factor = min(5.0, 0.9 * err_norm ** (-0.2))
                h *= factor
            else:
                factor = max(0.1, 0.9 * err_norm ** (-0.25))
                h *= factor

        return y
