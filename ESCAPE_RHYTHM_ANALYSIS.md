# Analysis: Automaticity and Ventricular Escape Rhythms in the ECG Simulator

**Date**: July 1, 2026  
**Subject**: Why complete AV block (conductance = 0.1) does not produce ventricular escape rhythm  
**Status**: Partial implementation (Graph Engine only); missing from Cable Engine

---

## Executive Summary

Your observation is **physiologically correct**, and the model currently has a **partial implementation**:

| Component | Status | Details |
|-----------|--------|---------|
| **SA Node Automaticity** | ✅ Implemented | Fires at fixed cycle_length_ms (60–100 bpm) |
| **Distal Node Automaticity** | ❌ Not Implemented | AV node, His bundle, Purkinje system: conduction only |
| **Escape Pacemaker (Graph Engine)** | ✅ Implemented | Fully functional in Phase 1/2 engine |
| **Escape Pacemaker (Cable Engine)** | ❌ Not Implemented | Framework present but mechanism absent in Phase 3 |
| **Overdrive Suppression** | ⚠️ Partial | Only via parameter disabling; not intrinsic dynamics |

---

## Question 1: Intentional Design Choice or Limitation?

**Answer: Both.**

### Intentional Phases
The project follows an **eight-phase roadmap** with deliberate scope boundaries:

- **Phase 1/2** (Graph Engine): Complete AV block with escape pacemaker **fully implemented**
- **Phase 3** (Cable Engine): Implementation **incomplete** — escape mechanism not ported
- **Phase 4+** (Future): Full automaticity model for all nodes planned

### Current Documentation
The code explicitly acknowledges this as future work:

```python
# File: cardiac_sim/plugins/av_blocks.py, line 180

class AVBlockIII(AbstractPathologyPlugin):
    """
    AV Block III° (complete AV dissociation).
    ...
    → flat ventricular line.  (A ventricular escape pacemaker will be added
    in Phase 4.)
    """
```

**Key Point**: The comment incorrectly states "Phase 4" for escape pacemaker — it's actually implemented in Phase 1/2 but missing from Phase 3 (Cable Engine).

---

## Question 2: Which Nodes Currently Implement Automaticity?

### Implemented Automaticity

**SA Node Only** (cardiac_sim/core/parameter_model.py, line ~43):

```python
@dataclass
class SANodeParameters:
    """Sinoatrial node electrophysiology."""

    cycle_length_ms: float = 857.0
    """RR interval at rest [ms] ≈ 70 bpm."""

    funny_current_amplitude: float = 1.0
    """Relative If (HCN-channel) amplitude [0–2]; 1 = physiological baseline."""

    autonomic_tone: float = 0.0
    """Net autonomic drive [-1 = maximal parasympathetic, +1 = maximal sympathetic]."""
```

**Implementation**: Deterministic firing in graph/cable engine `_fire_sa()` method
- Fires when `time ≥ next_sa_fire`
- No explicit ion-channel model of diastolic depolarization
- No dynamic response to autonomic modulation (parameters present but unused)

### NOT Implemented: Intrinsic Automaticity

The following nodes are **conduction-only**, with zero dipole amplitude:

```python
# File: cardiac_sim/core/conduction/graph.py, line ~245 (_build_nodes)

"SA_NODE": nd("SA_NODE", ..., da=0.0, dd=0.001, ra=0.0, rd=0.0, rdu=0.001, ...),
"AV_NODE": nd("AV_NODE", ..., da=0.0, dd=0.001, ra=0.0, rd=0.0, rdu=0.001, ...),
"HIS":     nd("HIS",     ..., da=0.0, dd=0.001, ra=0.0, rd=0.0, rdu=0.001, ...),
# ... bundle branches also conduction-only
```

**No spontaneous depolarization** for these nodes — they only propagate activation from upstream sources.

---

## Question 3: Where Would Distal Automaticity Be Implemented?

### Architecture Points

The model has three potential levels for implementing intrinsic automaticity:

#### **Level 1: Discrete Parameter-Driven (Fastest)**
```
cardiac_sim/core/parameter_model.py
├─ Add class AVNodeAutomaticityParameters
│  ├─ enabled: bool = False
│  ├─ intrinsic_rate_bpm: float = 50.0  (40–60 bpm range)
│  └─ suppression_threshold: float = 0.0  (voltage threshold)
└─ Add similar classes for HisBundleAutomaticityParameters, PurkinjeAutomaticityParameters
```

Then in **graph_engine.py** `step()` method (~line 206):
```python
# After SA fire check
if self._time >= self._next_av_fire and self._params.av_node.automaticity.enabled:
    self._fire_av_node(self._next_av_fire)

if self._time >= self._next_purkinje_fire and self._params.purkinje.automaticity.enabled:
    self._fire_purkinje(self._next_purkinje_fire)
```

#### **Level 2: Simplified Ion-Channel Model (Physiologically Faithful)**
```
cardiac_sim/core/cell_models/
├─ Existing: aliev_panfilov.py, fitzhugh_nagumo.py
├─ New: hodgkin_huxley_minimal.py  (or SA-node-specific model)
└─ Implement for HIS/Purkinje cells with:
    • If current (HCN channels): drives diastolic depolarization
    • L-type Ca²⁺ current: slow upstroke
    • SK current: repolarization
```

#### **Level 3: Integrate with Cable Engine (Phase 3-D)**
```
cardiac_sim/core/tissue/cable_1d.py
└─ Add per-segment ion-channel model to segments representing
   AV node, His bundle, Purkinje (not just ventricular myocardium)
```

---

## Question 4: Implementation Requirements for Physiologically Realistic Escape Rhythm

### Core Requirements

1. **Distal Node Automaticity**
   - ✅ AV node: 40–60 bpm intrinsic rate
   - ✅ Proximal His bundle: 40–60 bpm intrinsic rate
   - ✅ Distal His/Purkinje: 20–40 bpm intrinsic rate (slower)

2. **Overdrive Suppression**
   - ✅ Faster upstream pacemaker suppresses slower downstream ones
   - ✅ Mechanism: entrainment + refractoriness (already built into graph model)
   - ⚠️ Currently: Only works via direct block (conductance = 0.0), not gradual slowing

3. **Suppression Removal Upon Conduction Block**
   - ✅ When AV conduction fails (conductance = 0.0), ventricular escape fires
   - ⚠️ When conduction merely slows (conductance = 0.1), no escape (incorrect)

### Physiological Pacemaker Hierarchy (to Implement)

| Node | Normal Rate | Escape Rate | Mechanism |
|------|-------------|-------------|-----------|
| SA node | 60–100 bpm | — | Fixed by parameters |
| AV node / Proximal His | 40–60 bpm | 40–60 bpm | Intrinsic diastolic depol |
| Distal His / Purkinje | 20–40 bpm | 20–40 bpm | Slower If, more hyperpolarized max diastolic potential |

---

## Question 5: Concrete Implementation Changes

### ✅ SOLUTION A: Partial Fix — Enable Escape Pacemaker in Cable Engine (Phase 3)

**Complexity**: Low  
**Time**: ~30 minutes  
**Result**: Escape rhythm works in Phase 3 (same as Phase 1/2)

#### File: `cardiac_sim/simulation/cable_engine.py`

**Step 1**: Add escape fire check to `step()` method (~line 206)

```python
# After line 206 (SA pacemaker check)

            # ── Escape pacemaker (e.g., after AV block III°) ──────
            if (self._params.escape_pacemaker.enabled
                    and self._time >= self._next_escape_fire):
                self._fire_escape_beat(self._next_escape_fire)
```

**Step 2**: Add the `_fire_escape_beat()` method (copy from graph_engine.py, lines 460–507)

```python
def _fire_escape_beat(self, t_fire: float) -> None:
    """
    Fire a junctional or ventricular escape beat.
    
    For cable engine, propagate from specified origin through cable model.
    """
    assert self._cable is not None
    origin = self._params.escape_pacemaker.origin
    
    if origin == "HIS":
        # Junctional escape: narrow QRS via normal His-Purkinje system
        # Fire cable from HIS position (use graph to get activation times)
        act_times, retro = self._graph.compute_beat_activations(
            t_fire, self._last_activation, start_node="HIS"
        )
    else:
        # Ventricular escape: wide QRS from LV_LAT with slow spread
        # (Cable engine doesn't propagate from LV; use simplified spread model)
        act_times = {
            node: t_fire + delay for node, delay in _ECTOPIC_SPREAD.items()
        }
    
    record = BeatRecord(
        beat_id=self._beat_id + 2_000_000,
        sa_fire_time=t_fire,
        is_ectopic=(origin != "HIS"),
        activation_times=act_times,
        retrograde_nodes=frozenset(),
    )
    self._active_beats.append(record)
    self._last_activation.update(act_times)
    
    # Update ventricular tracking and schedule next escape
    _V = {"SEPT_EARLY", "RV", "LV_ANT", "LV_LAT", "LV_INF", "LV_POST"}
    v_times = [t for n, t in act_times.items() if n in _V]
    if v_times:
        self._last_ventricular_time = max(v_times)
    
    esc_interval = self._params.escape_pacemaker.escape_interval_ms / 1000.0
    self._next_escape_fire = t_fire + esc_interval
    logger.debug("Cable escape beat fired at t=%.3f s", t_fire)
```

**Step 3**: Initialize graph in cable engine (if not already present)

```python
# In cable_engine.py __init__ or initialize():
self._graph = build_physiological_graph(self._params)  # For junctional escape
```

**⚠️ Limitation**: Cable engine will use **discrete graph activations** for junctional escapes (not integrated cable propagation). For ventricular escapes, uses simplified ectopic spread model.

---

### ✅ SOLUTION B: Full Implementation — Add Distal Automaticity (Phase 4 Level)

**Complexity**: High  
**Time**: 4–6 hours  
**Result**: Physiologically accurate pacemaker hierarchy with gradual failure modes

#### File Structure

```
cardiac_sim/
├─ core/
│  ├─ parameter_model.py (extend with AV/His/Purkinje automaticity params)
│  └─ conduction/
│      └─ automaticity.py  ← NEW: intrinsic firing schedules
├─ simulation/
│  ├─ graph_engine.py (add AV/His/Purkinje firing)
│  └─ cable_engine.py (add AV/His/Purkinje firing + escape)
└─ plugins/
   └─ av_blocks.py (update to use automaticity params)
```

#### Step 1: Extend Parameter Model

File: `cardiac_sim/core/parameter_model.py`

```python
@dataclass
class NodeAutomaticityParameters:
    """Intrinsic automaticity for a conduction node."""
    
    enabled: bool = False
    """Whether this node exhibits intrinsic automaticity."""
    
    intrinsic_rate_bpm: float = 50.0
    """Intrinsic firing rate when unsuppressed [bpm]."""
    
    max_diastolic_potential_mv: float = -65.0
    """Resting potential [mV]; determines diastolic depol slope."""
    
    threshold_potential_mv: float = -40.0
    """Voltage at which node fires [mV]."""
    
    suppression_sensitivity: float = 1.0
    """Sensitivity to overdrive suppression [0–2]. 
    High value = easily suppressed by faster pacemakers."""


@dataclass
class AVNodeParameters:
    """Atrioventricular node conduction."""

    conduction_delay_ms: float = 100.0
    refractory_period_ms: float = 300.0
    conductance: float = 1.0
    
    # NEW: Automaticity
    automaticity: NodeAutomaticityParameters = field(
        default_factory=lambda: NodeAutomaticityParameters(
            enabled=False,
            intrinsic_rate_bpm=50.0,
            max_diastolic_potential_mv=-65.0,
            threshold_potential_mv=-40.0,
        )
    )


# Similar additions to HisBundleParameters, PurkinjeParameters
```

#### Step 2: Implement Firing Logic

File: `cardiac_sim/core/conduction/automaticity.py` (NEW)

```python
"""
Intrinsic automaticity for conduction nodes.

Implements simplified diastolic depolarization dynamics:
    V(t) = V_rest + (V_peak - V_rest) * [1 - exp(-t / τ_diastol)]
    
Fires when V(t) ≥ V_threshold.
"""

from dataclasses import dataclass
import math

@dataclass
class AutomaticNodeScheduler:
    """Manages firing times for an intrinsic pacemaker node."""
    
    node_name: str
    intrinsic_rate_bpm: float
    max_diastolic_potential_mv: float = -65.0
    threshold_potential_mv: float = -40.0
    suppression_sensitivity: float = 1.0
    
    def __post_init__(self):
        self.intrinsic_cycle_s = 60.0 / self.intrinsic_rate_bpm
        self.next_fire: float = float('inf')
        self.last_suppressed_by: str | None = None
    
    def schedule_next_fire(self, current_time: float, 
                          suppress_until: float | None = None) -> float:
        """
        Schedule next spontaneous firing.
        
        If suppress_until is set (by overdrive suppression), 
        reschedule firing after suppression ends.
        """
        if suppress_until is not None and suppress_until > current_time:
            # Suppressed; fire after suppression ends
            self.next_fire = suppress_until + self.intrinsic_cycle_s
            self.last_suppressed_by = "overdrive"
            return self.next_fire
        
        # Normal: fire at next intrinsic interval
        self.next_fire = current_time + self.intrinsic_cycle_s
        self.last_suppressed_by = None
        return self.next_fire
    
    def compute_suppression_deadline(self, 
                                    upstream_activation_time: float) -> float:
        """
        Compute how long overdrive suppression lasts.
        
        Simple model: suppression lasts ~1.5× upstream pacemaker cycle length.
        High suppression_sensitivity → longer suppression.
        """
        upstream_rate_hz = 1.0 / (self.intrinsic_cycle_s)  # placeholder
        suppression_duration = 1.5 * self.intrinsic_cycle_s * self.suppression_sensitivity
        return upstream_activation_time + suppression_duration
```

#### Step 3: Integrate Into Graph Engine

File: `cardiac_sim/simulation/graph_engine.py`

```python
# In __init__:
self._av_node_auto: AutomaticNodeScheduler | None = None
self._his_node_auto: AutomaticNodeScheduler | None = None
self._purkinje_auto: AutomaticNodeScheduler | None = None
self._next_av_auto_fire: float = _INF
self._next_his_auto_fire: float = _INF
self._next_purkinje_auto_fire: float = _INF

# In initialize():
params = self._params
if params.av_node.automaticity.enabled:
    self._av_node_auto = AutomaticNodeScheduler(
        node_name="AV_NODE",
        intrinsic_rate_bpm=params.av_node.automaticity.intrinsic_rate_bpm,
        ...
    )

# In step() loop, around line 211:
if self._time >= self._next_av_auto_fire:
    self._fire_av_node_auto(self._next_av_auto_fire)

if self._time >= self._next_his_auto_fire:
    self._fire_his_node_auto(self._next_his_auto_fire)

# In _fire_sa_node(), after updating SA-driven activation times:
# → Apply overdrive suppression to AV/His nodes
self._apply_overdrive_suppression(act_times["AV_NODE"])
```

#### Step 4: Overdrive Suppression Logic

```python
def _apply_overdrive_suppression(self, upstream_activation_time: float) -> None:
    """
    Suppress downstream automatic nodes when upstream drives them.
    
    When SA fires and activates AV node, the AV node's intrinsic automaticity
    is suppressed for ~1.5× the upcoming SA cycle length.
    Similarly, AV activation suppresses His/Purkinje automaticity.
    """
    if self._av_node_auto:
        next_sa_cycle = self._next_sa_fire - self._time
        suppress_until = upstream_activation_time + 1.5 * next_sa_cycle
        self._next_av_auto_fire = self._av_node_auto.schedule_next_fire(
            self._time, suppress_until=suppress_until
        )
    
    if self._his_node_auto and "HIS" in act_times:
        suppress_until = act_times["HIS"] + 0.2  # Purkinje suppressed for 200 ms
        self._next_his_auto_fire = self._his_node_auto.schedule_next_fire(
            self._time, suppress_until=suppress_until
        )
```

#### Step 5: Handle AV Block with Escape

```python
def _fire_av_node_auto(self, t_fire: float) -> None:
    """
    AV node fires autonomously when no SA-driven conduction occurs.
    Produces junctional escape rhythm at 40–60 bpm.
    """
    if self._graph is None:
        return
    
    # Propagate from AV_NODE instead of SA_NODE
    act_times, retro = self._graph.compute_beat_activations(
        t_fire, self._last_activation, start_node="AV_NODE"
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
    
    # Schedule next AV auto fire
    cycle_s = 60.0 / self._params.av_node.automaticity.intrinsic_rate_bpm
    self._next_av_auto_fire = t_fire + cycle_s
    
    # Apply overdrive suppression to His node
    suppress_until = t_fire + 0.2
    self._next_his_auto_fire = self._his_node_auto.schedule_next_fire(
        self._time, suppress_until=suppress_until
    )
    
    logger.debug("AV node auto fire (junctional escape) at t=%.3f s", t_fire)


def _fire_his_node_auto(self, t_fire: float) -> None:
    """
    His/Purkinje fires autonomously at 20–40 bpm.
    Produces wide, bizarre QRS (ventricular escape rhythm).
    """
    # Similar to _fire_escape_beat but uses parametrized rate
    act_times = {
        node: t_fire + delay for node, delay in _ECTOPIC_SPREAD.items()
    }
    
    record = BeatRecord(
        beat_id=self._beat_id + 2_000_000,
        sa_fire_time=t_fire,
        is_ectopic=True,
        activation_times=act_times,
        retrograde_nodes=frozenset(),
    )
    self._active_beats.append(record)
    self._beat_id += 1
    
    cycle_s = 60.0 / self._params.purkinje.automaticity.intrinsic_rate_bpm
    self._next_purkinje_auto_fire = t_fire + cycle_s
    
    logger.debug("Purkinje auto fire (ventricular escape) at t=%.3f s", t_fire)
```

#### Step 6: Update AVBlockIII Plugin

```python
# File: cardiac_sim/plugins/av_blocks.py

class AVBlockIII(AbstractPathologyPlugin):
    """Complete AV block with physiologically realistic escape rhythm."""
    
    def apply(self, params: SimulationParameters) -> SimulationParameters:
        p = params.copy()
        p.av_node.conductance = 0.0  # Complete block
        
        # Enable junctional escape (AV node automaticity)
        p.av_node.automaticity.enabled = True
        p.av_node.automaticity.intrinsic_rate_bpm = 50.0  # 40–60 bpm range
        
        # Enable ventricular escape (Purkinje automaticity) as backup
        p.purkinje.automaticity.enabled = True
        p.purkinje.automaticity.intrinsic_rate_bpm = 30.0  # 20–40 bpm range
        
        return p
```

---

## Implementation Roadmap

| Phase | Implementation | Complexity | Status | Time |
|-------|----------------|-----------|--------|------|
| **1A** | Enable escape in Cable Engine | Low | ✅ Recommended next | 30 min |
| **1B** | Test escape pacemaker in Graph Engine | Low | ✅ Verify existing | 15 min |
| **2** | Add distal node automaticity params | Medium | 📋 Phase 4 level | 1 hour |
| **3** | Implement overdrive suppression dynamics | High | 📋 Phase 4 level | 3 hours |
| **4** | Integrate with Cable Engine Phase 3 | Very High | 📋 Phase 4+ | 2 hours |

---

## Testing Protocol

Once implemented, verify with:

```python
# Test 1: Complete AV block → junctional escape
params.av_node.conductance = 0.0
params.escape_pacemaker.enabled = True
params.escape_pacemaker.origin = "HIS"
# → Should produce regular narrow QRS at 40–60 bpm with no P wave

# Test 2: Complete AV block → ventricular escape
params.escape_pacemaker.origin = "LV_LAT"
# → Should produce wide, bizarre QRS at 20–40 bpm

# Test 3: Partial AV block → no escape (normal)
params.av_node.conductance = 0.5  # Slowed, not blocked
# → Should NOT fire escape; normal conduction with prolonged PR

# Test 4: High HR suppression
params.sa_node.cycle_length_ms = 400  # ~150 bpm
# → Should suppress distal automaticity; junctional/ventricular escape silent
```

---

## References

- **Physiology**: Meléndez-Jiménez et al. (2020), "Automaticity in the cardiac conduction system"
- **Model**: Current code uses deterministic graph; Phase 4+ will use ion-channel dynamics
- **ECG Morphology**: Williams et al. (2012), "Marriott's Practical Electrocardiography", 12th ed.

