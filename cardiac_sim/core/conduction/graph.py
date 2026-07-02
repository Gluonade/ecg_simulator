"""
ConductionGraph — directed graph of the cardiac conduction system.

Nodes represent anatomical structures; edges represent conduction pathways
with delays and conductances.  A BFS traversal from SA_NODE computes the
absolute activation time for every reachable node in a single heartbeat.

Physiology encoded
------------------
All delays and conductances are read from :class:`SimulationParameters`,
so every structural pathology (AV blocks, bundle branch blocks, hemiblocks)
reduces to a parameter change — no special-case logic needed.

Propagation rules
-----------------
* ``conductance == 0``  → complete block; propagation halts at this edge.
* ``conductance < 1``   → slowed conduction; effective delay =
  ``base_delay / conductance`` (capped at 10× base_delay to avoid ∞).
* ``conductance > 1``   → faster conduction (e.g., sympathetic stimulation).
* Refractory check: if a target node was last activated less than
  ``node.refractory_period`` seconds ago, the incoming wavefront is blocked.
  This enables correct LBBB/RBBB simulation via slow retrograde pathways
  (Phase 2: retrograde edges added; normal path makes target refractory
  before retrograde signal arrives).
"""

from __future__ import annotations

import logging
import math
from collections import deque
from dataclasses import dataclass

import numpy as np

from cardiac_sim.core.conduction.node import ConductionNode
from cardiac_sim.core.parameter_model import SimulationParameters

logger = logging.getLogger(__name__)

_NEG_INF = float("-inf")
_MAX_DELAY_MULTIPLIER = 10.0  # cap on effective delay to avoid numerical issues

# ---------------------------------------------------------------------------
# Conduction "route" classes — determine downstream ECG morphology.
# ---------------------------------------------------------------------------
# The class of the *earliest-arriving* wavefront that reaches a node sets that
# node's waveform morphology in the ECG engine:
#
# * ANTEGRADE — normal forward His-Purkinje conduction. Narrow, concordant.
# * BUNDLE     — working-myocardium retrograde spread from the *contralateral*
#                bundle (LBBB / RBBB backup). Broad, slurred QRS + discordant T.
# * HEMIBLOCK  — intra-LV *fascicular* re-routing (LAHB / LPHB backup). The
#                re-routed segment is only mildly delayed/broadened and keeps a
#                concordant T wave. Crucially, a hemiblock must stay bounded and
#                never converge to a full bundle-branch-block morphology, no
#                matter how severe the fascicular conductance reduction — the
#                intact fascicle and His bundle still activate the LV.
#
# Severity ordering (BUNDLE > HEMIBLOCK > ANTEGRADE) is used when propagating a
# route through the graph: a node inherits the most severe class on its path.
ROUTE_ANTEGRADE = "antegrade"
ROUTE_BUNDLE = "bundle"
ROUTE_HEMIBLOCK = "hemiblock"

_ROUTE_SEVERITY = {
    ROUTE_ANTEGRADE: 0,
    ROUTE_HEMIBLOCK: 1,
    ROUTE_BUNDLE: 2,
}

# Delay of the RBBB backup path (LV_ANT → RV, slow trans-septal cell-to-cell
# spread). Long enough that the right ventricle depolarises clearly *after* the
# left, producing a terminal R' (V1) / slurred S (I, V6) and a QRS > 120 ms.
RBBB_RETRO_DELAY_S = 0.100


@dataclass
class ConductionEdge:
    """Directed conduction pathway between two nodes."""

    source: str
    target: str
    base_delay: float   # nominal conduction delay [s]
    conductance: float  # 1 = normal, 0 = complete block, <1 = slowed
    route: str = ROUTE_ANTEGRADE
    """Conduction class of this pathway (see the ``ROUTE_*`` constants).

    * ``ROUTE_ANTEGRADE`` — normal forward conduction.
    * ``ROUTE_BUNDLE`` — working-myocardium backup from the contralateral bundle
      (RV↔LV). Downstream nodes get broad Gaussians and discordant T waves.
    * ``ROUTE_HEMIBLOCK`` — intra-LV fascicular re-routing (LV_ANT↔LV_INF).
      Downstream nodes get only mild broadening and keep a concordant T wave, so
      a severe fascicular block stays a hemiblock instead of turning into LBBB.
    """


class ConductionGraph:
    """
    Directed cardiac conduction graph.

    Build via :func:`build_physiological_graph` rather than instantiating
    directly.
    """

    def __init__(
        self,
        nodes: dict[str, ConductionNode],
        edges: list[ConductionEdge],
    ) -> None:
        self.nodes = nodes
        self._adj: dict[str, list[ConductionEdge]] = {n: [] for n in nodes}
        for edge in edges:
            if edge.source in self._adj:
                self._adj[edge.source].append(edge)
            else:
                logger.warning("Edge references unknown source node '%s'.", edge.source)

    def compute_beat_activations(
        self,
        start_time: float,
        last_activation: dict[str, float] | None = None,
        start_node: str = "SA_NODE",
    ) -> tuple[dict[str, float], frozenset[str], frozenset[str]]:
        """
        BFS from *start_node* to compute each node's activation time for one beat.

        Parameters
        ----------
        start_time:
            Absolute simulation time when *start_node* fires [s].
        last_activation:
            Optional mapping of ``node_name → last activation time [s]``
            from the previous beat.  Used for refractory period checking.
        start_node:
            Entry point for the BFS.  Defaults to ``"SA_NODE"`` for normal
            sinus rhythm.  Use ``"HIS"`` for AF beats (bypasses atrial and
            AV-node timing).

        Returns
        -------
        tuple[dict[str, float], frozenset[str], frozenset[str]]
            ``(activation_times, retrograde_nodes, hemiblock_nodes)``.

            Each node is classified by the conduction *route* of its
            earliest-arriving wavefront (the most severe route encountered on
            that path — see the ``ROUTE_*`` constants):

            * ``retrograde_nodes`` — reached via a ``ROUTE_BUNDLE`` edge
              (LBBB/RBBB backup) or downstream of such a node. Rendered with
              broad, slurred Gaussians and discordant T waves.
            * ``hemiblock_nodes`` — reached via a ``ROUTE_HEMIBLOCK`` edge
              (LAHB/LPHB fascicular re-routing) or downstream of such a node,
              and *not* also bundle-retrograde. Rendered with only mild
              broadening and a concordant T wave, keeping a hemiblock bounded.
        """
        activation_times: dict[str, float] = {}
        node_route: dict[str, str] = {}
        # Queue items: (node_name, activation_time, path_route)
        queue: deque[tuple[str, float, str]] = deque()
        queue.append((start_node, start_time, ROUTE_ANTEGRADE))

        while queue:
            node_name, t_activate, path_route = queue.popleft()

            # If reached by multiple paths, keep the earliest arrival
            if node_name in activation_times:
                if activation_times[node_name] <= t_activate:
                    continue

            # Refractory check
            if last_activation is not None:
                node = self.nodes.get(node_name)
                if node is not None:
                    t_last = last_activation.get(node_name, _NEG_INF)
                    if (t_activate - t_last) < node.refractory_period:
                        continue   # still refractory — block propagation

            activation_times[node_name] = t_activate
            node_route[node_name] = path_route

            for edge in self._adj.get(node_name, []):
                if edge.conductance <= 0.0:
                    continue  # complete block

                factor = min(1.0 / edge.conductance, _MAX_DELAY_MULTIPLIER)
                effective_delay = edge.base_delay * factor
                # Carry the most severe conduction class seen along this path so
                # a hemiblock re-route never gets "upgraded" to bundle morphology
                # (and vice-versa a bundle path stays bundle through hemiblock edges).
                next_route = (
                    edge.route
                    if _ROUTE_SEVERITY[edge.route] > _ROUTE_SEVERITY[path_route]
                    else path_route
                )
                queue.append((edge.target, t_activate + effective_delay, next_route))

        retrograde_nodes = frozenset(
            n for n, r in node_route.items() if r == ROUTE_BUNDLE
        )
        hemiblock_nodes = frozenset(
            n for n, r in node_route.items() if r == ROUTE_HEMIBLOCK
        )
        return activation_times, retrograde_nodes, hemiblock_nodes


# ---------------------------------------------------------------------------
# Physiological graph factory
# ---------------------------------------------------------------------------

def build_physiological_graph(params: SimulationParameters) -> ConductionGraph:
    """
    Construct the standard 13-node cardiac conduction graph from *params*.

    Anatomy
    -------
    ::

        SA_NODE ─────────────────────────────► RA (P wave onset)
           └──── Bachmann (~40 ms) ──────────► LA (P terminal)
        RA ─── atrial conduction (~75 ms) ───► AV_NODE
        AV_NODE ─── AV delay (from params) ──► HIS
        HIS ─────────────────────────────────► LBB, RBB
        LBB ─────────────────────────────────► SEPT_EARLY  (early QRS)
        LBB ─── LAF ─────────────────────────► LV_ANT      (mid QRS)
        LBB ─── LPF ─────────────────────────► LV_INF      (inferior QRS)
        LV_ANT ──────────────────────────────► LV_LAT      (dominant R)
        LV_ANT ──────────────────────────────► LV_POST     (terminal QRS)
        RBB ─────────────────────────────────► RV          (right QRS)

    Pathology mapping
    -----------------
    * AV block I°:   increase ``av_node.conduction_delay_ms``
    * AV block III°: set ``av_node.conductance = 0``
    * LBBB:          set ``his_bundle.left_branch_conductance = 0``
    * RBBB:          set ``his_bundle.right_branch_conductance = 0``
    * LAHB:          set ``his_bundle.left_anterior_conductance = 0``
    * LPHB:          set ``his_bundle.left_posterior_conductance = 0``
    """
    av = params.av_node
    his = params.his_bundle

    nodes = _build_nodes()

    # All delays in seconds
    half_his = his.his_delay_ms / 1000.0 / 2.0
    av_delay_s = av.conduction_delay_ms / 1000.0

    lbc = his.left_branch_conductance
    rbc = his.right_branch_conductance
    laf_c = his.left_anterior_conductance
    lpf_c = his.left_posterior_conductance

    edges = [
        # ── Atrial activation ───────────────────────────────────────
        ConductionEdge("SA_NODE",    "RA",          0.000, 1.0),
        ConductionEdge("SA_NODE",    "LA",          0.040, 1.0),   # Bachmann's bundle
        ConductionEdge("RA",         "AV_NODE",     0.075, 1.0),   # intra-atrial

        # ── AV junction ─────────────────────────────────────────────
        ConductionEdge("AV_NODE",    "HIS",         av_delay_s, av.conductance),
        ConductionEdge("HIS",        "LBB",         half_his,   1.0),
        ConductionEdge("HIS",        "RBB",         half_his,   1.0),

        # ── Left bundle branch → ventricular segments ────────────────
        # Early septal (leftward → rightward; produces Q in I, r in V1)
        ConductionEdge("LBB",        "SEPT_EARLY",  0.010, lbc),
        # Left anterior fascicle → anterior/lateral LV
        ConductionEdge("LBB",        "LV_ANT",      0.020, lbc * laf_c),
        # Left posterior fascicle → inferior LV
        ConductionEdge("LBB",        "LV_INF",      0.025, lbc * lpf_c),
        # Working myocardium spread from LV_ANT
        ConductionEdge("LV_ANT",     "LV_LAT",      0.020, 1.0),
        ConductionEdge("LV_ANT",     "LV_POST",     0.035, 1.0),

        # ── Right bundle branch → RV ─────────────────────────────────
        ConductionEdge("RBB",        "RV",          0.015, rbc),

        # ── Retrograde working-myocardium pathways ───────────────────
        # These edges are ALWAYS present in the graph but are silent in
        # normal rhythm: the forward path activates the target node first,
        # making it refractory before the retrograde signal arrives.
        # They become active only when the corresponding forward path is
        # blocked (bundle branch or fascicle block).
        #
        # ROUTE_BUNDLE → ECG engine uses broad Gaussians + discordant T (LBBB/RBBB).
        # ROUTE_HEMIBLOCK → mild broadening + concordant T; a severe fascicular
        #   block therefore stays a hemiblock instead of converging to LBBB,
        #   because the contralateral fascicle and His bundle remain intact.
        #
        # LBBB backup (RV → left ventricle, slow cell-to-cell spread):
        ConductionEdge("RV",         "LV_ANT",      0.080, 1.0, route=ROUTE_BUNDLE),
        ConductionEdge("RV",         "LV_INF",      0.085, 1.0, route=ROUTE_BUNDLE),
        # RBBB backup (LV_ANT → RV):
        ConductionEdge("LV_ANT",     "RV",          RBBB_RETRO_DELAY_S, 1.0, route=ROUTE_BUNDLE),
        # Left anterior hemiblock backup (LV_INF → LV_ANT):
        ConductionEdge("LV_INF",     "LV_ANT",      0.040, 1.0, route=ROUTE_HEMIBLOCK),
        # Left posterior hemiblock backup (LV_ANT → LV_INF):
        ConductionEdge("LV_ANT",     "LV_INF",      0.040, 1.0, route=ROUTE_HEMIBLOCK),
    ]

    return ConductionGraph(nodes, edges)


# ---------------------------------------------------------------------------
# Node definitions  (physiologically calibrated)
# ---------------------------------------------------------------------------

def _build_nodes() -> dict[str, ConductionNode]:
    """
    Define all 13 conduction nodes with waveform parameters.

    Calibration targets (normal sinus rhythm, 12-lead)
    ---------------------------------------------------
    * Lead I  R wave  ≈ 1.0–1.2 mV
    * Lead II R wave  ≈ 1.2–1.5 mV
    * aVF     R wave  ≈ 0.8–1.0 mV
    * V1      r/S     ≈ small r (< 0.3 mV), deep S
    * V5/V6   R wave  ≈ 1.2–1.5 mV
    * Lead II P wave  ≈ 0.15–0.25 mV, duration ≈ 80–100 ms
    * T/R ratio       ≈ 25–40 %  (concordant T waves)
    * QRS duration    ≈ 80–110 ms (incl. Gaussian tails)
    * QT interval     ≈ 380–430 ms at 70 bpm
    """

    def nd(
        name: str,
        pos: list[float],
        direction: list[float],
        da: float, dd: float,
        ra: float, rd: float, rdu: float,
        refract: float = 0.25,
    ) -> ConductionNode:
        d = np.array(direction, dtype=np.float64)
        norm = float(np.linalg.norm(d))
        if norm > 0.0:
            d = d / norm
        return ConductionNode(
            name=name,
            position=np.array(pos, dtype=np.float64),
            dipole_direction=d,
            depol_amplitude=da,
            depol_duration=dd,
            repol_amplitude=ra,
            repol_delay=rd,
            repol_duration=rdu,
            refractory_period=refract,
        )

    return {
        # ── Pacemaker / conduction-only nodes (no ECG contribution) ─────
        # SA_NODE refractory 250 ms: allows up to ~240 bpm.
        # ORIGINAL VALUE was 800 ms — that hard-capped effective HR at 75 bpm
        # because any CL < 800 ms triggered the refractory check.
        "SA_NODE": nd("SA_NODE",   [-0.7, -0.8,  0.1], [0, 1, 0],
                      da=0.0, dd=0.001, ra=0.0, rd=0.0, rdu=0.001, refract=0.25),
        # AV_NODE refractory 250 ms: consistent with SA_NODE; allows 1:1 conduction
        # up to ~240 bpm. ORIGINAL VALUE was 300 ms — would cause refractory SA
        # block at rates above ~200 bpm that are reachable via the HR spinner.
        "AV_NODE": nd("AV_NODE",   [-0.1,  0.1,  0.0], [0, 1, 0],
                      da=0.0, dd=0.001, ra=0.0, rd=0.0, rdu=0.001, refract=0.25),
        "HIS":     nd("HIS",       [ 0.0,  0.2,  0.2], [0, 1, 0],
                      da=0.0, dd=0.001, ra=0.0, rd=0.0, rdu=0.001, refract=0.25),
        "LBB":     nd("LBB",       [ 0.2,  0.3,  0.2], [0, 1, 0],
                      da=0.0, dd=0.001, ra=0.0, rd=0.0, rdu=0.001, refract=0.25),
        "RBB":     nd("RBB",       [-0.2,  0.3,  0.2], [0, 1, 0],
                      da=0.0, dd=0.001, ra=0.0, rd=0.0, rdu=0.001, refract=0.25),

        # ── Atrial nodes (P wave) ────────────────────────────────────────
        # RA: activates left + inferior → P-wave axis ≈ +60° (positive in I, II, aVF)
        # Small negative repol_amplitude = tiny atrial T wave (Ta), usually
        # hidden under the QRS/PR segment; negative = opposite to P wave.
        "RA":   nd("RA",    [-0.5, -0.3,  0.1], [ 0.60,  0.80,  0.00],
                   da=0.20, dd=0.060, ra=-0.04, rd=0.40, rdu=0.10, refract=0.20),
        # LA: slightly posterior component → notches the P terminal in V1
        "LA":   nd("LA",    [ 0.5, -0.3, -0.1], [ 0.30,  0.60, -0.70],
                   da=0.10, dd=0.055, ra=-0.02, rd=0.38, rdu=0.09, refract=0.20),

        # ── Ventricular nodes (QRS + T wave) ────────────────────────────
        # SEPT_EARLY: initial septal activation (left-to-right)
        #   Dipole points rightward + inferior + anterior
        #   → Q wave in I/aVL/V5/V6; small r in V1
        "SEPT_EARLY": nd("SEPT_EARLY", [ 0.05,  0.20,  0.50],
                          [-0.75,  0.40,  0.50],
                          da=0.30, dd=0.025, ra=0.05, rd=0.30, rdu=0.12),

        # RV: right ventricular free wall (rightward + inferior + anterior)
        #   Normally overwhelmed by LV; contributes to terminal S in V5/V6
        "RV":   nd("RV",    [-0.45,  0.30,  0.35], [-0.55,  0.55,  0.60],
                   da=0.25, dd=0.025, ra=0.08, rd=0.27, rdu=0.12),

        # LV_ANT: anterior LV / mid-septal (leftward + inferior + anterior)
        "LV_ANT": nd("LV_ANT",  [ 0.30,  0.35,  0.40], [ 0.50,  0.60,  0.60],
                     da=0.70, dd=0.028, ra=0.22, rd=0.29, rdu=0.14),

        # LV_LAT: lateral free wall — dominant R-wave generator
        #   Strongly leftward, slightly inferior, slightly posterior
        "LV_LAT": nd("LV_LAT",  [ 0.70,  0.30, -0.05], [ 0.85,  0.40, -0.30],
                     da=1.20, dd=0.030, ra=0.38, rd=0.28, rdu=0.15),

        # LV_INF: inferior LV (inferior axis contribution)
        "LV_INF": nd("LV_INF",  [ 0.30,  0.70,  0.05], [ 0.35,  0.90,  0.25],
                     da=0.55, dd=0.026, ra=0.16, rd=0.28, rdu=0.13),

        # LV_POST: posterior-basal LV, last to activate
        #   Dipole points superior + posterior → terminal S in inferior leads
        "LV_POST": nd("LV_POST", [ 0.40, -0.10, -0.50], [ 0.45, -0.70, -0.55],
                      da=0.28, dd=0.024, ra=0.07, rd=0.25, rdu=0.11),
    }
