# Cardiac Simulation Project – Konzept & Architektur

## Projektziel
Physiologisch korrekte Simulation der kardialen Elektrophysiologie mit 12-Kanal-EKG-Ausgabe.
Alle pathologischen EKG-Veränderungen emergieren aus Parameteränderungen der Grundphysiologie –
keine vorgegebenen Templates.

---

## Phasenplan

| Phase | Inhalt | Status |
|-------|--------|--------|
| 1 | EKG-Simulation (Elektrophysiologie) | 🔜 aktuell |
| 2 | Kardiale Pumpfunktion (Frank-Starling, PV-Schleifen) | geplant |
| 3 | Kreislaufregulation (Windkessel, Barorezeptor) | geplant |

---

## Architektur: Kausale Kette

```
Ionenkanäle (ODE)
    ↓
Zelluläres Aktionspotential
    ↓
Erregungsausbreitung im Gewebe (PDE, Reaktions-Diffusion)
    ↓
Dipol-Vorwärtsmodell
    ↓
12-Kanal-EKG (physikalische Projektion auf Elektroden)
```

---

## Schicht 1: Zelluläre Ionenkanalmodelle (Hodgkin-Huxley-artig)

Jeder Zelltyp hat ein eigenes ODE-System:

| Zelltyp | Modell (Literatur) | Besonderheit |
|---------|-------------------|--------------|
| Sinusknoten | Zhang et al. (2000) | If-Strom (funny current) → Automatizität |
| Vorhof-Myozyt | Courtemanche et al. (1998) | Kurze Plateau-Phase |
| AV-Knoten | Inada et al. (2009) | Langsame Ca²⁺-abhängige Überleitung |
| His-Purkinje | DiFrancesco-Noble | Schnelle Überleitung, lange Refraktärzeit |
| Ventrikel-Myozyt | ten Tusscher-Panfilov (2006) | Langes Plateau, IK1-dominiert |

### Wichtigste Ionenströme
- **If** (funny current, HCN-Kanäle): Schrittmacherstrom im Sinusknoten
- **INa**: Schneller Natriumeinstrom (Depolarisation)
- **ICaL**: L-Typ Calciumstrom (Plateau-Phase)
- **IKr, IKs**: Verzögerte Kaliumströme (Repolarisation)
- **IK1**: Einwärts-Gleichrichter (Ruhemembranpotential)
- **INaCa**: Natrium-Calcium-Austauscher

---

## Schicht 2: Erregungsausbreitung (Gewebsebene)

### Reaktions-Diffusions-Gleichung (Monodomain-Näherung)
```
∂V/∂t = (1/Cm) · [∇·(D·∇V) - I_ion]
```

### Leitungsgeschwindigkeiten (physiologisch)
| Struktur | Geschwindigkeit |
|----------|----------------|
| Vorhofmyokard | 0.5–1.0 m/s |
| AV-Knoten | 0.02–0.05 m/s (verzögert!) |
| His-Bündel | 1.5–2.5 m/s |
| Tawara-Schenkel | 2.0–3.0 m/s |
| Purkinje-Fasern | 3.0–4.0 m/s |
| Ventrikelmuskel | 0.3–0.8 m/s |

### Vereinfachtes 1D-Leitungssystem (Phase 1 Einstieg)
```
SA-Knoten → Vorhof → AV-Knoten → His → Tawara (L/R) → Purkinje → Ventrikel
```
Dieses 1D-Modell liefert bereits korrektes EKG-Timing und ist der empfohlene Einstieg.

---

## Schicht 3: EKG-Vorwärtsmodell

### Dipolnäherung
```
φ(r) = (1/4πσ) · Σ [p_i / |r - r_i|²]
```
- Jede Gewebszone erzeugt einen Dipol proportional zu ∇Vm
- Elektroden an Standardpositionen (Mason-Likar / klinischer Standard)

### 12-Kanal-Berechnung
```
Extremitätenableitungen: I, II, III (Einthoven)
Verstärkte Ableitungen:  aVR, aVL, aVF
Brustwandableitungen:    V1–V6 (Wilson)
```

---

## HRV: Komplexe Systemdynamik

HRV entsteht aus dem Zusammenspiel mehrerer physiologischer Oszillatoren:

| Mechanismus | Frequenzband | Umsetzung |
|-------------|-------------|-----------|
| Parasympathikus (Vagus) | HF: 0.15–0.4 Hz | Respiratorische Sinusarrhythmie |
| Sympathikus/Parasympathikus-Balance | LF: 0.04–0.15 Hz | Van-der-Pol-Oszillator |
| Barorezeptorreflex | Mayer-Wellen ~0.1 Hz | Gekoppelter Kreislaufoszillator |
| Fraktale Langzeit-Korrelationen | 1/f-Charakteristik | Multiskalige Modulation |

**Validierung**: DFA (Detrended Fluctuation Analysis) sollte α ≈ 1.0–1.2 ergeben.

### Implementierung
- Autonomes Nervensystem moduliert If-Strom des Sinusknotens
- Sympathikus: erhöht If → höhere Herzfrequenz
- Parasympathikus (ACh): reduziert If + erhöht IKACh → niedrigere HF

---

## Pathologiemodell: Emergenz aus Parameterstörungen

**Prinzip**: Keine EKG-Templates – Pathologien entstehen durch Änderung physiologischer Parameter.

### Erregungsbildungsstörungen
| Störung | Mechanismus | Erwartetes EKG |
|---------|-------------|----------------|
| Sinusbradykardie | If-Strom ↓ (Parasympathikus ↑) | Frequenz < 60/min |
| Sinustachykardie | If-Strom ↑ (Sympathikus ↑) | Frequenz > 100/min |
| VES | Ektoper Fokus im Ventrikel | Breiter QRS, kompensatorische Pause |
| VT | Reentry (verkürzte Refraktärzeit + Leitungsverzögerung) | Breite QRS-Tachykardie |
| Kammerflimmern | Chaotische Reentry-Wellen | Unregelmäßige Undulationen |

### Erregungsleitungsstörungen
| Störung | Mechanismus | Erwartetes EKG |
|---------|-------------|----------------|
| AV-Block I° | AV-Leitgeschwindigkeit ↓ | PQ > 200ms |
| AV-Block II° Mobitz I | Progressive AV-Ermüdung | Wenckebach-Periodik |
| AV-Block II° Mobitz II | Intermittierender AV-Block | Plötzlicher QRS-Ausfall |
| AV-Block III° | Vollständige AV-Blockade | P/QRS-Dissoziation |
| Linksschenkelblock | σ linker Tawara-Schenkel → 0 | Breiter QRS, M-Form V5/V6, neg. V1 |
| Rechtsschenkelblock | σ rechter Tawara-Schenkel → 0 | rSR' V1, breites S in I/V6 |
| Linksanteriorer Hemiblock | σ linkes anteriores Faszikel → 0 | Linksachsenabweichung |

### Vorhofrhythmusstörungen
| Störung | Mechanismus | Erwartetes EKG |
|---------|-------------|----------------|
| Vorhofflimmern | Heterogenität ↑ + Refraktärzeit ↓ → Reentry-Wellen | Irreguläre f-Wellen, unregelmäßige RR |
| Vorhofflattern | Makro-Reentry im rechten Vorhof (300/min) | Sägezahnwellen, regelmäßige Überleitung |
| AVNRT | Reentry im AV-Knoten | Schmale QRS-Tachykardie, P in QRS |

### Ischämie & Infarkt
| Störung | Mechanismus | Erwartetes EKG |
|---------|-------------|----------------|
| Subendokardiale Ischämie | AP-Verkürzung subendokardial (IK↑, ICaL↓) | ST-Senkung |
| STEMI anterior (LAD) | Depolarisationsblock V1–V4 + If-Verlust | ST-Hebung V1–V4, R-Verlust |
| STEMI inferior (RCA) | Depolarisationsblock II, III, aVF | ST-Hebung II, III, aVF |
| STEMI lateral (LCX) | Depolarisationsblock I, aVL, V5–V6 | ST-Hebung lateral |
| Posteriorer Infarkt | Depolarisationsblock Hinterwand | Spiegelbildlich V1–V3 |

---

## Projektstruktur

```
cardiac_sim/
├── CONCEPT.md                    # Diese Datei
├── README.md
├── requirements.txt
├── main.py                       # Einstiegspunkt
│
├── core/
│   ├── cell_models/
│   │   ├── __init__.py
│   │   ├── base_cell.py          # Abstrakte Basisklasse
│   │   ├── sinoatrial.py         # Zhang-Modell (Sinusknoten)
│   │   ├── atrial.py             # Courtemanche-Modell
│   │   ├── avnode.py             # Inada-Modell
│   │   ├── purkinje.py           # DiFrancesco-Noble
│   │   └── ventricular.py        # ten Tusscher-Panfilov
│   │
│   ├── tissue/
│   │   ├── __init__.py
│   │   ├── conduction_system.py  # 1D-Leitungssystem
│   │   ├── grid_2d.py            # 2D-Gewebsgitter (Phase 1 Erweiterung)
│   │   ├── propagation.py        # PDE-Solver
│   │   └── conductance.py        # Anisotropie, Gewebsparameter
│   │
│   └── ecg/
│       ├── __init__.py
│       ├── forward_model.py      # Dipolmodell
│       ├── electrodes.py         # Elektrodenpositionen (Standard)
│       └── leads.py              # 12-Kanal-Berechnung
│
├── physiology/
│   ├── __init__.py
│   ├── hrv.py                    # HRV-Oszillatoren
│   ├── autonomic.py              # Sympathikus/Parasympathikus
│   └── reflex.py                 # Barorezeptor (Phase 3)
│
├── pathology/
│   ├── __init__.py
│   ├── parameter_sets.py         # Pathologische Parameterprofile
│   ├── arrhythmias.py            # Rhythmusstörungen
│   ├── ischemia.py               # Ischämie/Infarkt
│   └── conduction_blocks.py      # Leitungsblockaden
│
├── simulation/
│   ├── __init__.py
│   ├── engine.py                 # Hauptsimulationsloop
│   └── solver.py                 # Numerische ODE/PDE-Löser
│
└── visualization/
    ├── __init__.py
    ├── ecg_display.py            # 12-Kanal EKG-Anzeige
    └── activation_map.py         # Erregungsausbreitung (Animation)
```

---

## Technologie-Stack

```
requirements.txt:
numpy>=1.24
scipy>=1.10
matplotlib>=3.7
numba>=0.57          # JIT-Kompilierung für PDE-Solver
pyqtgraph>=0.13      # Echtzeit-EKG-Darstellung
PyQt6>=6.4           # GUI
```

---

## Empfohlene Startreihenfolge (Copilot-Prompts)

### Schritt 1: Sinusknoten
```
Implementiere das Zhang et al. (2000) Sinusknoten-Zellmodell in Python.
ODE-System mit If (funny current, HCN), ICaL, IKr, INa, INaCa.
Klasse SinoatrialCell mit Methode compute_derivatives(t, state).
```

### Schritt 2: Weitere Zelltypen
```
Implementiere das ten Tusscher-Panfilov (2006) Ventrikelzellmodell.
Alle Ionenströme als separate Methoden, Parameter als Klassenkonstanten.
```

### Schritt 3: 1D-Leitungssystem
```
Implementiere ein 1D-Leitungssystem: SA → Vorhof → AV-Knoten → His →
Tawara (L/R) → Purkinje → Ventrikel. Kopplung über Gap-Junctions
(elektrotonische Kopplung). Unterschiedliche Leitungsgeschwindigkeiten
pro Segment.
```

### Schritt 4: EKG-Vorwärtsmodell
```
Implementiere ein Dipolmodell für 12-Kanal-EKG.
Elektroden nach Standard-12-Kanal-Schema (Einthoven-Dreieck + Wilson).
Input: Vm(t) pro Gewebszone. Output: 12 Ableitungen.
```

### Schritt 5: Pathologieframework
```
Implementiere ein Pathologieframework: Funktionen, die Gewebsparameter
modifizieren (Leitfähigkeit, Refraktärzeit, Ionenkanalamplituden).
Beispiel: av_block(degree=3) setzt AV-Leitfähigkeit auf 0.
```

---

## Literatur (Schlüsselreferenzen)

- **Zhang et al. (2000)**: Mathematical models of action potentials in the periphery and center of the rabbit sinoatrial node. *Am J Physiol Heart Circ Physiol*
- **Courtemanche et al. (1998)**: Ionic mechanisms underlying human atrial action potential properties. *Am J Physiol*
- **ten Tusscher & Panfilov (2006)**: Alternans and spiral breakup in a human ventricular tissue model. *Am J Physiol Heart Circ Physiol*
- **Inada et al. (2009)**: One-dimensional mathematical model of the atrioventricular node. *Circ Res*
- **Malmivuo & Plonsey (1995)**: Bioelectromagnetism. Oxford University Press. *(Vorwärtsmodell/EKG)*
- **Goldberger et al. (2002)**: PhysioBank, PhysioToolkit, PhysioNet. *(HRV-Analyse)*

---

*Erstellt: Mai 2026 | Projekt: cardiac_sim | Phase: 1 – EKG-Simulation*
