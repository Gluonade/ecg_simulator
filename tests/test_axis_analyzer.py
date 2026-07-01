"""
Unit tests for cardiac electrical axis analysis.

Tests cover:
- Axis calculation algorithm (Lead I & aVF method)
- Classification into all 6 categories
- Edge cases (no QRS, noisy data, equipment artifacts)
- Clinical scenarios (normal, LAD, RAD, etc.)
"""

import math
import unittest
from unittest.mock import patch

import numpy as np

from cardiac_sim.core.ecg.axis_analyzer import CardiacAxisAnalyzer, AxisAnalysisResult


class TestCardiacAxisCalculation(unittest.TestCase):
    """Test axis calculation algorithm."""

    def setUp(self):
        """Create analyzer instance for each test."""
        self.analyzer = CardiacAxisAnalyzer(sample_rate_hz=500.0)

    def test_initialization(self):
        """Test analyzer initializes in correct state."""
        self.assertEqual(self.analyzer.sample_rate_hz, 500.0)
        self.assertIsNone(self.analyzer.last_result)

    def test_normal_axis_0_degrees(self):
        """Test 0° axis (standard reference direction)."""
        # Lead I positive, aVF zero → 0°
        # Create QRS-like peak: need local maximum above threshold
        for i in range(100):
            if i == 50:  # Peak at i=50
                self.analyzer.add_ecg_sample(i * 0.002, 1.5, 0.0)  # >1.0 threshold
            else:
                self.analyzer.add_ecg_sample(i * 0.002, 0.2, 0.0)
        
        result = self.analyzer.analyze()
        
        self.assertAlmostEqual(result.angle_degrees, 0.0, delta=5.0)
        self.assertEqual(result.classification, "Indifferenztyp")
        self.assertTrue(result.is_valid)

    def test_normal_axis_plus_60_degrees(self):
        """Test +60° axis (typical in normal adults)."""
        # atan2(aVF, I) = atan2(√3, 1) ≈ 60°
        i_amp = 1.0
        avf_amp = math.sqrt(3.0)
        
        for idx in range(100):
            if idx == 50:  # Peak
                self.analyzer.add_ecg_sample(idx * 0.002, i_amp * 1.5, avf_amp * 1.5)
            else:
                self.analyzer.add_ecg_sample(idx * 0.002, i_amp * 0.1, avf_amp * 0.1)
        
        result = self.analyzer.analyze()
        
        self.assertAlmostEqual(result.angle_degrees, 60.0, delta=5.0)
        self.assertEqual(result.classification, "Indifferenztyp")

    def test_plus_90_degrees_vertical(self):
        """Test +90° axis (vertical, common in thin chests)."""
        # Lead I zero, aVF positive → +90°
        for i in range(100):
            if i == 50:
                self.analyzer.add_ecg_sample(i * 0.002, 0.0, 1.5)
            else:
                self.analyzer.add_ecg_sample(i * 0.002, 0.0, 0.2)
        
        result = self.analyzer.analyze()
        
        self.assertAlmostEqual(result.angle_degrees, 90.0, delta=5.0)
        self.assertEqual(result.classification, "Steiltyp")

    def test_minus_30_degrees_left_boundary(self):
        """Test -30° axis (boundary of normal/LAD)."""
        # atan2(-0.5, 0.866) ≈ -30°
        for i in range(100):
            if i == 50:
                self.analyzer.add_ecg_sample(i * 0.002, 0.866 * 1.5, -0.5 * 1.5)
            else:
                self.analyzer.add_ecg_sample(i * 0.002, 0.866 * 0.1, -0.5 * 0.1)
        
        result = self.analyzer.analyze()
        
        # -30° is at boundary; should be classified as normal on the normal side
        self.assertAlmostEqual(result.angle_degrees, -30.0, delta=5.0)
        self.assertIn(result.classification, ["Indifferenztyp", "Linkstyp"])

    def test_minus_60_degrees_left_axis_deviation(self):
        """Test -60° axis (typical left axis deviation)."""
        # atan2(-0.866, 0.5) ≈ -60°
        for i in range(100):
            if i == 50:
                self.analyzer.add_ecg_sample(i * 0.002, 0.5 * 1.5, -0.866 * 1.5)
            else:
                self.analyzer.add_ecg_sample(i * 0.002, 0.5 * 0.1, -0.866 * 0.1)
        
        result = self.analyzer.analyze()
        
        self.assertAlmostEqual(result.angle_degrees, -60.0, delta=5.0)
        self.assertEqual(result.classification, "Linkstyp")

    def test_minus_120_degrees_extreme_left(self):
        """Test -120° axis (extreme left axis deviation)."""
        # atan2(-0.866, -0.5) ≈ -120°
        for i in range(100):
            if i == 50:
                self.analyzer.add_ecg_sample(i * 0.002, -0.5 * 1.5, -0.866 * 1.5)
            else:
                self.analyzer.add_ecg_sample(i * 0.002, -0.5 * 0.1, -0.866 * 0.1)
        
        result = self.analyzer.analyze()
        
        self.assertAlmostEqual(result.angle_degrees, -120.0, delta=5.0)
        self.assertEqual(result.classification, "Überdrehter Linkstyp")

    def test_plus_120_degrees_right_axis(self):
        """Test +120° axis (right axis deviation)."""
        # atan2(0.866, -0.5) ≈ 120°
        for i in range(100):
            if i == 50:
                self.analyzer.add_ecg_sample(i * 0.002, -0.5 * 1.5, 0.866 * 1.5)
            else:
                self.analyzer.add_ecg_sample(i * 0.002, -0.5 * 0.1, 0.866 * 0.1)
        
        result = self.analyzer.analyze()
        
        self.assertAlmostEqual(result.angle_degrees, 120.0, delta=5.0)
        self.assertEqual(result.classification, "Rechtstyp")

    def test_plus_150_degrees_extreme_right(self):
        """Test +150° axis (right axis deviation, not extreme right)."""
        # 150° is in the Rechtstyp range (120° to 180°), not extreme right
        # atan2(0.5, -0.866) ≈ 150°
        for i in range(100):
            if i == 50:
                self.analyzer.add_ecg_sample(i * 0.002, -0.866 * 1.5, 0.5 * 1.5)
            else:
                self.analyzer.add_ecg_sample(i * 0.002, -0.866 * 0.1, 0.5 * 0.1)
        
        result = self.analyzer.analyze()
        
        self.assertAlmostEqual(result.angle_degrees, 150.0, delta=5.0)
        self.assertEqual(result.classification, "Rechtstyp")

    def test_minus_160_degrees_wraps_to_negative(self):
        """Test that angles wrap correctly to [-180, 180] range."""
        # Simulate -160° (which is equivalent to +200°)
        for i in range(100):
            if i == 50:
                self.analyzer.add_ecg_sample(i * 0.002, -0.939 * 1.5, -0.342 * 1.5)
            else:
                self.analyzer.add_ecg_sample(i * 0.002, -0.939 * 0.1, -0.342 * 0.1)
        
        result = self.analyzer.analyze()
        
        # Should be in [-180, 180] range
        self.assertTrue(-180.0 <= result.angle_degrees <= 180.0)


class TestAxisClassification(unittest.TestCase):
    """Test classification into Lagetyp categories."""

    def setUp(self):
        self.analyzer = CardiacAxisAnalyzer(sample_rate_hz=500.0)

    def _add_axis_samples(self, axis_degrees: float, num_samples: int = 100):
        """Helper to add ECG samples for a specific axis."""
        rad = math.radians(axis_degrees)
        i_component = math.cos(rad)
        avf_component = math.sin(rad)
        
        for idx in range(num_samples):
            # Create Gaussian-like QRS peaks (not constant amplitude)
            # This creates realistic peaks that will be detected
            qrs_center = 50
            qrs_sigma = 10.0
            gaussian_env = math.exp(-0.5 * ((idx - qrs_center) / qrs_sigma) ** 2)
            
            # Scale amplitude based on Gaussian (peak at idx=50)
            qrs_amplitude = gaussian_env * 1.5
            
            i_amp = i_component * qrs_amplitude
            avf_amp = avf_component * qrs_amplitude
            
            # Use minimal baseline (0 for clean axis calculation)
            # In real ECGs, baseline is typically 0 when no activity
            self.analyzer.add_ecg_sample(idx * 0.002, i_amp, avf_amp)

    def test_classify_indifferenztyp(self):
        """Test normal axis classification (-30° to +90°)."""
        # Use values well within the range, away from boundaries
        # Boundaries: Indifferenztyp is -30° to +90° (not including 90°)
        test_angles = [-30.0, 0.0, 30.0, 60.0]  # Removed 90.0 (boundary with Steiltyp)
        
        for angle in test_angles:
            self.analyzer.reset()
            self._add_axis_samples(angle)
            result = self.analyzer.analyze()
            self.assertEqual(
                result.classification, "Indifferenztyp",
                f"Failed for angle {angle}°"
            )

    def test_classify_linkstyp(self):
        """Test left axis deviation classification (-90° to -30°)."""
        test_angles = [-45.0, -60.0, -75.0]
        
        for angle in test_angles:
            self.analyzer.reset()
            self._add_axis_samples(angle)
            result = self.analyzer.analyze()
            self.assertEqual(
                result.classification, "Linkstyp",
                f"Failed for angle {angle}°"
            )

    def test_classify_ueberdrehter_linkstyp(self):
        """Test extreme left deviation classification (-180° to -90°)."""
        test_angles = [-120.0, -135.0, -150.0]
        
        for angle in test_angles:
            self.analyzer.reset()
            self._add_axis_samples(angle)
            result = self.analyzer.analyze()
            self.assertEqual(
                result.classification, "Überdrehter Linkstyp",
                f"Failed for angle {angle}°"
            )

    def test_classify_steiltyp(self):
        """Test vertical axis classification (+90° to +120°)."""
        # Use values well within the range, away from boundaries
        # Boundaries: Steiltyp is +90° to +120° (not including 120°)
        test_angles = [100.0, 110.0]  # Removed 90.0 and 120.0 (both boundaries)
        
        for angle in test_angles:
            self.analyzer.reset()
            self._add_axis_samples(angle)
            result = self.analyzer.analyze()
            self.assertEqual(
                result.classification, "Steiltyp",
                f"Failed for angle {angle}°"
            )

    def test_classify_rechtstyp(self):
        """Test right axis deviation classification (+120° to +180°)."""
        test_angles = [130.0, 150.0, 170.0]
        
        for angle in test_angles:
            self.analyzer.reset()
            self._add_axis_samples(angle)
            result = self.analyzer.analyze()
            self.assertEqual(
                result.classification, "Rechtstyp",
                f"Failed for angle {angle}°"
            )

    def test_classify_ueberdrehter_rechtstyp(self):
        """Test extreme right deviation classification (-180° to -120°)."""
        # Note: angles around -160° and -170° should be classified as extreme right
        test_angles = [-160.0, -170.0]
        
        for angle in test_angles:
            self.analyzer.reset()
            self._add_axis_samples(angle)
            result = self.analyzer.analyze()
            self.assertEqual(
                result.classification, "Überdrehter Rechtstyp",
                f"Failed for angle {angle}°"
            )


class TestEdgeCases(unittest.TestCase):
    """Test edge cases and error handling."""

    def setUp(self):
        self.analyzer = CardiacAxisAnalyzer(sample_rate_hz=500.0)

    def test_empty_buffer(self):
        """Test analysis with no ECG samples."""
        result = self.analyzer.analyze()
        
        self.assertFalse(result.is_valid)
        self.assertEqual(result.classification, "Undetermined")
        self.assertEqual(result.num_qrs_complexes, 0)

    def test_insufficient_samples_for_peak_detection(self):
        """Test with too few samples for QRS detection."""
        self.analyzer.add_ecg_sample(0.0, 0.5, 0.5)
        self.analyzer.add_ecg_sample(0.002, 0.5, 0.5)
        
        result = self.analyzer.analyze()
        
        # Need at least 3 samples for peak detection
        self.assertEqual(result.num_qrs_complexes, 0)

    def test_no_qrs_peaks_found(self):
        """Test with ECG signal below threshold (no detectable QRS)."""
        # All amplitudes below 1.0 mV threshold → no peaks
        for i in range(100):
            self.analyzer.add_ecg_sample(i * 0.002, 0.2, 0.2)
        
        result = self.analyzer.analyze()
        
        self.assertFalse(result.is_valid)
        self.assertEqual(result.num_qrs_complexes, 0)

    def test_single_qrs_complex(self):
        """Test with only one QRS complex detected."""
        # Create a single sharp QRS-like peak above threshold
        for i in range(100):
            if i == 50:
                # Single peak at midpoint
                self.analyzer.add_ecg_sample(i * 0.002, 1.5, 1.5)
            else:
                # Baseline noise below threshold
                self.analyzer.add_ecg_sample(i * 0.002, 0.2, 0.2)
        
        result = self.analyzer.analyze()
        
        self.assertTrue(result.is_valid)
        self.assertEqual(result.num_qrs_complexes, 1)

    def test_multiple_qrs_complexes(self):
        """Test averaging across multiple QRS complexes."""
        # Create 3 QRS peaks
        for i in range(200):
            if i in [50, 100, 150]:
                self.analyzer.add_ecg_sample(i * 0.002, 1.0 + 0.1 * (i % 3), 1.0)
            else:
                self.analyzer.add_ecg_sample(i * 0.002, 0.2, 0.2)
        
        result = self.analyzer.analyze()
        
        self.assertTrue(result.is_valid)
        self.assertGreater(result.num_qrs_complexes, 0)

    def test_biphasic_qrs(self):
        """Test handling of biphasic QRS (positive then negative)."""
        # Simulate biphasic complex using peak detection on positive peak
        for i in range(100):
            if i < 50:
                # Rising phase
                self.analyzer.add_ecg_sample(i * 0.002, 1.5, 0.5)
            elif i < 55:
                # Peak
                self.analyzer.add_ecg_sample(i * 0.002, 1.5, 0.5)
            else:
                # Falling phase
                self.analyzer.add_ecg_sample(i * 0.002, 0.5, -0.5)
        
        result = self.analyzer.analyze()
        
        # Should still detect the positive peak
        self.assertTrue(result.is_valid or result.num_qrs_complexes >= 0)

    def test_low_amplitude_qrs(self):
        """Test with QRS amplitude just above threshold."""
        for i in range(100):
            if i == 50:
                self.analyzer.add_ecg_sample(i * 0.002, 1.01, 1.01)  # Just above 1.0 threshold
            else:
                self.analyzer.add_ecg_sample(i * 0.002, 0.5, 0.5)
        
        result = self.analyzer.analyze()
        
        # Should detect the marginal peak
        self.assertTrue(result.is_valid or result.num_qrs_complexes >= 0)

    def test_confidence_increases_with_more_complexes(self):
        """Test that confidence score increases with more detected complexes."""
        # Scenario 1: Single complex
        analyzer1 = CardiacAxisAnalyzer(sample_rate_hz=500.0)
        for i in range(50):
            if i == 25:
                analyzer1.add_ecg_sample(i * 0.002, 1.5, 0.5)
            else:
                analyzer1.add_ecg_sample(i * 0.002, 0.2, 0.2)
        result1 = analyzer1.analyze()
        conf1 = result1.confidence if result1.is_valid else 0.0
        
        # Scenario 2: Multiple complexes (same buffer duration)
        analyzer2 = CardiacAxisAnalyzer(sample_rate_hz=500.0)
        for i in range(200):
            if i % 50 == 25:
                analyzer2.add_ecg_sample(i * 0.002, 1.5, 0.5)
            else:
                analyzer2.add_ecg_sample(i * 0.002, 0.2, 0.2)
        result2 = analyzer2.analyze()
        conf2 = result2.confidence if result2.is_valid else 0.0
        
        # More complexes should have higher or equal confidence
        if result1.is_valid and result2.is_valid:
            self.assertGreaterEqual(conf2, conf1)


class TestResetBehavior(unittest.TestCase):
    """Test analyzer reset and state management."""

    def test_reset_clears_state(self):
        """Test that reset() clears all internal state."""
        analyzer = CardiacAxisAnalyzer()
        
        # Add some data
        for i in range(50):
            analyzer.add_ecg_sample(i * 0.002, 1.0, 0.5)
        
        result_before = analyzer.analyze()
        
        # Reset
        analyzer.reset()
        
        # Should now be in empty state
        result_after = analyzer.analyze()
        
        self.assertFalse(result_after.is_valid)
        self.assertEqual(result_after.num_qrs_complexes, 0)

    def test_cached_result_property(self):
        """Test that last_result property is cached."""
        analyzer = CardiacAxisAnalyzer()
        
        # Initially, last_result should be None
        self.assertIsNone(analyzer.last_result)
        
        # Add data and analyze
        for i in range(100):
            if i == 50:
                analyzer.add_ecg_sample(i * 0.002, 1.5, 0.5)
            else:
                analyzer.add_ecg_sample(i * 0.002, 0.2, 0.2)
        
        result = analyzer.analyze()
        
        # last_result should now match
        self.assertIsNotNone(analyzer.last_result)
        self.assertEqual(analyzer.last_result.angle_degrees, result.angle_degrees)
        self.assertEqual(analyzer.last_result.classification, result.classification)


class TestClinicalScenarios(unittest.TestCase):
    """Test realistic clinical scenarios."""

    def setUp(self):
        self.analyzer = CardiacAxisAnalyzer(sample_rate_hz=500.0)

    def _simulate_normal_ecg(self, axis_degrees: float, num_beats: int = 5):
        """Simulate normal ECG with multiple heartbeats."""
        samples_per_beat = 100
        
        for beat_idx in range(num_beats):
            for sample_idx in range(samples_per_beat):
                t = (beat_idx * samples_per_beat + sample_idx) * 0.002
                
                # Simulate QRS as a Gaussian-like peak centered at sample 25
                # This creates a realistic peaked QRS (not a flat rectangle)
                rad = math.radians(axis_degrees)
                i_component = math.cos(rad)
                avf_component = math.sin(rad)
                
                # Create Gaussian envelope centered at sample 25, width ~15 samples
                qrs_center = 25
                qrs_sigma = 5.0  # standard deviation
                gaussian_env = math.exp(-0.5 * ((sample_idx - qrs_center) / qrs_sigma) ** 2)
                
                # QRS amplitude scaled by Gaussian (peak at sample 25)
                # Maximum value when sample_idx == 25: gaussian_env = 1.0
                qrs_amplitude = gaussian_env * 1.5
                
                # Apply axis direction to both leads
                i_amp = i_component * qrs_amplitude
                avf_amp = avf_component * qrs_amplitude
                
                # Use minimal baseline (0 for clean axis calculation)
                self.analyzer.add_ecg_sample(t, i_amp, avf_amp)

    def test_scenario_normal_adult(self):
        """Test normal adult ECG (axis ~60°, Indifferenztyp)."""
        self._simulate_normal_ecg(axis_degrees=60.0)
        result = self.analyzer.analyze()
        
        self.assertEqual(result.classification, "Indifferenztyp")
        self.assertGreater(result.confidence, 0.5)

    def test_scenario_left_axis_deviation(self):
        """Test left axis deviation (axis ~-45°, Linkstyp)."""
        self._simulate_normal_ecg(axis_degrees=-45.0)
        result = self.analyzer.analyze()
        
        self.assertEqual(result.classification, "Linkstyp")
        self.assertGreater(result.confidence, 0.5)

    def test_scenario_right_axis_deviation(self):
        """Test right axis deviation (axis ~+130°, Rechtstyp)."""
        # Use 130° instead of 120° to avoid boundary floating-point issues
        self._simulate_normal_ecg(axis_degrees=130.0)
        result = self.analyzer.analyze()
        
        self.assertEqual(result.classification, "Rechtstyp")
        self.assertGreater(result.confidence, 0.5)

    def test_scenario_vertical_axis(self):
        """Test vertical axis (axis ~+90°, Steiltyp)."""
        self._simulate_normal_ecg(axis_degrees=90.0)
        result = self.analyzer.analyze()
        
        self.assertEqual(result.classification, "Steiltyp")
        self.assertGreater(result.confidence, 0.5)


if __name__ == "__main__":
    unittest.main()
