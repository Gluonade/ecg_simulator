"""
ConductionCableEngine — Phase 3 simulation engine.

Replaces the discrete :class:`~cardiac_sim.simulation.graph_engine.ConductionGraphEngine`
with an engine that derives cardiac activation timing from actual action
potential propagation along a 1-D FitzHugh-Nagumo cable.

Improvements over Phase 1/2
----------------------------
* **Emergent refractory** — the cable's FHN cells naturally enter a
  refractory state after an AP.  No per-node ``refractory_period``
  parameter is consulted; re-excitation is blocked by the dynamics.
* **Wave-based atrial propagation** — P-wave duration arises from the
  time the activation front takes to cross the 7-cell atrial cable
  (~58 ms at default D_atrium).
* **Wave-based ventricular propagation** — QRS duration arises from the
  time for the LV cable to depolarise (~60 ms at default D_lv).

Architecture
------------
The engine wraps :class:`~cardiac_sim.core.tissue.cable_1d.ConductionCable1D`
for activation-time computation, then reuses the Phase 1/2 Gaussian-dipole
ECG model (:class:`~cardiac_sim.core.ecg.lead_field.LeadFieldForwardModel`
plus the node waveform parameters from
:func:`~cardiac_sim.core.conduction.graph._build_nodes`).

Phase 3-D will calibrate the cable parameters (D values, segment lengths)
to match the Phase 2 calibration targets precisely.

Pathology support
-----------------
For this phase, pathology handling is limited:

* **AV blocks (I, II, III)** — conductance/delay parameter respected by
  the AV-gate logic in the cable.
* **Sinus rate changes** — immediately effective (same reschedule logic
  as the graph engine).
* **Bundle branch blocks, hemiblocks** — not natively modelled by the
  cable.  If any bundle-branch plugin is active, the engine falls back to
  Phase 1/2 **graph-based** activation times for that beat so morphological
  changes remain visible.

Thread safety
-------------
All mutable state guarded by ``threading.Lock`` (identical pattern to
:class:`~cardiac_sim.simulation.graph_engine.ConductionGraphEngine`).
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cardiac_sim.core.conduction.graph import (
    _build_nodes,
    build_physiological_graph,
)
from cardiac_sim.core.ecg.lead_field import LeadFieldForwardModel
from cardiac_sim.core.interfaces import (
    AbstractPathologyPlugin,
    AbstractStatefulPathologyPlugin,
    ECGSample,
    SimulationEngine,
    SimulationState,
)
from cardiac_sim.core.parameter_model import SimulationParameters
from cardiac_sim.core.tissue.cable_1d import ConductionCable1D, _TAU
from cardiac_sim.simulation.graph_engine import BeatRecord

logger = logging.getLogger(__name__)

_NEG_INF = float("-inf")
_INF = float("inf")
_BEAT_RETENTION_S = 0.85

# Ventricular nodes whose Gaussian widths are adjusted for cable timing
_VENTRICULAR_NODES = frozenset(
    {"SEPT_EARLY", "RV", "LV_ANT", "LV_LAT", "LV_INF", "LV_POST"}
)


def _build_cable_nodes() -> dict:
    """
    Return dipole nodes with depol_duration adjusted for the cable engine.

    The Phase 1/2 graph nodes use depol_duration calibrated for a ~45 ms
    QRS activation spread.  The cable engine produces a ~70 ms spread, so
    ventricular depol_duration is widened proportionally (σ ≈ 17 ms) so that
    adjacent nodes (≈ 14 ms apart) contribute overlapping Gaussians that
    sum to a single smooth QRS complex rather than isolated narrow spikes.

    T-wave parameters (repol_delay, repol_duration, repol_amplitude) are kept
    at Phase 1/2 values so the QT interval remains physiologically correct.
    """
    nodes = _build_nodes()
    # depol_duration for ventricular nodes: ~40 ms → σ ≈ 17 ms
    # This covers the inter-node spacing (~14 ms at D_LV = 3.0, τ = 15 ms)
    # while still preserving subtle Q-R-S morphology between distant nodes.
    _DEPOL_DUR_VEN = 0.040   # seconds
    for name in _VENTRICULAR_NODES:
        if name in nodes:
            nodes[name].depol_duration = _DEPOL_DUR_VEN
    return nodes

# Nodes that, if retrograde (set by the cable), get widened Gaussians
_RETROGRADE_IF_LATE: frozenset[str] = frozenset(
    {"LV_ANT", "LV_LAT", "LV_INF", "LV_POST"}
)


class ConductionCableEngine(SimulationEngine):
    """
    Phase 3 engine: 1-D FHN cable for activation timing + dipole ECG.

    Drop-in replacement for
    :class:`~cardiac_sim.simulation.graph_engine.ConductionGraphEngine`.
    """

    def __init__(self) -> None:
        # Parameter layers (same plugin stacking as graph engine)
        self._base_params = SimulationParameters()
        self._params = SimulationParameters()
        self._static_plugins: list[AbstractPathologyPlugin] = []
        self._stateful_plugins: list[AbstractStatefulPathologyPlugin] = []

        # Forward model and dipole node parameters
        self._forward_model = LeadFieldForwardModel()
        self._nodes = _build_cable_nodes()   # calibrated for cable timing

        self._lock = threading.Lock()

        # Time state
        self._time: float = 0.0
        self._beat_id: int = 0
        self._next_sa_fire: float = 0.0
        self._next_ectopic_fire: float = _INF
        self._next_escape_fire: float = _INF

        # Cable
        self._cable: ConductionCable1D | None = None

        # Beat history
        self._active_beats: deque[BeatRecord] = deque(maxlen=4)
        self._last_activation: dict[str, float] = {}
        self._last_ventricular_time: float = _NEG_INF
        self._last_ventricular_rr: float = 0.857  # smoothed ventricular RR interval

        # HR tracking
        self._last_rr: float = 0.857
        self._scheduled_cl_s: float = 0.857

        # QRS detection from ECG trace (clinical rate calculation)
        # ─────────────────────────────────────────────────────────
        # Stores recent ECG samples for QRS peak detection
        # Each entry: (timestamp, lead_II_voltage)
        self._ecg_buffer: deque[tuple[float, float]] = deque(maxlen=600)  # ~2 sec @ 300 Hz
        self._last_qrs_detected_time: float = _NEG_INF
        self._detected_qrs_rr: float = 0.857  # RR from detected peaks
        self._last_buffer_index_checked: int = 0  # avoid re-scanning old samples

        # Beat accumulator: collects cable outputs over the current beat
        self._current_beat_nodes: dict[str, float] = {}
        self._beat_in_progress: bool = False

    # ------------------------------------------------------------------
    # SimulationEngine interface
    # ------------------------------------------------------------------

    def initialize(self, params: SimulationParameters) -> None:
        with self._lock:
            self._base_params = params.copy()
            self._static_plugins.clear()
            for p in self._stateful_plugins:
                p.reset()
            self._stateful_plugins.clear()
            self._params = params.copy()

            self._time = 0.0
            self._beat_id = 0
            self._next_sa_fire = 0.0
            self._next_ectopic_fire = _INF
            self._next_escape_fire = _INF
            self._active_beats.clear()
            self._last_activation.clear()
            self._last_ventricular_time = _NEG_INF
            self._last_ventricular_rr = params.sa_node.cycle_length_ms / 1000.0
            self._last_rr = params.sa_node.cycle_length_ms / 1000.0
            self._scheduled_cl_s = self._last_rr
            self._ecg_buffer.clear()
            self._last_qrs_detected_time = _NEG_INF
            self._detected_qrs_rr = params.sa_node.cycle_length_ms / 1000.0
            self._last_buffer_index_checked = 0

            self._cable = ConductionCable1D(params)
            self._current_beat_nodes.clear()
            self._beat_in_progress = False

        logger.info("ConductionCableEngine initialised.")

    def update_base_parameters(self, params: SimulationParameters) -> None:
        """Update base params without clearing plugins (same as graph engine)."""
        with self._lock:
            self._base_params = params.copy()
            self._rebuild_static_params()
        logger.debug("ConductionCableEngine: base params updated.")

    def step(self, dt: float) -> ECGSample:
        with self._lock:
            if self._cable is None:
                self._cable = ConductionCable1D(self._params)

            self._time += dt

            # ── SA pacemaker ─────────────────────────────────────────
            if self._time >= self._next_sa_fire:
                self._fire_sa(self._next_sa_fire)

            # ── Advance cable ALWAYS (keep internal clock synchronised) ─
            # If the cable is not advanced between beats, its internal _t
            # freezes at the commit time of the last beat.  When the next
            # beat starts and _t_act_v / _reported_ven are reset, the cells
            # still above threshold are instantly re-detected at the frozen
            # time, producing ghost activations with beat#0 timings inside
            # beat#1.  Advancing every step also ensures ventricular cells
            # repolarise naturally before the next beat.
            newly = self._cable.advance(dt)

            # ── Collect nodes only while a beat is in progress ───────
            if self._beat_in_progress:
                for node, t_act in newly.items():
                    if node not in self._current_beat_nodes:
                        self._current_beat_nodes[node] = t_act                        # KEY FIX: update the partial BeatRecord in place
                        # so _compute_ecg renders the QRS at activation time
                        # (not at commit time 770 ms later when Gaussians
                        # have decayed to zero).
                        if self._active_beats:
                            self._active_beats[-1].activation_times[node] = t_act
                # Commit beat when all ventricular nodes have activated, OR
                # when 90 % of the SA cycle length has elapsed — whichever
                # comes first.  Using 90 % of CL (not a fixed 0.5 s) ensures
                # all ventricular nodes (LV_POST ~645 ms at Phase 3-B timing)
                # are included before the next SA fire, regardless of HR.
                sa_fire = self._active_beats[-1].sa_fire_time if self._active_beats else 0.0
                cl_s = self._params.sa_node.cycle_length_ms / 1000.0
                ventr_done = all(
                    n in self._current_beat_nodes
                    for n in ("LV_LAT", "LV_POST", "RV")
                )
                time_limit = self._time - sa_fire > cl_s * 0.90
                if ventr_done or time_limit:
                    self._commit_current_beat(sa_fire)
                    self._beat_in_progress = False

            ecg = self._compute_ecg(self._time)
            
            # Store ECG sample for QRS detection (lead II is index 1)
            self._ecg_buffer.append((self._time, float(ecg[1])))
            
            # Detect QRS peaks in the trace
            self._detect_qrs_peaks()

        return ECGSample(timestamp=self._time, leads=ecg)

    def _detect_qrs_peaks(self) -> None:
        """
        Detect QRS complexes from the actual ECG trace (lead II).

        This mimics real clinical ECG monitors: count voltage peaks above a
        threshold, regardless of the electrophysiological mechanism generating
        them. This correctly handles:
        - Normal sinus: QRS rate = SA rate
        - AV block with escape: QRS rate = escape rate (independent of SA)
        - AV block without escape: QRS rate = 0 (no peaks detected)
        - Atrial fibrillation: QRS rate = detected F-R intervals
        - Ectopics and PVCs: counted as QRS peaks
        """
        if len(self._ecg_buffer) < 3:
            return

        # Detect QRS peaks in lead II using local-maximum method
        # A true QRS is a sharply peaked high-voltage deflection.
        # Adult ECG: QRS amplitude typically 0.5-2.0 mV. Set threshold high
        # to exclude noise/baseline while catching real complexes.
        QRS_THRESHOLD_MV = 1.0  # Real QRS minimum (exclude noise below 1 mV)
        MIN_QRS_INTERVAL_S = 0.200  # Physiological minimum: 300 bpm
        RATE_TIMEOUT_S = 4.5  # If no peak detected for this long, rate = 0
                              # Handles slow escape rates (~20 bpm = 3s intervals) without flickering
        
        times = list(self._ecg_buffer)
        voltages = [v for _, v in times]
        times = [t for t, _ in times]
        
        # Only check new samples added since last call to avoid re-detection
        buffer_len = len(voltages)
        start_idx = max(1, self._last_buffer_index_checked - 2)  # overlap by 2 for edge detection
        end_idx = max(2, buffer_len - 1)  # must have neighbors for peak detection
        
        found_peak = False
        
        # Scan for new peaks in the recently-added portion of buffer
        for i in range(start_idx, end_idx):
            # Local maximum: higher than neighbors AND above threshold
            if (voltages[i] > voltages[i-1] and 
                voltages[i] > voltages[i+1] and 
                voltages[i] > QRS_THRESHOLD_MV):
                
                peak_time = times[i]
                
                # Check minimum interval constraint (avoid double-detection)
                if peak_time - self._last_qrs_detected_time >= MIN_QRS_INTERVAL_S:
                    # Valid QRS detected
                    if self._last_qrs_detected_time > _NEG_INF:
                        # Calculate RR interval from detected peaks
                        vent_rr = peak_time - self._last_qrs_detected_time
                        self._detected_qrs_rr = max(0.3, vent_rr)  # min 200 bpm
                    
                    self._last_qrs_detected_time = peak_time
                    found_peak = True
        
        # Remember where we scanned up to
        self._last_buffer_index_checked = end_idx
        
        # Timeout logic: if no QRS detected for too long, rate = 0 (cardiac arrest)
        # This handles cases like AV block III without escape
        if not found_peak:
            time_since_last_qrs = self._time - self._last_qrs_detected_time
            if time_since_last_qrs > RATE_TIMEOUT_S:
                # No QRS complexes detected for > 1.5 seconds → rate = 0
                self._detected_qrs_rr = float("inf")  # infinity converts to 0 bpm

    def get_state(self) -> SimulationState:
        with self._lock:
            hr = 60.0 / self._last_rr if self._last_rr > 0.0 else 0.0
            # Use detected QRS rate (from actual ECG peaks) rather than mechanism-based rate
            vr = 60.0 / self._detected_qrs_rr if self._detected_qrs_rr > 0.0 else 0.0
            return SimulationState(time=self._time, heart_rate=hr, ventricular_rate=vr, is_running=True)

    def apply_pathology(self, plugin: AbstractPathologyPlugin) -> None:
        with self._lock:
            if isinstance(plugin, AbstractStatefulPathologyPlugin):
                plugin.reset()
                self._stateful_plugins.append(plugin)
            else:
                self._static_plugins.append(plugin)
                self._rebuild_static_params()
        logger.info("CableEngine: pathology applied: %s", plugin.get_display_name())

    def remove_pathology(self, plugin_name: str) -> None:
        with self._lock:
            self._static_plugins = [
                p for p in self._static_plugins
                if p.get_display_name() != plugin_name
            ]
            removed = [
                p for p in self._stateful_plugins
                if p.get_display_name() == plugin_name
            ]
            for p in removed:
                p.reset()
            self._stateful_plugins = [
                p for p in self._stateful_plugins
                if p.get_display_name() != plugin_name
            ]
            self._rebuild_static_params()

    # ------------------------------------------------------------------
    # Plugin helpers (identical pattern to graph engine)
    # ------------------------------------------------------------------

    def _rebuild_static_params(self) -> None:
        p = self._base_params.copy()
        for plugin in self._static_plugins:
            p = plugin.apply(p)
        self._params = p
        if self._cable is not None:
            self._cable.update_params(p)
        self._reschedule_pacemaker()

    def _reschedule_pacemaker(self) -> None:
        new_cl = self._params.sa_node.cycle_length_ms / 1000.0
        if new_cl <= 0.0:
            return
        self._last_rr = new_cl
        if abs(new_cl - self._scheduled_cl_s) > 0.001:
            self._scheduled_cl_s = new_cl
            self._next_sa_fire = self._time + max(new_cl, 0.050)

    # ------------------------------------------------------------------
    # SA pacemaker
    # ------------------------------------------------------------------

    def _fire_sa(self, t_fire: float) -> None:
        """Start a new beat: notify cable, apply stateful plugins."""
        assert self._cable is not None

        # Apply stateful plugins (Wenckebach, Mobitz II)
        beat_params = self._params
        if self._stateful_plugins:
            p = self._params.copy()
            for plugin in self._stateful_plugins:
                p = plugin.on_beat(p, self._beat_id)
            beat_params = p
            self._cable.update_params(p)

        # Trigger cable (SA node stimulus)
        self._cable.new_beat(t_fire, beat_params)

        # Start accumulating nodes for this beat
        self._current_beat_nodes = {"SA_NODE": t_fire}
        self._beat_in_progress = True

        # Create a partial BeatRecord now (will be completed later)
        record = BeatRecord(
            beat_id=self._beat_id,
            sa_fire_time=t_fire,
            activation_times={"SA_NODE": t_fire},
            retrograde_nodes=frozenset(),
        )
        self._active_beats.append(record)
        self._beat_id += 1

        # Schedule next SA fire
        cl = self._get_cycle_length(t_fire)
        self._last_rr = cl
        self._scheduled_cl_s = cl
        self._next_sa_fire = t_fire + cl

        logger.debug("CableEngine: beat %d at t=%.3f s", self._beat_id - 1, t_fire)

    def _get_cycle_length(self, t: float) -> float:
        p = self._params
        cl = p.sa_node.cycle_length_ms / 1000.0
        if p.hrv.enabled:
            hf = (p.hrv.hf_amplitude_ms / 1000.0) * math.sin(
                2.0 * math.pi * p.hrv.respiratory_rate_hz * t
            )
            lf = (p.hrv.lf_amplitude_ms / 1000.0) * math.sin(
                2.0 * math.pi * 0.10 * t
            )
            cl += hf + lf
        return max(0.300, cl)

    def _commit_current_beat(self, sa_fire_time: float) -> None:
        """
        Finalise the current beat record with all collected activation times.

        Detects retrograde nodes: ventricular nodes that activate
        significantly later than the HIS + expected Purkinje time are
        marked retrograde (wide Gaussian, discordant T).
        """
        act = dict(self._current_beat_nodes)

        # Identify retrograde ventricular nodes (LBBB / RBBB indicators)
        retro: set[str] = set()
        if "HIS" in act:
            t_his = act["HIS"]
            expected_lv_fast = t_his + 0.040  # 40 ms after HIS = normal fast path
            for n in _RETROGRADE_IF_LATE:
                if n in act and act[n] > expected_lv_fast + 0.060:
                    retro.add(n)

        # Update the last active BeatRecord in-place
        if self._active_beats:
            last = self._active_beats[-1]
            if last.sa_fire_time == sa_fire_time:
                # Replace with completed record
                self._active_beats[-1] = BeatRecord(
                    beat_id=last.beat_id,
                    sa_fire_time=sa_fire_time,
                    activation_times=act,
                    retrograde_nodes=frozenset(retro),
                )

        self._last_activation.update(act)

        # Track last ventricular activation for escape pacemaker
        _V = {"SEPT_EARLY", "RV", "LV_ANT", "LV_LAT", "LV_INF", "LV_POST"}
        v_times = [t for n, t in act.items() if n in _V]
        if v_times:
            current_ventricular_time = max(v_times)
            # Calculate ventricular RR interval
            if self._last_ventricular_time > float('-inf'):
                vent_rr = current_ventricular_time - self._last_ventricular_time
                self._last_ventricular_rr = max(0.3, vent_rr)  # minimum 200 bpm
            self._last_ventricular_time = current_ventricular_time

    # ------------------------------------------------------------------
    # ECG computation (identical to graph engine)
    # ------------------------------------------------------------------

    def _compute_ecg(self, t: float) -> np.ndarray:
        total_dipole = np.zeros(3, dtype=np.float64)
        cutoff = t - _BEAT_RETENTION_S

        for beat in self._active_beats:
            if beat.sa_fire_time < cutoff:
                continue
            for node_name, t_act in beat.activation_times.items():
                node = self._nodes.get(node_name)
                if node is None:
                    continue
                is_retro = node_name in beat.retrograde_nodes
                if beat.is_ectopic or is_retro:
                    width = 3.0 if beat.is_ectopic else 2.5
                    amp = 1.5 if (is_retro and node_name == "RV") else 1.0
                    total_dipole += node.dipole_contribution(
                        t, t_act,
                        depol_width_factor=width,
                        repol_sign=-1.0,
                        amplitude_factor=amp,
                    )
                else:
                    total_dipole += node.dipole_contribution(t, t_act)

        return self._forward_model.project(total_dipole)
