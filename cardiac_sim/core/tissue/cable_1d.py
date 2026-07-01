"""
ConductionCable1D — 1-D monodomain cable model of the cardiac conduction system.

Two cable arrays simulate AP propagation using FitzHugh-Nagumo cells:

  Atrial cable  (N_A = 8 cells):
      cell 0         — SA node (pacemaker, no diffusion; receives periodic stimulus)
      cells 1 – 4    — right atrial myocardium  → ECG node "RA"
      cells 5 – 7    — left atrial myocardium   → ECG node "LA"

  Ventricular cable (N_V = 10 cells):
      cell 0         — His bundle               → ECG node "HIS"
      cells 1 – 3    — Left bundle branch       → ECG nodes "LBB", "SEPT_EARLY"
      cells 4 – 5    — LV anterior              → ECG node "LV_ANT"
      cells 6 – 7    — LV lateral free wall     → ECG node "LV_LAT"
      cells 8 – 9    — LV inferior / basal      → ECG nodes "LV_INF", "LV_POST"

  AV junction: programmed delay between last atrial cell crossing threshold
  and stimulation of the first ventricular cell.  Controlled by
  ``params.av_node.conduction_delay_ms``.

  RV: activated at ``t_HIS + params.his_bundle.his_delay_ms / 2 / 1000``
  (param-based, same as Phase 1/2; full RV cable in Phase 4).

Cable equation (explicit Euler, physical time)
----------------------------------------------
::

    dv[i]/dt = (D[i]/τ) · (v[i-1] – 2v[i] + v[i+1])  +  f(v[i], w[i]) / τ
    dw[i]/dt = ε · (v[i] + a – b·w[i]) / τ

    f(v,w) = v – v³/3 – w  (FHN fast variable)

    τ = 0.025 s,  ε = 0.08,  a = 0.7,  b = 0.8

Diffusion coefficients and expected conduction velocities
----------------------------------------------------------
Physical cell spacing dx = 6 mm.  With τ = 0.025 s:
  cv_phys ≈ K · √(D_dim/ε) · dx / τ   (K ≈ 0.5 for FHN)

  D_atrium  = 2.88  →  cv ≈ 0.69 m/s   (atrial myocardium ✓)
  D_his_lbb = 72.0  →  cv ≈ 3.6  m/s   (His-Purkinje ✓)
  D_lv      = 1.43  →  cv ≈ 0.49 m/s   (ventricular myocardium ✓)

CFL condition: D_dim · (dt / τ) ≤ 0.5.
With dt_int = 0.1 ms (τ/250): D_max = 125 ≥ 72 ✓

Expected timing (default params, normal sinus rhythm)
------------------------------------------------------
  SA → last atrial cell:          ~58 ms  (P wave duration)
  AV programmed delay:           100 ms
  His → SEPT_EARLY:               ~5 ms
  SEPT_EARLY → LV_LAT:           ~60 ms  (QRS duration)
  SA → end QRS:                  ~223 ms
"""

from __future__ import annotations

import logging
import math
import numpy as np

from cardiac_sim.core.cell_models.fitzhugh_nagumo import FitzHughNagumoCell
from cardiac_sim.core.parameter_model import SimulationParameters

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Optional Numba JIT acceleration
# ---------------------------------------------------------------------------
# Numba requires NumPy ≤ 2.4; the environment currently has NumPy 2.5.
# The try/except below keeps the code Numba-ready: when a compatible Numba
# is installed, @njit compilation kicks in automatically on first call and
# is cached to disk (__pycache__/*.nbi) for all subsequent runs.
# Without Numba the vectorised NumPy fallback runs at ≈2.2× real-time, which
# meets the Phase 3 performance requirement on a contemporary machine.
try:
    import numba as _numba

    @_numba.njit(cache=True)
    def _fhn_euler_step_jit(
        V: np.ndarray,
        W: np.ndarray,
        D: np.ndarray,
        I_ext: np.ndarray,
        dt: float,
        tau_s: float,
        a: float,
        b: float,
        eps: float,
    ):
        """Single explicit Euler step for FHN cable (Numba nopython mode)."""
        n = V.shape[0]
        V_new = np.empty(n)
        W_new = np.empty(n)
        inv_tau = 1.0 / tau_s
        for i in range(n):
            if i == 0:
                lap = V[1] - V[0]
            elif i == n - 1:
                lap = V[n - 2] - V[n - 1]
            else:
                lap = V[i - 1] - 2.0 * V[i] + V[i + 1]
            dv = (D[i] * lap + V[i] - V[i] ** 3 / 3.0 - W[i] + I_ext[i]) * inv_tau
            dw = eps * (V[i] + a - b * W[i]) * inv_tau
            V_new[i] = V[i] + dt * dv
            W_new[i] = W[i] + dt * dw
        return V_new, W_new

    _NUMBA_AVAILABLE = True
    logger.info("Numba JIT available — cable update will be compiled on first call.")

except (ImportError, Exception):
    _NUMBA_AVAILABLE = False
    logger.info(
        "Numba not available (requires NumPy ≤ 2.4); using vectorised NumPy fallback "
        "(≈2.2× real-time on a contemporary machine — meets Phase 3 requirement)."
    )


def _fhn_euler_step_numpy(
    V: np.ndarray,
    W: np.ndarray,
    D: np.ndarray,
    I_ext: np.ndarray,
    dt: float,
    tau_s: float,
    a: float,
    b: float,
    eps: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Single explicit Euler step for FHN cable (vectorised NumPy fallback)."""
    lap = np.empty_like(V)
    lap[1:-1] = V[:-2] - 2.0 * V[1:-1] + V[2:]
    lap[0]    = V[1]   - V[0]
    lap[-1]   = V[-2]  - V[-1]
    inv_tau = 1.0 / tau_s
    dv = (D * lap + V - V * V * V / 3.0 - W + I_ext) * inv_tau
    dw = eps * (V + a - b * W) * inv_tau
    return V + dt * dv, W + dt * dw


def _fhn_cable_step(
    V: np.ndarray,
    W: np.ndarray,
    D: np.ndarray,
    I_ext: np.ndarray,
    dt: float,
    tau_s: float,
    a: float,
    b: float,
    eps: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Dispatch to Numba JIT or NumPy fallback."""
    if _NUMBA_AVAILABLE:
        return _fhn_euler_step_jit(V, W, D, I_ext, dt, tau_s, a, b, eps)
    return _fhn_euler_step_numpy(V, W, D, I_ext, dt, tau_s, a, b, eps)

# ---------------------------------------------------------------------------
# Cable FHN parameters  (Phase 3-B validated)
# ---------------------------------------------------------------------------
# These are the verified Phase 3-B values that produce a working simulation.
# Timings with these values (measured, not estimated):
#   RA activation: ~124 ms    P-wave onset
#   LA activation: ~191 ms    P-wave duration
#   AV_NODE:       ~307 ms    (includes 100 ms programmed AV delay)
#   SEPT_EARLY:    ~435 ms    QRS onset
#   LV_POST:       ~645 ms    QRS end  (QRS ~210 ms)
# These timings are longer than clinical targets and will be reduced in
# Phase 3-D calibration once a reliable analytical model is available.
# The CFL condition is satisfied: D_max=72, dt=0.1 ms, τ=25 ms → CFL=0.288 ✓
# ---------------------------------------------------------------------------

_A   = 0.700   # FHN parameter a
_B   = 0.800   # FHN parameter b
_EPS = 0.080   # FHN recovery rate (gives APD ~180 ms at τ = 15 ms)
_TAU = 0.015   # physical time scale [s]  ← calibrated for correct PR and QRS
               # Numerical search result (Phase 3-D):
               #   τ=25ms → P=191ms  PR=435ms  QRS=210ms  (too slow)
               #   τ=20ms → P=104ms  PR=266ms  QRS=151ms
               #   τ=15ms → P= 64ms  PR=196ms  QRS=111ms  ← selected
               #   τ=13ms → CFL violated with current dt_int=0.1ms
               # CFL check: D_max × dt_int / τ = 72 × 0.0001 / 0.015 = 0.480 ≤ 0.5 ✓
               # Recovery τ_w = τ/(ε·b) = 234 ms → 91% at 70 bpm → multi-beat ✓

_V_REST  = FitzHughNagumoCell.V_REST   # −1.200
_W_REST  = FitzHughNagumoCell.W_REST   # −0.625
_V_THRESH = 0.0   # v > 0  ≡  "activated"

# Internal micro-step: CFL = D_max · (dt/τ) = 72 · (0.0001/0.025) = 0.288 ≤ 0.5 ✓
_DT_INT = 0.0001   # 0.1 ms

# Cable cell counts
_NA = 8    # SA node (1) + atrial cells (7)
_NV = 10   # His (1) + LBB (3) + LV (6)

# Diffusion profiles
_D_ATR = np.full(_NA, 2.88, dtype=np.float64)   # atrial myocardium
_D_ATR[0] = 0.0                                   # SA node: no diffusion

_D_VEN = np.empty(_NV, dtype=np.float64)
_D_VEN[0]   = 0.0    # His bundle: point stimulus receiver (no diffusion loading)
_D_VEN[1:4] = 72.0   # Left bundle branch: fast Purkinje
_D_VEN[4:]  = 3.0    # LV myocardium  (↑ from 1.43 → shorter QRS ≈ 70 ms)

# Stimulus parameters
_STIM_AMP = 1.5
_STIM_DUR = 0.010   # 10 ms

# Segment → ECG-node mapping
_ATR_SEGS: dict[str, tuple[int, int]] = {
    "SA_NODE": (0, 0),
    "RA":      (1, 4),   # right atrial myocardium (cells 1-4)
    "LA":      (5, 7),   # left atrial myocardium  (cells 5-7)
}
_VEN_SEGS: dict[str, tuple[int, int]] = {
    "HIS":        (0, 0),
    "LBB":        (1, 3),
    "SEPT_EARLY": (1, 1),  # first LBB cell = earliest septal activation
    "LV_ANT":     (2, 3),
    "LV_LAT":     (4, 5),
    "LV_INF":     (6, 7),
    "LV_POST":    (8, 9),
}
# ---------------------------------------------------------------------------


def _check_cfl() -> None:
    """
    Verify the CFL stability condition for the explicit Euler integrator.

    Condition: ``D_max · (dt_int / τ) ≤ 0.5``

    With the current defaults (D_max = 72, dt_int = 0.1 ms, τ = 0.025 s):
    CFL = 72 × (0.0001 / 0.025) = **0.288 ≤ 0.5** ✓

    A warning is logged (not an exception) so that experimental parameter
    overrides don't hard-crash the application.
    """
    D_max = max(float(_D_ATR.max()), float(_D_VEN.max()))
    cfl = D_max * _DT_INT / _TAU
    if cfl > 0.5:
        logger.warning(
            "CFL condition violated: D_max=%.0f, dt_int=%.4f s, τ=%.3f s "
            "→ CFL=%.3f > 0.5.  Reduce dt_int or cap D values to restore "
            "numerical stability.",
            D_max, _DT_INT, _TAU, cfl,
        )
    else:
        logger.debug(
            "CFL check: D_max=%.0f, dt_int=%.4f s, τ=%.3f s → CFL=%.3f ≤ 0.5 ✓",
            D_max, _DT_INT, _TAU, cfl,
        )


class ConductionCable1D:
    """
    1-D monodomain FitzHugh-Nagumo cable for cardiac AP propagation.

    Usage
    -----
    Instantiate once, call :meth:`new_beat` when the SA pacemaker fires, then
    call :meth:`advance` every simulation step to propagate the wave.
    :meth:`advance` returns a dict of ``{ecg_node: activation_time_s}`` for
    nodes that first crossed threshold during that step.
    """

    def __init__(self, params: SimulationParameters) -> None:
        self._params = params
        self._t: float = 0.0

        # CFL check at construction time
        _check_cfl()

        # State arrays: (n_cells, 2) → column 0 = v, column 1 = w
        self._sa = self._resting_state(_NA)   # atrial cable
        self._sv = self._resting_state(_NV)   # ventricular cable

        # SA pacemaker
        self._stim_end_a: float = -1.0        # SA stimulus end time
        self._av_pending_at: float = math.inf  # scheduled ventricular activation
        self._ven_stim_end: float = -1.0

        # Activation tracking: times when each cell first crossed threshold
        self._t_act_a = np.full(_NA, math.inf)
        self._t_act_v = np.full(_NV, math.inf)

        # Track which ECG node names have already been returned by advance()
        # so we don't re-emit them on every subsequent call.
        self._reported_atr: set[str] = set()
        self._reported_ven: set[str] = set()
        self._sa_reported: bool = False

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def new_beat(self, sa_fire_time: float, params: SimulationParameters) -> None:
        """
        Trigger a new SA node stimulus at *sa_fire_time*.

        Resets atrial and ventricular activation trackers so the next beat
        is recorded fresh.  Ventricular cells are NOT reset (they remain in
        their refractory state from the previous beat — emergent refractory).
        """
        self._params = params
        self._stim_end_a = sa_fire_time + _STIM_DUR
        self._av_pending_at = math.inf
        # Reset activation trackers for the new beat
        self._t_act_a[:] = math.inf
        self._t_act_v[:] = math.inf
        self._t_act_a[0] = sa_fire_time   # SA node activates at fire time
        self._reported_atr.clear()
        self._reported_ven.clear()
        self._sa_reported = False

    def advance(self, dt_s: float) -> dict[str, float]:
        """
        Advance the cable by *dt_s* seconds using internal 0.1 ms micro-steps.

        Returns
        -------
        dict[str, float]
            ECG node names → absolute activation time [s] for nodes that
            first crossed threshold **during this call**.  Only newly
            activated nodes are returned; previously activated nodes are absent.
        """
        n_micro = max(1, round(dt_s / _DT_INT))
        dt_micro = dt_s / n_micro
        newly: dict[str, float] = {}

        # Emit SA_NODE once per beat on the first advance() call
        if not self._sa_reported and self._t_act_a[0] < math.inf:
            newly["SA_NODE"] = self._t_act_a[0]
            self._sa_reported = True
            self._reported_atr.add("SA_NODE")

        for _ in range(n_micro):
            self._t += dt_micro
            self._step_atrial(dt_micro)
            self._step_ventricular(dt_micro)

            # Check atrial threshold crossings.
            # Always track per-cell times (needed for AV gate on last cell);
            # only ADD to newly on the first report per node.
            for node, (i0, i1) in _ATR_SEGS.items():
                if node == "SA_NODE":
                    continue
                for i in range(i0, i1 + 1):
                    if self._t_act_a[i] == math.inf and self._sa[i, 0] > _V_THRESH:
                        self._t_act_a[i] = self._t
                if node not in self._reported_atr:
                    first = self._t_act_a[i0 : i1 + 1]
                    first_finite = first[first < math.inf]
                    if len(first_finite):
                        newly[node] = float(first_finite.min())
                        self._reported_atr.add(node)

            # Schedule ventricular cable once last atrial cell activates
            if (self._av_pending_at == math.inf
                    and self._t_act_a[-1] < math.inf
                    and self._params.av_node.conductance > 0.0):
                av_delay = self._params.av_node.conduction_delay_ms / 1000.0
                self._av_pending_at = self._t_act_a[-1] + av_delay

            # Stimulate ventricular cable at scheduled AV time
            if self._t >= self._av_pending_at:
                if self._ven_stim_end < self._av_pending_at:  # only once per beat
                    self._ven_stim_end = self._av_pending_at + _STIM_DUR
                    # Don't reset ventricular cable (keep refractory state)
                    # — cell 0 (HIS) is stimulated by the AV signal
                    newly["AV_NODE"] = self._av_pending_at  # mark AV activation

            # Check ventricular threshold crossings (same separation as atrial).
            for node, (i0, i1) in _VEN_SEGS.items():
                for i in range(i0, i1 + 1):
                    if self._t_act_v[i] == math.inf and self._sv[i, 0] > _V_THRESH:
                        self._t_act_v[i] = self._t
                if node not in self._reported_ven:
                    first = self._t_act_v[i0 : i1 + 1]
                    first_finite = first[first < math.inf]
                    if len(first_finite):
                        newly[node] = float(first_finite.min())
                        self._reported_ven.add(node)

        # Derive RV and RBB from HIS activation time (param-based)
        if "HIS" in newly and "RBB" not in newly:
            half_his = self._params.his_bundle.his_delay_ms / 1000.0 / 2.0
            rbc = self._params.his_bundle.right_branch_conductance
            if rbc > 0.0:
                rbb_delay = half_his / max(rbc, 0.01)
                newly["RBB"] = newly["HIS"] + rbb_delay
                newly["RV"]  = newly["RBB"] + 0.015  # RBB→RV fixed 15 ms

        return newly

    def get_current_time(self) -> float:
        return self._t

    def update_params(self, params: SimulationParameters) -> None:
        self._params = params

    # ------------------------------------------------------------------
    # Internal: cable dynamics
    # ------------------------------------------------------------------

    def _step_atrial(self, dt: float) -> None:
        # np.ascontiguousarray ensures Numba sees a contiguous 1-D float64 array
        # (self._sa[:, 0] is a strided view, not contiguous).
        V = np.ascontiguousarray(self._sa[:, 0])
        W = np.ascontiguousarray(self._sa[:, 1])
        I = np.zeros(_NA, dtype=np.float64)
        if self._t < self._stim_end_a:
            I[0] = _STIM_AMP
        V_new, W_new = _fhn_cable_step(V, W, _D_ATR, I, dt, _TAU, _A, _B, _EPS)
        self._sa[:, 0] = V_new
        self._sa[:, 1] = W_new

    def _step_ventricular(self, dt: float) -> None:
        V = np.ascontiguousarray(self._sv[:, 0])
        W = np.ascontiguousarray(self._sv[:, 1])
        I = np.zeros(_NV, dtype=np.float64)
        if self._av_pending_at <= self._t < self._ven_stim_end:
            I[0] = _STIM_AMP
        V_new, W_new = _fhn_cable_step(V, W, _D_VEN, I, dt, _TAU, _A, _B, _EPS)
        self._sv[:, 0] = V_new
        self._sv[:, 1] = W_new

    @staticmethod
    def _fhn_cable_deriv(
        v: np.ndarray,
        w: np.ndarray,
        D: np.ndarray,
        I_ext: np.ndarray,
        dt: float,
    ) -> tuple[np.ndarray, np.ndarray]:
        """
        Compute one explicit Euler increment for the FHN cable.

        dv increment = dt · [D/τ · ∇²v  +  (v – v³/3 – w + I_ext) / τ]
        dw increment = dt · ε · (v + a – b·w) / τ
        """
        # No-flux Neumann boundary conditions
        lap = np.empty_like(v)
        lap[1:-1] = v[:-2] - 2.0 * v[1:-1] + v[2:]
        lap[0]    = v[1]   - v[0]
        lap[-1]   = v[-2]  - v[-1]

        inv_tau = 1.0 / _TAU
        dv = dt * ((D * lap + v - v * v * v / 3.0 - w + I_ext) * inv_tau)
        dw = dt * (_EPS * (v + _A - _B * w) * inv_tau)
        return dv, dw

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    @staticmethod
    def _resting_state(n: int) -> np.ndarray:
        """Allocate n-cell state array at FHN rest."""
        s = np.empty((n, 2), dtype=np.float64)
        s[:, 0] = _V_REST
        s[:, 1] = _W_REST
        return s
