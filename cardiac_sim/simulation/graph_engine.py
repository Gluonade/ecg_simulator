"""
ConductionGraphEngine — Phase 1/2 simulation engine.

Produces a clinically recognisable 12-lead ECG from a discrete cardiac
conduction graph.  No PDEs or full ion-channel ODEs are required.

Algorithm (per heartbeat)
--------------------------
1. SA node fires when ``simulation_time ≥ next_sa_fire``.
2. Stateful plugins (e.g., Wenckebach) apply beat-specific parameter
   overrides via :meth:`~cardiac_sim.core.interfaces.AbstractStatefulPathologyPlugin.on_beat`.
3. BFS through the conduction graph computes every node's absolute
   activation time from the current SA fire.
4. The result is stored as a :class:`BeatRecord`.
5. At each call to :meth:`step`, the ECG is computed by summing
   :meth:`~cardiac_sim.core.conduction.node.ConductionNode.dipole_contribution`
   across all nodes in all currently active beats, then projecting the total
   dipole onto the 12 lead-field vectors.

If the ``ectopic_focus`` parameter is enabled, a second pacemaker fires
a ventricular ectopic beat at ``coupling_interval_ms`` after each SA fire,
producing a wide QRS without a preceding P wave.

Plugin stacking
---------------
Multiple plugins can be active simultaneously.  Static plugins are applied
once when added; their combined effect updates the cached graph.  Stateful
plugins are called per-beat and may override any parameter for that beat
only without altering the cached parameter set.

Thread safety
-------------
All mutable state is guarded by a :class:`threading.Lock`.
"""

from __future__ import annotations

import logging
import math
import threading
from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cardiac_sim.core.conduction.graph import ConductionGraph, build_physiological_graph
from cardiac_sim.core.ecg.axis_analyzer import CardiacAxisAnalyzer
from cardiac_sim.core.ecg.lead_field import LeadFieldForwardModel, LEAD_NAMES
from cardiac_sim.core.interfaces import (
    AbstractPathologyPlugin,
    AbstractStatefulPathologyPlugin,
    ECGSample,
    SimulationEngine,
    SimulationState,
)
from cardiac_sim.core.parameter_model import SimulationParameters

logger = logging.getLogger(__name__)

_NEG_INF = float("-inf")
_INF = float("inf")

# Beats older than this are excluded from ECG computation.
# Covers the longest T-wave tail (~700 ms at slow HR).
_BEAT_RETENTION_S = 0.85

# Fixed activation spread for ectopic ventricular beats [seconds].
# Mimics slow cell-to-cell spread without the Purkinje fast pathway.
_ECTOPIC_SPREAD: dict[str, float] = {
    "LV_LAT":  0.000,
    "LV_ANT":  0.040,
    "LV_INF":  0.050,
    "LV_POST": 0.055,
    "RV":      0.090,
}


@dataclass
class BeatRecord:
    """Activation times produced by one cardiac cycle."""

    beat_id: int
    sa_fire_time: float
    is_ectopic: bool = False
    activation_times: dict[str, float] = field(default_factory=dict)
    retrograde_nodes: frozenset[str] = field(default_factory=frozenset)
    """
    Nodes activated via working-myocardium retrograde edges (LBBB, RBBB,
    hemiblocks) or nodes transitively downstream of such a node.
    These receive broadened Gaussians and discordant T waves in ECG computation.
    """


class ConductionGraphEngine(SimulationEngine):
    """
    Phase 1/2 engine: discrete cardiac conduction graph with plugin stacking.

    Static vs. stateful plugins
    ---------------------------
    * **Static** (:class:`~cardiac_sim.core.interfaces.AbstractPathologyPlugin`):
      applied once via ``apply(params)``, result cached as ``_params`` and
      ``_graph``.  Examples: LBBB, RBBB, AV block III°, sinus bradycardia.

    * **Stateful** (:class:`~cardiac_sim.core.interfaces.AbstractStatefulPathologyPlugin`):
      called once per beat via ``on_beat(params, beat_id)``.  Receive the
      cached static-plugin params and may add beat-level overrides.
      The per-beat graph is rebuilt only for the duration of that beat.
      Examples: Wenckebach (AV block II° Mobitz I), Mobitz II.
    """

    def __init__(self) -> None:
        # Parameter layers
        self._base_params = SimulationParameters()   # raw user configuration
        self._params = SimulationParameters()         # base + static plugins

        # Plugin lists
        self._static_plugins: list[AbstractPathologyPlugin] = []
        self._stateful_plugins: list[AbstractStatefulPathologyPlugin] = []

        # Graph (built from _params; rebuilt on static-plugin change)
        self._graph: ConductionGraph | None = None
        self._forward_model = LeadFieldForwardModel()
        self._lock = threading.Lock()

        # Time tracking
        self._time: float = 0.0
        self._beat_id: int = 0
        self._next_sa_fire: float = 0.0          # first beat fires immediately
        self._next_ectopic_fire: float = _INF    # disabled until plugin adds it
        self._next_escape_fire: float = _INF     # junctional/ventricular escape

        # Beat history (rolling window; maxlen auto-prunes oldest)
        self._active_beats: deque[BeatRecord] = deque(maxlen=4)

        # Refractory tracking (updated after each beat)
        self._last_activation: dict[str, float] = {}

        # Ventricular activation tracking (for escape pacemaker)
        self._last_ventricular_time: float = _NEG_INF
        self._last_ventricular_rr: float = 0.857  # smoothed ventricular RR interval

        # AF state
        self._af_next_qrs: float = _INF
        self._af_last_qrs: float = _NEG_INF
        self._af_rng = np.random.default_rng()   # random seed each run

        # Smoothed HR display value
        self._last_rr: float = 0.857

        # Base cycle length last used to schedule _next_sa_fire.
        # Compared in _reschedule_pacemaker to detect actual CL changes and
        # avoid spurious rescheduling when non-CL parameters change.
        self._scheduled_cl_s: float = 0.857

        # QRS detection from ECG trace (clinical rate calculation)
        # ─────────────────────────────────────────────────────────
        # Stores recent ECG samples for QRS peak detection
        # Each entry: (timestamp, lead_II_voltage)
        self._ecg_buffer: deque[tuple[float, float]] = deque(maxlen=600)  # ~2 sec @ 300 Hz
        self._last_qrs_detected_time: float = _NEG_INF
        self._detected_qrs_rr: float = 0.857  # RR from detected peaks
        self._last_buffer_index_checked: int = 0  # avoid re-scanning old samples

        # Cardiac electrical axis analyzer
        # ─────────────────────────────────────────────────────────
        self._axis_analyzer = CardiacAxisAnalyzer(
            sample_rate_hz=500.0  # Will be updated in initialize()
        )
        self._axis_analysis_counter: int = 0
        self._axis_analysis_interval: int = 50  # Analyze every ~100 ms at 500 Hz

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
            self._graph = build_physiological_graph(params)
            self._time = 0.0
            self._beat_id = 0
            self._next_sa_fire = 0.0
            self._next_ectopic_fire = _INF
            self._next_escape_fire = _INF
            self._active_beats.clear()
            self._last_activation.clear()
            self._last_ventricular_time = _NEG_INF
            self._last_ventricular_rr = params.sa_node.cycle_length_ms / 1000.0
            self._af_next_qrs = _INF
            self._af_last_qrs = _NEG_INF
            self._last_rr = params.sa_node.cycle_length_ms / 1000.0
            self._scheduled_cl_s = params.sa_node.cycle_length_ms / 1000.0
            self._ecg_buffer.clear()
            self._last_qrs_detected_time = _NEG_INF
            self._detected_qrs_rr = params.sa_node.cycle_length_ms / 1000.0
            self._last_buffer_index_checked = 0
            
            # Reset cardiac axis analyzer
            self._axis_analyzer.reset()
            self._axis_analysis_counter = 0
            
        logger.info("ConductionGraphEngine initialised.")

    def update_base_parameters(self, params: SimulationParameters) -> None:
        """
        Update base parameters **without** clearing active plugins or
        resetting simulation state.

        Called when the user adjusts a parameter panel value while the
        simulation is running.  Plugins remain active; the graph is rebuilt
        to reflect the combined effect of new base params + existing plugins.
        """
        with self._lock:
            self._base_params = params.copy()
            self._rebuild_static_params()
        logger.debug("Base parameters updated.")

    def step(self, dt: float) -> ECGSample:
        with self._lock:
            if self._graph is None:
                self._graph = build_physiological_graph(self._params)

            self._time += dt

            if self._params.atrial_fib.enabled:
                # ── AF mode: suppress SA node, use irregular QRS ──────
                if self._af_next_qrs == _INF:
                    # First entry into AF: schedule first QRS
                    self._af_next_qrs = self._time + 0.3
                if self._time >= self._af_next_qrs:
                    self._fire_af_qrs(self._af_next_qrs)
            else:
                # ── Normal sinus mode ─────────────────────────────────
                if self._time >= self._next_sa_fire:
                    self._fire_sa_node(self._next_sa_fire)
                if self._time >= self._next_ectopic_fire:
                    self._fire_ectopic(self._next_ectopic_fire)
                # ── Escape pacemaker (e.g., after AV block III°) ──────
                if (self._params.escape_pacemaker.enabled
                        and self._time >= self._next_escape_fire):
                    self._fire_escape_beat(self._next_escape_fire)

            ecg = self._compute_ecg(self._time)
            
            # Store ECG sample for QRS detection (lead II is index 1)
            self._ecg_buffer.append((self._time, float(ecg[1])))
            
            # Feed ECG to axis analyzer (leads I=index 0, aVF=index 5)
            self._axis_analyzer.add_ecg_sample(
                self._time,
                float(ecg[0]),      # Lead I
                float(ecg[5]),      # Lead aVF
            )
            
            # Detect QRS peaks in the trace
            self._detect_qrs_peaks()
            
            # Perform cardiac axis analysis periodically
            self._axis_analysis_counter += 1
            if self._axis_analysis_counter >= self._axis_analysis_interval:
                self._axis_analysis_counter = 0
                # Analysis is performed, result will be used in get_state()

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
            
            # Perform cardiac axis analysis (called every get_state(), which emits ~10x/sec)
            axis_result = self._axis_analyzer.analyze()
            
            return SimulationState(
                time=self._time,
                heart_rate=hr,
                ventricular_rate=vr,
                cardiac_axis_degrees=axis_result.angle_degrees,
                cardiac_axis_classification=axis_result.classification,
                is_running=True,
            )

    def apply_pathology(self, plugin: AbstractPathologyPlugin) -> None:
        with self._lock:
            if isinstance(plugin, AbstractStatefulPathologyPlugin):
                plugin.reset()
                self._stateful_plugins.append(plugin)
                logger.info("Stateful pathology registered: %s", plugin.get_display_name())
            else:
                self._static_plugins.append(plugin)
                self._rebuild_static_params()
                logger.info("Static pathology applied: %s", plugin.get_display_name())

    def remove_pathology(self, plugin_name: str) -> None:
        with self._lock:
            before_static = len(self._static_plugins)
            self._static_plugins = [
                p for p in self._static_plugins
                if p.get_display_name() != plugin_name
            ]
            before_stateful = len(self._stateful_plugins)
            removed_stateful = [
                p for p in self._stateful_plugins
                if p.get_display_name() == plugin_name
            ]
            for p in removed_stateful:
                p.reset()
            self._stateful_plugins = [
                p for p in self._stateful_plugins
                if p.get_display_name() != plugin_name
            ]
            if (len(self._static_plugins) < before_static
                    or len(self._stateful_plugins) < before_stateful):
                self._rebuild_static_params()
                logger.info("Pathology removed: %s", plugin_name)
            else:
                logger.warning("remove_pathology: '%s' not found.", plugin_name)

    # ------------------------------------------------------------------
    # Plugin management helpers
    # ------------------------------------------------------------------

    def _rebuild_static_params(self) -> None:
        """
        Recompute ``_params`` = ``_base_params`` + all static plugins,
        then rebuild the cached conduction graph.

        Always calls :meth:`_reschedule_pacemaker` afterward so that a
        shorter cycle length (higher HR) takes effect immediately rather
        than waiting for the already-scheduled next SA fire.
        """
        p = self._base_params.copy()
        for plugin in self._static_plugins:
            p = plugin.apply(p)
        self._params = p
        self._graph = build_physiological_graph(p)
        self._reschedule_pacemaker()

    def _reschedule_pacemaker(self) -> None:
        """
        Reschedule ``_next_sa_fire`` immediately when the base cycle length
        changes, making both HR increase AND decrease take effect at once.

        Design
        ------
        * Updates ``_last_rr`` so the status-bar HR readout refreshes within
          the next ``state_changed`` emission (≈200 ms) without waiting for
          the SA node to actually fire.
        * Only reschedules when the base CL changed by > 1 ms (compares
          against ``_scheduled_cl_s``) to avoid spurious reschedules when
          the user adjusts non-CL parameters (AV delay, etc.).
        * For **HR increase** (shorter CL): advances ``_next_sa_fire`` so
          the next beat fires sooner than the old schedule.
        * For **HR decrease** (longer CL): pushes ``_next_sa_fire`` further
          out so the first long gap appears immediately rather than after
          the already-imminent old-schedule beat.
        * A minimum advance of 50 ms prevents two SA fires in the same batch.
        """
        new_cl_s = self._params.sa_node.cycle_length_ms / 1000.0
        if new_cl_s <= 0.0:
            return

        # Immediately update HR display without waiting for next SA fire
        self._last_rr = new_cl_s

        # Only reschedule if the base CL actually changed (> 1 ms)
        if abs(new_cl_s - self._scheduled_cl_s) <= 0.001:
            return

        self._scheduled_cl_s = new_cl_s
        self._next_sa_fire = self._time + max(new_cl_s, 0.050)

        # AF mode: reschedule next QRS if mean RR changed
        if self._params.atrial_fib.enabled and self._af_next_qrs < _INF:
            new_rr_s = self._params.atrial_fib.mean_rr_ms / 1000.0
            if new_rr_s > 0.0:
                self._af_next_qrs = self._time + max(new_rr_s * 0.5, 0.050)

    # ------------------------------------------------------------------
    # SA node pacemaker
    # ------------------------------------------------------------------

    def _fire_sa_node(self, t_fire: float) -> None:
        """Apply stateful plugins, compute beat activations, schedule next fire."""
        assert self._graph is not None

        # ── Apply stateful plugins for this specific beat ──────────────
        beat_params = self._params
        beat_graph = self._graph
        if self._stateful_plugins:
            p = self._params.copy()
            for plugin in self._stateful_plugins:
                p = plugin.on_beat(p, self._beat_id)
            beat_params = p
            beat_graph = build_physiological_graph(p)

        # ── Propagate activation through the graph ─────────────────────
        act_times, retro = beat_graph.compute_beat_activations(
            t_fire, self._last_activation
        )

        record = BeatRecord(
            beat_id=self._beat_id,
            sa_fire_time=t_fire,
            activation_times=act_times,
            retrograde_nodes=retro,
        )
        self._active_beats.append(record)
        self._last_activation.update(act_times)
        self._beat_id += 1

        # ── Track last ventricular activation for escape pacemaker ────
        _VENTRICULAR_NODES = {"SEPT_EARLY", "RV", "LV_ANT", "LV_LAT", "LV_INF", "LV_POST"}
        v_times = [t for n, t in act_times.items() if n in _VENTRICULAR_NODES]
        if v_times:
            current_ventricular_time = max(v_times)
            # Calculate ventricular RR interval
            if self._last_ventricular_time > _NEG_INF:
                vent_rr = current_ventricular_time - self._last_ventricular_time
                self._last_ventricular_rr = max(0.3, vent_rr)  # minimum 200 bpm, typically ~0.85 s
            self._last_ventricular_time = current_ventricular_time
            # Ventricles fired: push escape deadline forward
            esc_interval = beat_params.escape_pacemaker.escape_interval_ms / 1000.0
            self._next_escape_fire = self._last_ventricular_time + esc_interval
        elif beat_params.escape_pacemaker.enabled and self._next_escape_fire == _INF:
            # First beat with no ventricular activation: arm escape timer
            esc_interval = beat_params.escape_pacemaker.escape_interval_ms / 1000.0
            self._next_escape_fire = t_fire + esc_interval

        # ── Schedule next SA fire ──────────────────────────────────────
        cl = self._get_cycle_length(t_fire)
        self._last_rr = cl
        self._next_sa_fire = t_fire + cl

        # ── Schedule ectopic after this beat ──────────────────────────
        ectopic = beat_params.ectopic_focus
        if ectopic.enabled:
            self._next_ectopic_fire = t_fire + ectopic.coupling_interval_ms / 1000.0
        else:
            self._next_ectopic_fire = _INF

        logger.debug(
            "Beat %d  t=%.3f s  CL=%.0f ms  %d nodes  ectopic=%s",
            record.beat_id, t_fire, cl * 1000, len(act_times),
            f"{self._next_ectopic_fire:.3f}s" if ectopic.enabled else "off",
        )

    def _get_cycle_length(self, t: float) -> float:
        """
        Next RR interval with Phase 1 HRV modulation.

        Two sinusoidal oscillators:

        * **HF** (respiratory sinus arrhythmia) — from ``hrv.hf_amplitude_ms``
          and ``hrv.respiratory_rate_hz``.
        * **LF** (sympathovagal placeholder, fixed 0.10 Hz) — from
          ``hrv.lf_amplitude_ms``.

        Phase 6 will replace this with physiologically coupled oscillators
        and baroreflex feedback.
        """
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

    # ------------------------------------------------------------------
    # Ectopic pacemaker (VES)
    # ------------------------------------------------------------------

    def _fire_ectopic(self, t_fire: float) -> None:
        """
        Fire a ventricular ectopic beat.

        The ectopic beat bypasses the normal conduction system entirely:
        the focus fires directly from the ventricular myocardium, spreading
        cell-to-cell at working-myocardium velocity (~0.5 m/s).  This
        produces a wide QRS with no preceding P wave and, depending on
        timing, may be followed by a compensatory pause as ventricular
        nodes remain refractory when the next sinus beat arrives.
        """
        ectopic = self._params.ectopic_focus
        act_times = {
            node: t_fire + delay
            for node, delay in _ECTOPIC_SPREAD.items()
        }

        record = BeatRecord(
            beat_id=self._beat_id + 1_000_000,   # distinct ID range
            sa_fire_time=t_fire,
            is_ectopic=True,
            activation_times=act_times,
        )
        self._active_beats.append(record)
        self._last_activation.update(act_times)

        # Update ventricular RR tracking
        current_ventricular_time = t_fire
        if self._last_ventricular_time > _NEG_INF:
            vent_rr = current_ventricular_time - self._last_ventricular_time
            self._last_ventricular_rr = max(0.3, vent_rr)
        self._last_ventricular_time = current_ventricular_time

        # Schedule repeat or disable
        if ectopic.repetitive:
            self._next_ectopic_fire = t_fire + ectopic.coupling_interval_ms / 1000.0
        else:
            self._next_ectopic_fire = _INF

        logger.debug("Ectopic beat fired at t=%.3f s", t_fire)

    # ------------------------------------------------------------------
    # Escape pacemaker
    # ------------------------------------------------------------------

    def _fire_escape_beat(self, t_fire: float) -> None:
        """
        Fire a junctional escape beat from the His bundle.

        The beat starts at HIS and propagates through the normal ventricular
        conduction system, producing a narrow QRS (unless bundle branch block
        is also present) at the escape rate.
        """
        assert self._graph is not None
        origin = self._params.escape_pacemaker.origin
        if origin == "HIS":
            # Junctional escape (supra-Hisian block): narrow QRS
            act_times, retro = self._graph.compute_beat_activations(
                t_fire, self._last_activation, start_node="HIS"
            )
            record = BeatRecord(
                beat_id=self._beat_id + 2_000_000,
                sa_fire_time=t_fire,
                activation_times=act_times,
                retrograde_nodes=retro,
            )
        else:
            # Ventricular escape (infra-Hisian block): wide, bizarre QRS
            # Fires from LV_LAT via slow cell-to-cell spread – same model as VES
            act_times = {
                node: t_fire + delay for node, delay in _ECTOPIC_SPREAD.items()
            }
            record = BeatRecord(
                beat_id=self._beat_id + 2_000_000,
                sa_fire_time=t_fire,
                is_ectopic=True,   # → wide/bizarre waveforms in _compute_ecg
                activation_times=act_times,
                retrograde_nodes=frozenset(),
            )
        self._active_beats.append(record)
        self._last_activation.update(act_times)

        # Update ventricular tracking
        _V = {"SEPT_EARLY", "RV", "LV_ANT", "LV_LAT", "LV_INF", "LV_POST"}
        v_times = [t for n, t in act_times.items() if n in _V]
        if v_times:
            current_ventricular_time = max(v_times)
            # Calculate ventricular RR interval
            if self._last_ventricular_time > _NEG_INF:
                vent_rr = current_ventricular_time - self._last_ventricular_time
                self._last_ventricular_rr = max(0.3, vent_rr)  # minimum 200 bpm
            self._last_ventricular_time = current_ventricular_time

        # Schedule next escape
        esc_interval = self._params.escape_pacemaker.escape_interval_ms / 1000.0
        self._next_escape_fire = t_fire + esc_interval
        logger.debug("Escape beat fired at t=%.3f s", t_fire)

    # ------------------------------------------------------------------
    # AF pacemaker
    # ------------------------------------------------------------------

    def _fire_af_qrs(self, t_fire: float) -> None:
        """
        Fire one ventricular beat in AF mode.

        The beat starts at HIS (bypassing SA and AV nodes) and uses the
        normal His-Purkinje system, so QRS morphology correctly reflects
        any active bundle-branch block plugin.
        """
        assert self._graph is not None
        act_times, retro = self._graph.compute_beat_activations(
            t_fire, self._last_activation, start_node="HIS"
        )
        record = BeatRecord(
            beat_id=self._beat_id,
            sa_fire_time=t_fire,
            activation_times=act_times,
            retrograde_nodes=retro,
        )
        self._active_beats.append(record)
        self._last_activation.update(act_times)
        self._beat_id += 1

        # Update ventricular RR tracking
        _V = {"SEPT_EARLY", "RV", "LV_ANT", "LV_LAT", "LV_INF", "LV_POST"}
        v_times = [t for n, t in act_times.items() if n in _V]
        if v_times:
            current_ventricular_time = max(v_times)
            if self._last_ventricular_time > _NEG_INF:
                vent_rr = current_ventricular_time - self._last_ventricular_time
                self._last_ventricular_rr = max(0.3, vent_rr)
            self._last_ventricular_time = current_ventricular_time

        # Compute inter-beat interval: exponential distribution
        af = self._params.atrial_fib
        mean_rr = af.mean_rr_ms / 1000.0
        interval = float(self._af_rng.exponential(mean_rr))
        interval = max(0.300, interval)   # physiological minimum: AV refractory
        if self._af_last_qrs > _NEG_INF:
            self._last_rr = t_fire - self._af_last_qrs
        self._af_last_qrs = t_fire
        self._af_next_qrs = t_fire + interval
        logger.debug("AF QRS at t=%.3f s, next in %.0f ms", t_fire, interval * 1000)

    # ------------------------------------------------------------------
    # ECG computation
    # ------------------------------------------------------------------

    def _compute_ecg(self, t: float) -> np.ndarray:
        """
        Sum all active node dipole contributions and project to 12 leads.

        Morphological modulation
        -------------------------
        Nodes flagged as **retrograde** in a beat record (LBBB, RBBB,
        hemiblocks) and all nodes in **ectopic** beats (VES, ventricular
        escape) receive modified waveform parameters:

        * ``depol_width_factor = 2.5`` (retrograde) / ``3.0`` (ectopic) —
          broad, slurred QRS components from slow cell-to-cell spread.
        * ``repol_sign = -1.0`` — discordant T wave (T opposite to QRS in
          each lead), mandatory for all these conditions per ECG literature.
        * ``amplitude_factor = 1.5`` for retrograde RV (RBBB only) — boosts
          the terminal R' in V1 to physiologically realistic amplitude.

        Known Phase 2 limitations (deferred to Phase 3 with cell models)
        -----------------------------------------------------------------
        - M-shaped bifid R in lateral leads (LBBB): requires context-dependent
          dipole DIRECTIONS per node — not possible with fixed-direction model.
          TODO Phase 3: FitzHugh-Nagumo / ten Tusscher ODE cells will produce
          authentic action potential morphology and automatically correct this.
        - Positive V1 dominant R for LV-origin VES (RBBB-like): LV_LAT dipole
          is calibrated for sinus activation (leftward); ectopic activation
          travels rightward but no direction override is applied.
          TODO Phase 3: Cell model propagation handles this automatically.
        - Full ST discordance per-lead: requires per-lead ST offset computation.
          TODO Phase 3: Emerges naturally from APD heterogeneity in cell models.
        """
        assert self._graph is not None

        total_dipole = np.zeros(3, dtype=np.float64)
        cutoff = t - _BEAT_RETENTION_S

        for beat in self._active_beats:
            if beat.sa_fire_time < cutoff:
                continue

            for node_name, t_act in beat.activation_times.items():
                node = self._graph.nodes.get(node_name)
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

        # ── AF f-waves: superposition of sinusoids near atrial_rate_hz ──
        if self._params.atrial_fib.enabled:
            af = self._params.atrial_fib
            f = af.atrial_rate_hz
            amp = af.f_wave_amplitude_mv
            fw = (
                amp * math.sin(2.0 * math.pi * f * t)
                + amp * 0.65 * math.sin(2.0 * math.pi * f * 1.09 * t + 0.85)
                + amp * 0.45 * math.sin(2.0 * math.pi * f * 0.94 * t + 1.73)
                + amp * 0.30 * math.sin(2.0 * math.pi * f * 1.18 * t + 3.14)
            )
            total_dipole += np.array([0.45 * fw, 0.60 * fw, 0.0])

        return self._forward_model.project(total_dipole)


