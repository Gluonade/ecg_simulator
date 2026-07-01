"""
ConductionNode — anatomical node in the cardiac conduction graph.

Each node represents a cardiac structure (atrial myocardium, AV node,
bundle branch, ventricular segment, etc.) that may contribute to the
surface ECG when activated.

Waveform model
--------------
When the node activates at time ``t_act``, its instantaneous contribution
to the cardiac dipole vector at time ``t`` is::

    dipole(t | t_act) =
          depol_amplitude * G(t,  t_act,                   σ_depol)
        + repol_amplitude * G(t,  t_act + repol_delay,     σ_repol)

where ``G(t, μ, σ) = exp(−(t − μ)² / (2σ²))`` and
``σ = duration / (2 √(2 ln 2))``.

* Depolarisation pulse: fast, narrow Gaussian.
* Repolarisation pulse: slower, broader Gaussian, delayed by ``repol_delay``.
  A **positive** ``repol_amplitude`` produces a T wave concordant with the
  QRS in that node's dipole direction (physiologically correct for LV).
* Nodes that only conduct (SA node, AV node, His bundle): both amplitudes = 0.

Coordinate system
-----------------
x : positive toward patient's **left**
y : positive toward patient's **inferior** (feet-ward)
z : positive toward patient's **anterior** (front of chest)
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np


def _sigma(duration: float) -> float:
    """Convert Gaussian full-width (at half-maximum) to std-dev."""
    return duration / (2.0 * math.sqrt(2.0 * math.log(2.0))) if duration > 0.0 else 1e-9


def _gauss(t: float, mu: float, sigma: float) -> float:
    return math.exp(-0.5 * ((t - mu) / sigma) ** 2)


@dataclass
class ConductionNode:
    """
    Single anatomical node in the cardiac conduction graph.

    Parameters
    ----------
    name:
        Unique identifier used as the graph key.
    position:
        3-D position in heart coordinates, shape ``(3,)`` [arbitrary units].
    dipole_direction:
        **Unit vector** of this node's activation dipole.  Zero vector → no
        ECG contribution (pure conduction node).
    depol_amplitude:
        Peak magnitude of the depolarisation dipole [mV].  Zero for
        conduction-only nodes.
    depol_duration:
        Full-width of the depolarisation Gaussian [s].
    repol_amplitude:
        Peak magnitude of the repolarisation dipole [mV].  Positive value
        produces a T wave concordant with the QRS (normal physiology for LV).
    repol_delay:
        Delay from the activation instant to the repolarisation peak [s].
        Approximately equals the local action potential duration.
    repol_duration:
        Full-width of the repolarisation Gaussian [s].  Broader than
        ``depol_duration`` → T wave is wider than QRS component.
    refractory_period:
        Effective refractory period [s].  Guards against rapid re-excitation
        and enables correct LBBB/RBBB simulation via retrograde paths
        (Phase 2).
    """

    name: str
    position: np.ndarray        # shape (3,)
    dipole_direction: np.ndarray  # unit vector, shape (3,)

    depol_amplitude: float
    depol_duration: float

    repol_amplitude: float
    repol_delay: float
    repol_duration: float

    refractory_period: float

    # ------------------------------------------------------------------
    # Waveform computation
    # ------------------------------------------------------------------

    def dipole_contribution(
        self,
        t: float,
        t_act: float,
        depol_width_factor: float = 1.0,
        repol_sign: float = 1.0,
        amplitude_factor: float = 1.0,
    ) -> np.ndarray:
        """
        Return this node's instantaneous dipole contribution at time *t*,
        given that it activated at *t_act*.

        Parameters
        ----------
        depol_width_factor:
            Multiplier for both depolarisation and repolarisation Gaussian
            widths.  Use > 1 for retrograde/ectopic activation (slow
            cell-to-cell spread → broad, slurred QRS component).
        repol_sign:
            Sign of the repolarisation term (+1 = concordant, −1 = discordant).
            Retrograde and ectopic beats have discordant T waves (opposite
            polarity to the main QRS deflection in each lead).
        amplitude_factor:
            Multiplier for the depolarisation amplitude only.  Used to boost
            the retrograde RV amplitude in RBBB to produce a visible R'.

        Returns
        -------
        np.ndarray
            Shape ``(3,)`` — dipole vector [mV].
        """
        sigma_d = _sigma(self.depol_duration * depol_width_factor)
        sigma_r = _sigma(self.repol_duration * depol_width_factor)

        magnitude = (
            self.depol_amplitude * amplitude_factor * _gauss(t, t_act, sigma_d)
            + self.repol_amplitude * repol_sign * _gauss(
                t, t_act + self.repol_delay, sigma_r
            )
        )
        return magnitude * self.dipole_direction
