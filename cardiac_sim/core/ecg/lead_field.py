"""
LeadFieldForwardModel — simplified single-equivalent-dipole ECG forward model.

Maps an instantaneous 3-D cardiac dipole vector to 12 body-surface lead
voltages via pre-defined unit lead-field vectors.

Coordinate system
-----------------
x : positive toward patient's **left**
y : positive toward patient's **inferior** (feet-ward)
z : positive toward patient's **anterior** (front of chest)

This is the standard clinical ECG axis convention:

* Frontal plane = x-y:
  Lead I axis at 0°, Lead II at +60°, Lead III at +120°, etc.

* Transverse plane = x-z:
  Precordial leads V1–V6 progress from right-anterior (V1) to
  left-lateral (V6).

Lead vectors
------------
Each row of :data:`LEAD_MATRIX` is the unit vector from the heart centre
to the positive electrode for that lead.  The ECG voltage is the dot
product of this vector with the cardiac dipole::

    V_lead = lead_vector · P_cardiac

Frontal leads are exact (Einthoven geometry).  Precordial lead vectors
include a +15° inferior tilt (y-component ≈ 0.259) to account for the
electrodes sitting below the cardiac centre.

Phase note
----------
This model is adequate for Phase 1 (discrete conduction graph) and
produces clinically recognisable ECG morphology.  Phase 5+ will replace
it with a boundary-element torso conductor model incorporating realistic
cardiac geometry and tissue anisotropy.
"""

from __future__ import annotations

import numpy as np

from cardiac_sim.core.interfaces import AbstractECGForwardModel, LEAD_NAMES

# ---------------------------------------------------------------------------
# Lead field unit vectors
# ---------------------------------------------------------------------------
# Frontal leads — derived from Einthoven triangle, no z-component.
# Precordial leads — transverse-plane positions with +15° inferior tilt.
# Convention: V_lead = lead_vector · (px, py, pz)

_RAW: dict[str, list[float]] = {
    # ── Frontal (limb) leads ─────────────────────────────────────────────
    "I":    [ 1.000,  0.000,  0.000],   # 0°
    "II":   [ 0.500,  0.866,  0.000],   # +60°
    "III":  [-0.500,  0.866,  0.000],   # +120°
    "aVR":  [-0.866, -0.500,  0.000],   # −150°
    "aVL":  [ 0.866, -0.500,  0.000],   # −30°
    "aVF":  [ 0.000,  1.000,  0.000],   # +90°

    # ── Precordial leads ─────────────────────────────────────────────────
    # V1: 4th ICS right of sternum — slightly rightward, very anterior
    "V1":   [-0.346,  0.259,  0.901],
    # V2: 4th ICS left of sternum — mostly anterior
    "V2":   [-0.150,  0.259,  0.954],
    # V3: between V2 and V4
    "V3":   [ 0.150,  0.259,  0.955],
    # V4: 5th ICS midclavicular line
    "V4":   [ 0.454,  0.259,  0.852],
    # V5: anterior axillary line
    "V5":   [ 0.698,  0.259,  0.668],
    # V6: midaxillary line
    "V6":   [ 0.891,  0.259,  0.370],
}

# Build (12 × 3) array in canonical LEAD_NAMES order, then normalise rows
LEAD_MATRIX: np.ndarray = np.array(
    [_RAW[name] for name in LEAD_NAMES], dtype=np.float64
)
_row_norms = np.linalg.norm(LEAD_MATRIX, axis=1, keepdims=True)
LEAD_MATRIX /= _row_norms   # each row is a unit vector


# ---------------------------------------------------------------------------
# Forward model class
# ---------------------------------------------------------------------------

class LeadFieldForwardModel(AbstractECGForwardModel):
    """
    Simplified single-equivalent-dipole forward model.

    Suitable for Phase 1 (discrete conduction graph).  Produces clinically
    recognisable ECG morphology for normal sinus rhythm and all
    conduction-level pathologies.
    """

    def compute_leads(self, activation_vectors: np.ndarray) -> np.ndarray:
        """
        Project dipole contributions to 12-lead voltages.

        Parameters
        ----------
        activation_vectors:
            Shape ``(N, 3)`` — one dipole per active zone, or ``(3,)`` for an
            already-summed total dipole.

        Returns
        -------
        np.ndarray
            Shape ``(12,)`` [mV], ``float32``.
        """
        if activation_vectors.ndim == 2:
            total = activation_vectors.sum(axis=0)
        else:
            total = activation_vectors
        return (LEAD_MATRIX @ total).astype(np.float32)

    def project(self, dipole: np.ndarray) -> np.ndarray:
        """
        Project a single total dipole vector to 12 lead voltages.

        Parameters
        ----------
        dipole:
            Shape ``(3,)`` — the summed instantaneous cardiac dipole [mV].

        Returns
        -------
        np.ndarray
            Shape ``(12,)`` [mV], ``float32``.
        """
        return (LEAD_MATRIX @ dipole).astype(np.float32)
