"""
ECG Interval Measurement Module.

Analyzes the ECG signal to measure diagnostic intervals:
- **PR interval (PQ interval)**: From P wave onset to QRS onset [ms]
- **QRS duration**: From QRS onset to QRS offset [ms]
- **QT interval**: From QRS onset to T wave offset [ms]

Algorithm
---------
Global (multi-lead) delineation on a rolling 12-lead buffer:

1. Maintain a rolling buffer of 12-lead ECG frames.
2. Detect QRS complexes on lead II (high-amplitude *and* steep-slope
   deflections, so large but gentle T waves — e.g. discordant T in LBBB — are
   not mistaken for QRS peaks) for beat timing.
3. Measure a **completed** beat — the most recent QRS that already has its
   full trailing waveform (T wave) inside the buffer — rather than the newest
   peak, whose T wave has not been recorded yet. This is what prevents the QT
   value from flickering to ``None`` for most of every cardiac cycle.
4. Locate QRS onset/offset from the **global spatial-velocity envelope** — the
   root-sum-square of the per-lead derivatives — rather than a single lead's
   amplitude. The QRS is the burst of high spatial velocity; the slower T wave
   is excluded. Pooling all leads means the true QRS span is found even when a
   component is tiny in lead II (e.g. the RBBB terminal slur in V1/V6/I) or the
   complex is wide/notched/monophasic (LBBB). This is the standard basis for
   global QRS duration (earliest onset to latest offset across leads).
5. PR: on lead II, search back from the global QRS onset for the P-wave onset
   (suppressed when no organised P wave is expected, e.g. atrial fibrillation).
6. QT: from the global QRS onset to the T-wave offset on lead II (return toward
   baseline after the T peak), bounded so the search never runs into the
   following QRS.

Measurement Reliability
-----------------------
Each interval is reported as a numeric value (ms) when measurable, and is
**held** at its last valid value across brief gaps (a few beats) so the
display does not flicker; it falls back to ``None`` ("not measurable") only
after sustained signal loss (e.g. asystole) or when a feature genuinely
cannot be identified.

Clinical Context
----------------
Normal intervals (at rest, HR ≈ 60 bpm):
- PR interval: 120-200 ms
- QRS duration: 80-120 ms
- QT interval: 400-450 ms (varies with heart rate)

References
----------
- Goldberger AL, Goldberger ZD, Shvilkin A. Goldberger's Clinical Electrocardiography. 9th ed.
- Macfarlane PW, Lawrie TDV. Comprehensive Electrocardiology. Pergamon Press.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class IntervalMeasurementResult:
    """Result of ECG interval measurement."""

    pr_interval_ms: Optional[float]
    """PR interval in milliseconds. None if not measurable."""

    qrs_duration_ms: Optional[float]
    """QRS duration in milliseconds. None if not measurable."""

    qt_interval_ms: Optional[float]
    """QT interval in milliseconds. None if not measurable."""

    is_valid: bool
    """True if at least one interval could be reliably measured."""


class ECGIntervalAnalyzer:
    """
    Analyzes ECG intervals from lead II signal.

    Measures PR interval, QRS duration, and QT interval from a rolling ECG
    buffer. Measurements are taken from a *completed* beat and held across
    brief gaps so the reported values stay stable between beats.

    Thread Safety
    -------------
    This class is NOT thread-safe. Call it only from the simulation thread.
    """

    # Detection thresholds (optimized for adult ECG at 500 Hz)
    _QRS_THRESHOLD_MV = 0.8
    """Minimum voltage for QRS complex detection [mV]."""

    _QRS_SLOPE_FRACTION = 0.25
    """A QRS peak must have a local slope ≥ this fraction of the buffer's
    steepest slope. Rejects large but gentle deflections (e.g. discordant
    T waves in LBBB, fibrillatory waves) that clear the amplitude threshold."""

    _P_WAVE_MIN_MV = 0.05
    """Minimum P wave amplitude [mV]."""

    _T_WAVE_MIN_MV = 0.05
    """Minimum T wave amplitude [mV]."""

    _BUFFER_DURATION_S = 3.0
    """Duration of rolling buffer to maintain [s]."""

    _MIN_QRS_INTERVAL_MS = 200
    """Minimum physiological RR interval [ms]. Corresponds to ~300 bpm."""

    _HOLD_MIN_S = 1.5
    """Minimum time to hold the last valid value across measurement gaps [s]."""

    _HOLD_MAX_S = 3.5
    """Maximum hold time; beyond this a stale value falls back to None [s]."""

    _LEAD_II = 1
    """Column index of lead II in a 12-lead frame (I=0, II=1, III=2, aVR=3,
    aVL=4, aVF=5, V1..V6=6..11). Used for beat timing, P and T waves."""

    def __init__(self, sample_rate_hz: float = 500.0):
        """
        Initialize the ECG interval analyzer.

        Parameters
        ----------
        sample_rate_hz
            Sampling rate of ECG data [Hz]. Default 500 Hz.
        """
        self.sample_rate_hz = sample_rate_hz
        self._ms_per_sample = 1000.0 / sample_rate_hz

        # Calculate buffer size
        buffer_size = max(300, int(self._BUFFER_DURATION_S * sample_rate_hz))

        # Rolling buffer: (timestamp, 12-lead voltage frame).
        # Lead II drives beat/P/T detection; all leads drive QRS delineation.
        self._ecg_buffer: deque[tuple[float, np.ndarray]] = deque(maxlen=buffer_size)

        # Track last detected QRS to avoid re-detection
        self._last_qrs_time: float = -np.inf
        self._last_qrs_index: int = -1

        # Cached result
        self._last_result: Optional[IntervalMeasurementResult] = None

        # Persistence: last valid value + the buffer time it was measured at,
        # per interval. Used to hold a reading across brief gaps so the display
        # does not flicker to None between/within beats.
        self._valid: dict[str, tuple[float, float]] = {}

        # Last known RR interval [s], used to scale the hold timeout and to
        # bound persistence when no QRS is currently detected.
        self._last_rr_s: float = 0.857

    def add_ecg_sample(self, timestamp: float, leads: np.ndarray) -> None:
        """
        Add one 12-lead ECG frame to the rolling buffer.

        Parameters
        ----------
        timestamp
            Simulation time [s].
        leads
            12-lead ECG voltage vector [mV], shape ``(12,)`` in the standard
            order (I, II, III, aVR, aVL, aVF, V1..V6). QRS onset/offset are
            delineated globally across all leads; P/T waves use lead II.
        """
        self._ecg_buffer.append((timestamp, np.asarray(leads, dtype=np.float64)))

    def analyze(self, p_wave_expected: bool = True) -> IntervalMeasurementResult:
        """
        Perform interval measurement on the current ECG buffer.

        Should be called regularly (e.g., every ~200 ms or 10× per second).

        Parameters
        ----------
        p_wave_expected
            Whether an organised P wave precedes each QRS. Set ``False`` for
            rhythms without discrete P waves (e.g. atrial fibrillation) so the
            PR interval is reported as ``None`` instead of latching onto a
            fibrillatory wave.

        Returns
        -------
        IntervalMeasurementResult
            Contains PR interval, QRS duration, QT interval (or None if not measurable).
        """
        if len(self._ecg_buffer) < 50:
            return self._finalize(None, None, None)  # not enough data

        # Convert buffer to arrays
        times = np.array([t for t, _ in self._ecg_buffer], dtype=np.float64)
        frames = np.array([f for _, f in self._ecg_buffer], dtype=np.float64)
        if frames.ndim == 1:  # single-lead fallback (defensive)
            frames = frames[:, None]
        lead_ii = frames[:, self._LEAD_II] if frames.shape[1] > self._LEAD_II else frames[:, 0]
        now = float(times[-1])

        # Global QRS delineation signal: spatial velocity = root-sum-square of
        # per-lead derivatives. A depolarisation force that is small in lead II
        # (e.g. RBBB terminal slur, best seen in V1/V6/I) still raises this
        # envelope, so the QRS onset/offset span the *global* complex.
        deriv = np.gradient(frames, axis=0)
        global_slope = self._smooth_signal(
            np.sqrt(np.sum(deriv * deriv, axis=1)), 5
        )

        # Detect QRS complexes (beat timing) on lead II
        qrs_indices = self._detect_qrs_peaks(lead_ii)

        if not qrs_indices:
            # No QRS in buffer → only persistence can carry a recent value.
            return self._finalize(None, None, None, now=now)

        # Update RR estimate from detected peaks (median of recent intervals).
        if len(qrs_indices) >= 2:
            rr_diffs = np.diff(times[np.asarray(qrs_indices)])
            if len(rr_diffs):
                self._last_rr_s = float(np.median(rr_diffs))

        # ── Fix 1: measure a *completed* beat ─────────────────────────────
        # The newest peak's T wave is not in the buffer yet, so QT/QRS-offset
        # cannot be measured from it. Use the second-to-last peak, which always
        # has a full inter-beat trailing window; bound forward searches by the
        # following (newest) peak so they never cross into the next complex.
        if len(qrs_indices) >= 2:
            meas_idx = qrs_indices[-2]
            next_idx: Optional[int] = qrs_indices[-1]
        else:
            meas_idx = qrs_indices[-1]
            next_idx = None
        self._last_qrs_index = meas_idx

        # Global QRS onset/offset from the multi-lead spatial-velocity envelope
        # (morphology-robust and captures terminal forces invisible in lead II).
        onset_idx, offset_idx = self._detect_qrs_bounds(global_slope, meas_idx, next_idx)

        pr_interval = (
            self._measure_pr_interval(lead_ii, onset_idx)
            if p_wave_expected else None
        )
        qrs_duration = self._measure_qrs_duration(onset_idx, offset_idx)
        qt_interval = self._measure_qt_interval(lead_ii, onset_idx, offset_idx, next_idx)

        logger.debug(
            "Beat idx=%d onset=%d offset=%d → PR=%s QRS=%s QT=%s",
            meas_idx, onset_idx, offset_idx, pr_interval, qrs_duration, qt_interval,
        )

        return self._finalize(pr_interval, qrs_duration, qt_interval, now=now)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _hold_seconds(self) -> float:
        """Adaptive hold window: ~2.5 RR, clamped to [HOLD_MIN, HOLD_MAX]."""
        return float(np.clip(2.5 * self._last_rr_s, self._HOLD_MIN_S, self._HOLD_MAX_S))

    def _persist(self, name: str, value: Optional[float], now: Optional[float]) -> Optional[float]:
        """
        Return *value* if measured this call; otherwise the last valid value
        while it is still within the hold window; otherwise None.
        """
        if value is not None and now is not None:
            self._valid[name] = (value, now)
            return value
        if now is not None:
            stored = self._valid.get(name)
            if stored is not None and (now - stored[1]) <= self._hold_seconds():
                return stored[0]
        return None

    def _finalize(
        self,
        pr: Optional[float],
        qrs: Optional[float],
        qt: Optional[float],
        now: Optional[float] = None,
    ) -> IntervalMeasurementResult:
        """Apply persistence, build/cache the result."""
        pr = self._persist("pr", pr, now)
        qrs = self._persist("qrs", qrs, now)
        qt = self._persist("qt", qt, now)
        result = IntervalMeasurementResult(
            pr_interval_ms=pr,
            qrs_duration_ms=qrs,
            qt_interval_ms=qt,
            is_valid=any(x is not None for x in (pr, qrs, qt)),
        )
        self._last_result = result
        return result

    # ------------------------------------------------------------------
    # QRS detection
    # ------------------------------------------------------------------

    def _detect_qrs_peaks(self, voltages: np.ndarray) -> list[int]:
        """
        Detect QRS peak indices in the voltage array.

        A candidate is a local maximum in |voltage| that clears the amplitude
        threshold AND has a steep local slope (≥ ``_QRS_SLOPE_FRACTION`` of the
        buffer's steepest slope). The slope gate rejects large-but-gentle
        deflections such as discordant T waves (LBBB) and fibrillatory waves.
        A minimum-interval filter merges ripples within the same complex.
        """
        qrs_indices: list[int] = []
        n = len(voltages)
        if n < 3:
            return qrs_indices

        dv = np.abs(np.diff(voltages))
        max_slope = float(np.max(dv)) if len(dv) else 0.0
        slope_thr = self._QRS_SLOPE_FRACTION * max_slope
        slope_win = max(1, int(0.020 * self.sample_rate_hz))  # ±20 ms

        for i in range(1, n - 1):
            abs_v = abs(voltages[i])
            if abs_v <= self._QRS_THRESHOLD_MV:
                continue
            if not (abs_v > abs(voltages[i - 1]) and abs_v > abs(voltages[i + 1])):
                continue
            # Slope gate: steepest change in a small window around the peak.
            lo = max(0, i - slope_win)
            hi = min(len(dv), i + slope_win)
            local_slope = float(np.max(dv[lo:hi])) if hi > lo else 0.0
            if max_slope > 0.0 and local_slope < slope_thr:
                continue  # too gentle → not a QRS (likely T wave / f-wave)

            if not qrs_indices:
                qrs_indices.append(i)
            else:
                time_since_last = (i - qrs_indices[-1]) * self._ms_per_sample
                if time_since_last > self._MIN_QRS_INTERVAL_MS:
                    qrs_indices.append(i)
                elif abs_v > abs(voltages[qrs_indices[-1]]):
                    qrs_indices[-1] = i  # higher peak on same complex

        return qrs_indices

    def _detect_qrs_bounds(
        self, global_slope: np.ndarray, peak_idx: int, next_idx: Optional[int]
    ) -> tuple[int, int]:
        """
        Locate global QRS onset and offset from the spatial-velocity envelope.

        *global_slope* is the root-sum-square of per-lead derivatives (see
        :meth:`analyze`). The QRS is the contiguous burst of high spatial
        velocity; onset/offset are where the envelope drops and *stays* below a
        threshold (a fraction of the burst peak). Because it is slope-based and
        pooled across all 12 leads, wide/slurred/monophasic complexes (LBBB) and
        terminal forces small in lead II (RBBB) are both delineated correctly,
        while the slower T wave is excluded.

        The scan is anchored at the *maximum* of the envelope within the window
        (a steep QRS stroke), guaranteeing the anchor sits inside the complex.

        Returns ``(onset_idx, offset_idx)`` as global buffer indices.
        """
        fs = self.sample_rate_hz
        pre = int(0.160 * fs)   # look up to 160 ms before the peak
        post = int(0.240 * fs)  # and up to 240 ms after (BBB terminal forces)
        lo = max(0, peak_idx - pre)
        hi = min(len(global_slope), peak_idx + post)
        if next_idx is not None:
            hi = min(hi, next_idx - int(0.040 * fs))  # stay clear of next QRS
        if hi - lo < 5:
            return peak_idx, peak_idx

        env = global_slope[lo:hi]
        # Anchor on the steepest stroke in the window, not the R peak (where
        # slope ≈ 0), so the outward scan starts firmly inside the QRS.
        p = int(np.argmax(env))

        env_max = float(env[p])
        if env_max <= 0.0:
            return peak_idx, peak_idx
        # Noise floor from the quietest fifth of the window (iso-electric baseline).
        noise = float(np.median(np.sort(env)[: max(3, len(env) // 5)]))
        # 0.30 of the burst peak: low enough to span slurred wide complexes
        # (LBBB ≈ 155 ms) yet high enough not to over-extend the onset into the
        # slow initial forces. (Terminal forces that stay below this — e.g. a
        # weakly-rendered RBBB R' — will be under-measured; that is a waveform/
        # model-calibration limit, not a delineation-threshold one.)
        thr = max(0.30 * env_max, 3.0 * noise)
        bridge = max(1, int(0.045 * fs))   # merge deflections < 45 ms apart
        merge_gate = 0.35 * env_max        # bridged deflection must be QRS-strength
        merge_min_dur = max(2, int(0.008 * fs))  # ...and a real deflection, ≥ 8 ms

        # Split the above-threshold samples into contiguous runs, then, starting
        # from the anchor run, merge neighbouring runs across short gaps — but
        # only if the neighbour is itself a strong, sustained (QRS-strength)
        # deflection. This bridges an intra-QRS notch (RSR' in RBBB, M-shaped
        # LBBB) without swallowing the slower, weaker T wave, whose spatial
        # velocity is lower and appears only as brief sub-threshold blips.
        active = np.flatnonzero(env >= thr)
        if len(active) == 0:
            return peak_idx, peak_idx

        runs: list[tuple[int, int]] = []
        s = prev = int(active[0])
        for k in active[1:]:
            k = int(k)
            if k == prev + 1:
                prev = k
            else:
                runs.append((s, prev))
                s = prev = k
        runs.append((s, prev))

        def _bridgeable(run: tuple[int, int], gap: int) -> bool:
            return (
                gap <= bridge
                and (run[1] - run[0] + 1) >= merge_min_dur
                and float(np.max(env[run[0]:run[1] + 1])) >= merge_gate
            )

        # Anchor run (contains the steepest stroke p).
        ai = next((i for i, (a, b) in enumerate(runs) if a <= p <= b), 0)
        lo_run = hi_run = ai
        while hi_run + 1 < len(runs) and _bridgeable(
            runs[hi_run + 1], runs[hi_run + 1][0] - runs[hi_run][1]
        ):
            hi_run += 1
        while lo_run - 1 >= 0 and _bridgeable(
            runs[lo_run - 1], runs[lo_run][0] - runs[lo_run - 1][1]
        ):
            lo_run -= 1
        onset_local, offset_local = runs[lo_run][0], runs[hi_run][1]

        onset_idx = lo + int(np.clip(onset_local, 0, len(env) - 1))
        offset_idx = lo + int(np.clip(offset_local, 0, len(env) - 1))
        if offset_idx <= onset_idx:
            offset_idx = min(len(global_slope) - 1, peak_idx + int(0.040 * fs))
        return onset_idx, offset_idx

    # ------------------------------------------------------------------
    # Interval measurements (onset/offset supplied by _detect_qrs_bounds)
    # ------------------------------------------------------------------

    def _measure_pr_interval(
        self, voltages: np.ndarray, qrs_onset_idx: int
    ) -> Optional[float]:
        """
        Measure PR interval (P-wave onset to QRS onset).

        Identifies the P wave as the dominant deflection in the physiological
        band 40-300 ms before the QRS onset, then walks back to its onset. This
        band excludes the QRS itself and the *peak* of the previous beat's T
        wave (which can be large and discordant, e.g. in LBBB), so the previous
        repolarisation is not mistaken for the P wave.

        Returns
        -------
        float or None
            PR interval in milliseconds, or None if not measurable.
        """
        fs = self.sample_rate_hz
        region_start = max(0, qrs_onset_idx - int(0.260 * fs))
        region_end = qrs_onset_idx - int(0.050 * fs)  # skip the PR segment
        if region_end - region_start < 10:
            return None

        region = self._smooth_signal(voltages[region_start:region_end], 5)
        baseline = float(np.median(region))
        rel = np.abs(region - baseline)

        # The P wave is the deflection *closest* to the QRS. Picking the plain
        # maximum can latch onto the tail of the previous beat's (large,
        # discordant in LBBB) T wave earlier in the window; instead take the
        # last strong deflection before the QRS.
        p_amp_max = float(np.max(rel))
        if p_amp_max < self._P_WAVE_MIN_MV:
            return None
        strong = np.flatnonzero(rel >= max(self._P_WAVE_MIN_MV, 0.40 * p_amp_max))
        p_peak_local = int(strong[-1])
        p_amp = rel[p_peak_local]

        # P-wave onset: walk back from the P peak to where it emerges from baseline.
        onset_thr = max(0.20 * p_amp, 0.010)
        p_onset_local = 0
        for i in range(p_peak_local, -1, -1):
            if abs(rel[i]) <= onset_thr:
                p_onset_local = i
                break

        p_onset_idx = region_start + p_onset_local
        pr_interval_ms = (qrs_onset_idx - p_onset_idx) * self._ms_per_sample
        if 80 < pr_interval_ms < 300:
            return pr_interval_ms
        return None

    def _measure_qrs_duration(
        self, onset_idx: int, offset_idx: int
    ) -> Optional[float]:
        """
        QRS duration from the onset/offset located on the slope envelope.

        Sanity range covers normal (60-100 ms), hemiblocks (90-120 ms),
        bundle branch blocks (120-200 ms) and extreme cases (up to ~240 ms).
        """
        qrs_duration_ms = (offset_idx - onset_idx) * self._ms_per_sample
        if 40 < qrs_duration_ms <= 240:
            return qrs_duration_ms
        logger.debug("QRS duration rejected: %.1f ms", qrs_duration_ms)
        return None

    def _measure_qt_interval(
        self,
        voltages: np.ndarray,
        onset_idx: int,
        offset_idx: int,
        next_idx: Optional[int],
    ) -> Optional[float]:
        """
        Measure QT interval (QRS onset to T-wave offset).

        Finds the T-wave peak after the QRS offset, then the point where the
        signal returns toward baseline (within 10 % of the T-peak amplitude).
        The forward search is bounded by the next QRS so it cannot spill into
        the following complex.

        Returns
        -------
        float or None
            QT interval in milliseconds, or None if not measurable.
        """
        fs = self.sample_rate_hz
        search_start = offset_idx + int(0.040 * fs)  # skip the ST junction
        search_end = min(len(voltages), onset_idx + int(0.600 * fs))
        if next_idx is not None:
            search_end = min(search_end, next_idx - int(0.040 * fs))
        if search_end - search_start < int(0.050 * fs):
            return None

        seg = self._smooth_signal(voltages[search_start:search_end], 7)
        # Baseline from the tail of the window (TP segment, iso-electric).
        baseline = float(np.median(seg[-max(3, len(seg) // 8):]))
        rel = seg - baseline

        t_peak_local = int(np.argmax(np.abs(rel)))
        t_amp = abs(rel[t_peak_local])
        if t_amp < self._T_WAVE_MIN_MV:
            return None

        end_thr = 0.10 * t_amp
        t_end_local = len(seg) - 1
        for i in range(t_peak_local, len(seg)):
            if abs(rel[i]) <= end_thr:
                t_end_local = i
                break

        t_end_idx = search_start + t_end_local
        qt_interval_ms = (t_end_idx - onset_idx) * self._ms_per_sample
        if 250 < qt_interval_ms < 700:
            return qt_interval_ms
        return None

    def _smooth_signal(self, signal: np.ndarray, window_size: int) -> np.ndarray:
        """Apply simple moving average smoothing."""
        if len(signal) < window_size:
            return signal
        kernel = np.ones(window_size) / window_size
        smoothed = np.convolve(signal, kernel, mode="same")
        return smoothed

    def reset(self) -> None:
        """Clear all buffers and reset analyzer to initial state."""
        self._ecg_buffer.clear()
        self._last_qrs_time = -np.inf
        self._last_qrs_index = -1
        self._last_result = None
        self._valid.clear()
        self._last_rr_s = 0.857

    @property
    def last_result(self) -> Optional[IntervalMeasurementResult]:
        """Get the most recent measurement result (may be None if never analyzed)."""
        return self._last_result
