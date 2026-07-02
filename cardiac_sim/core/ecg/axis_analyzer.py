"""
Cardiac Electrical Axis (Lagetyp) Analysis Module.

Analyzes the QRS complex to determine the cardiac electrical axis and classify it
according to German cardiology standards (Indifferenztyp, Linkstyp, etc.).

Algorithm
---------
Uses the standard clinical **Lead I & aVF method**:

1. Extract QRS complex from 12-lead ECG samples
2. Compute average amplitude in lead I and aVF
3. Calculate axis angle: atan2(aVF_amplitude, I_amplitude) * 180/π
4. Classify into 6 categories based on angle ranges

Advantages:
- Simple and fast (only 2 leads required)
- Clinically standard and well-established
- Robust to noise when using multi-beat averaging
- Matches real ECG analysis workflow

Coordinate System
-----------------
- Frontal plane: Lead I at 0°, aVF at +90°
- Angle ranges (in degrees):
  * 0° to +90°:       Lead I positive, aVF positive
  * +90° to +180°:    Lead I negative, aVF positive
  * -180° to -90°:    Lead I negative, aVF negative
  * -90° to 0°:       Lead I positive, aVF negative

Classification Categories
--------------------------
Following German cardiology standards ("Lagetyp"):

1. **Indifferenztyp (Normal Axis)**: -30° to +90°
   Typical in healthy adults, best seen in frontal plane radiating from
   third quadrant toward first quadrant of Einthoven triangle.

2. **Linkstyp (Left Axis Deviation, LAD)**: -30° to -90°
   Common in obesity, pregnancy, older age, inferior MI recovery.
   Axis rotated toward left shoulder.

3. **Überdrehter Linkstyp (Extreme Left, ELAD)**: -90° to -180°
   Rare; extreme leftward rotation; may indicate pathology (severe LAD,
   lead reversal artifacts, emphysema with hyperinflation).

4. **Steiltyp (Vertical Axis)**: +90° to +120°
   Common in thin chest walls, young adults, tall patients.
   Axis rotated toward feet; may indicate inferior conduction delay.

5. **Rechtstyp (Right Axis Deviation, RAD)**: +120° to +180°
   Indicates rightward axis rotation; seen in chronic lung disease (COPD,
   pulmonary hypertension), tall thin patients, or lateral MI.

6. **Überdrehter Rechtstyp (Extreme Right, ERAD)**: -180° to -120°
   (Wraps around ±180°)
   Rare; extreme rightward rotation; may indicate lead reversal or
   extreme pathology (severe emphysema).

Clinical Notes
--------------
- Axis deviation can develop over minutes/hours in acute settings
- Normal changes during physical exertion (axis tilts more vertical)
- Some drugs (stimulants, TCAs) can cause axis shifts
- Equipment artifacts or lead reversal can produce false extreme values
- Should correlate with heart rate, QRS duration, and other ECG findings

References
----------
- Macfarlane PW, Lawrie TDV. Comprehensive Electrocardiology. Pergamon Press.
- Goldberger AL, Goldberger ZD, Shvilkin A. Goldberger's Clinical
  Electrocardiography. 9th ed. Elsevier.
- German cardiology guidelines: Lagetyp classification per DGK standards.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)


@dataclass
class AxisAnalysisResult:
    """Result of cardiac axis analysis."""

    angle_degrees: float
    """Cardiac electrical axis in degrees (-180 to +180)."""

    classification: str
    """Lagetyp classification (e.g., 'Indifferenztyp', 'Linkstyp', etc.)."""

    confidence: float
    """Confidence score (0.0 to 1.0). Indicates quality of QRS detection."""

    is_valid: bool
    """True if axis could be determined from available data."""

    lead_i_amplitude: float
    """Average QRS amplitude in lead I [mV]."""

    lead_avf_amplitude: float
    """Average QRS amplitude in lead aVF [mV]."""

    num_qrs_complexes: int
    """Number of QRS complexes averaged."""


class CardiacAxisAnalyzer:
    """
    Analyzes cardiac electrical axis from 12-lead ECG using leads I and aVF.

    This analyzer maintains rolling buffers of ECG samples and performs QRS
    detection to compute the cardiac axis. Results are classified into clinical
    categories.

    Thread Safety
    -------------
    This class is NOT thread-safe. Call it only from the simulation thread.
    """

    # QRS detection parameters (validated clinically)
    _QRS_THRESHOLD_MV = 1.0
    """Minimum voltage for QRS peak detection [mV]. Typical adult QRS: 0.5-2.0 mV."""

    _MIN_QRS_INTERVAL_S = 0.200
    """Minimum physiological RR interval [s]. Corresponds to ~300 bpm."""

    _BUFFER_DURATION_S = 5.0
    """Duration of rolling buffer to maintain [s]. For rate ~300 Hz, ~1500 samples."""

    # Axis classification boundaries (degrees)
    _AXIS_BOUNDARIES = {
        "Indifferenztyp": (-30, 90),
        "Linkstyp": (-90, -30),
        "Überdrehter Linkstyp": (-180, -90),
        "Steiltyp": (90, 120),
        "Rechtstyp": (120, 180),
        "Überdrehter Rechtstyp": (-180, -120),  # Wraps around ±180
    }

    def __init__(self, sample_rate_hz: float = 500.0):
        """
        Initialize the cardiac axis analyzer.

        Parameters
        ----------
        sample_rate_hz
            Sampling rate of ECG data [Hz]. Used to size rolling buffer.
        """
        self.sample_rate_hz = sample_rate_hz
        
        # Calculate buffer size from duration
        buffer_size = max(300, int(self._BUFFER_DURATION_S * sample_rate_hz))
        
        # Rolling buffers: (timestamp, voltage) for leads I and aVF
        self._ecg_buffer_i: deque[tuple[float, float]] = deque(maxlen=buffer_size)
        self._ecg_buffer_avf: deque[tuple[float, float]] = deque(maxlen=buffer_size)
        
        # Track last detected QRS to avoid re-detection
        self._last_qrs_detected_time: float = -np.inf
        
        # For QRS amplitude accumulation during detection window
        self._qrs_amplitudes_i: list[float] = []
        self._qrs_amplitudes_avf: list[float] = []
        self._detected_qrs_times: list[float] = []
        
        # Last analysis result (cached)
        self._last_result: Optional[AxisAnalysisResult] = None

    def add_ecg_sample(
        self,
        timestamp: float,
        lead_i_voltage: float,
        lead_avf_voltage: float,
    ) -> None:
        """
        Add one ECG sample to the rolling buffers.

        Called each simulation step (typically every 2 ms at 500 Hz).

        Parameters
        ----------
        timestamp
            Simulation time [s].
        lead_i_voltage
            ECG voltage at lead I [mV].
        lead_avf_voltage
            ECG voltage at lead aVF [mV].
        """
        self._ecg_buffer_i.append((timestamp, lead_i_voltage))
        self._ecg_buffer_avf.append((timestamp, lead_avf_voltage))

    def analyze(self) -> AxisAnalysisResult:
        """
        Perform QRS detection and compute cardiac axis.

        Should be called regularly (e.g., every ~200 ms or 10× per second)
        to track axis changes.

        Returns
        -------
        AxisAnalysisResult
            Contains angle, classification, confidence, and diagnostic info.
            When no complexes are detected between beats, returns the last
            valid result to avoid display flickering. If never detected any
            complexes, returns an invalid result.
        """
        if not self._ecg_buffer_i or not self._ecg_buffer_avf:
            # Not enough data yet
            # Return last valid result if available, otherwise invalid
            if self._last_result and self._last_result.is_valid:
                return self._last_result
            return AxisAnalysisResult(
                angle_degrees=0.0,
                classification="Undetermined",
                confidence=0.0,
                is_valid=False,
                lead_i_amplitude=0.0,
                lead_avf_amplitude=0.0,
                num_qrs_complexes=0,
            )

        # Detect QRS peaks in both leads
        self._detect_qrs_peaks()

        # If no complexes found, return last valid result to avoid flickering
        if not self._detected_qrs_times or not self._qrs_amplitudes_i:
            if self._last_result and self._last_result.is_valid:
                return self._last_result
            return AxisAnalysisResult(
                angle_degrees=0.0,
                classification="Undetermined",
                confidence=0.0,
                is_valid=False,
                lead_i_amplitude=0.0,
                lead_avf_amplitude=0.0,
                num_qrs_complexes=0,
            )

        # Average QRS amplitudes across all detected complexes
        avg_amplitude_i = float(np.mean(self._qrs_amplitudes_i))
        avg_amplitude_avf = float(np.mean(self._qrs_amplitudes_avf))

        # Calculate axis angle using atan2
        axis_radians = np.arctan2(avg_amplitude_avf, avg_amplitude_i)
        axis_degrees = float(np.degrees(axis_radians))

        # Ensure angle is in [-180, 180] range
        if axis_degrees > 180.0:
            axis_degrees -= 360.0
        elif axis_degrees < -180.0:
            axis_degrees += 360.0

        # Classify the axis
        classification = self._classify_axis(axis_degrees)

        # Compute confidence based on number of complexes and amplitude consistency
        num_complexes = len(self._detected_qrs_times)
        confidence = min(1.0, num_complexes / 5.0)  # Normalized to 1.0 at 5 complexes

        result = AxisAnalysisResult(
            angle_degrees=axis_degrees,
            classification=classification,
            confidence=confidence,
            is_valid=True,
            lead_i_amplitude=avg_amplitude_i,
            lead_avf_amplitude=avg_amplitude_avf,
            num_qrs_complexes=num_complexes,
        )

        self._last_result = result
        return result

    def _detect_qrs_peaks(self) -> None:
        """
        Detect QRS peaks in both leads using threshold-based method.

        Detects local extrema (maxima or minima) with large absolute magnitude
        in EITHER lead I or lead aVF. This handles QRS complexes across all axis
        directions, since different axes have different lead dominance.

        Updates self._qrs_amplitudes_i, self._qrs_amplitudes_avf,
        self._detected_qrs_times.
        """
        self._qrs_amplitudes_i.clear()
        self._qrs_amplitudes_avf.clear()
        self._detected_qrs_times.clear()

        if len(self._ecg_buffer_i) < 3 or len(self._ecg_buffer_avf) < 3:
            return

        # Convert to arrays for easier processing
        times_i = np.array([t for t, _ in self._ecg_buffer_i], dtype=np.float64)
        voltages_i = np.array([v for _, v in self._ecg_buffer_i], dtype=np.float64)
        voltages_avf = np.array([v for _, v in self._ecg_buffer_avf], dtype=np.float64)

        # Compute combined signal: magnitude of QRS is max(|I|, |aVF|) at any time
        # This ensures detection regardless of axis direction
        combined_magnitude = np.maximum(np.abs(voltages_i), np.abs(voltages_avf))

        # Detect local extrema (maxima) in the combined magnitude signal
        for i in range(1, len(combined_magnitude) - 1):
            is_local_max = (
                combined_magnitude[i] > combined_magnitude[i - 1]
                and combined_magnitude[i] > combined_magnitude[i + 1]
            )
            is_above_threshold = combined_magnitude[i] > self._QRS_THRESHOLD_MV

            if is_local_max and is_above_threshold:
                peak_time = times_i[i]
                
                # Ensure minimum interval between detected peaks
                if peak_time - self._last_qrs_detected_time >= self._MIN_QRS_INTERVAL_S:
                    self._last_qrs_detected_time = peak_time
                    
                    # Record signed amplitudes from both leads at this QRS time
                    # Use signed values to preserve axis information
                    self._qrs_amplitudes_i.append(float(voltages_i[i]))
                    self._qrs_amplitudes_avf.append(float(voltages_avf[i]))
                    self._detected_qrs_times.append(float(peak_time))

    def _classify_axis(self, angle_degrees: float) -> str:
        """
        Classify the axis angle into a clinical category.

        Parameters
        ----------
        angle_degrees
            Cardiac electrical axis [-180 to +180].

        Returns
        -------
        str
            Classification name (Lagetyp).
        """
        # Special handling for extreme right axis (wraps around ±180)
        if angle_degrees < -150.0:  # i.e., between -180 and -150
            return "Überdrehter Rechtstyp"

        # Check each boundary range
        for classification, (lower, upper) in self._AXIS_BOUNDARIES.items():
            # Skip extreme right (already handled)
            if classification == "Überdrehter Rechtstyp":
                continue
            
            if lower <= angle_degrees < upper:
                return classification

        # Fallback (should not reach here if boundaries are complete)
        logger.warning(
            f"Axis angle {angle_degrees}° did not match any classification. "
            "This may indicate incomplete axis boundary definitions."
        )
        return "Indifferenztyp"  # Safe default

    def reset(self) -> None:
        """Clear all buffers and reset analyzer to initial state."""
        self._ecg_buffer_i.clear()
        self._ecg_buffer_avf.clear()
        self._qrs_amplitudes_i.clear()
        self._qrs_amplitudes_avf.clear()
        self._detected_qrs_times.clear()
        self._last_qrs_detected_time = -np.inf
        self._last_result = None

    @property
    def last_result(self) -> Optional[AxisAnalysisResult]:
        """Get the most recent analysis result (may be None if never analyzed)."""
        return self._last_result
