# Physics models

What TwinLight computes, which published formulation each part follows, and —
just as importantly — where the implementation simplifies its source. Read the
[limitations](#known-limitations) section before quoting numbers from the twin
in a paper.

Reference numbers in brackets point at the
[References](../README.md#references) section of the main README. Citation keys
in `[monospace]` are the keys used in source-code comments, so any equation here
can be traced to the line that implements it.

## Structure

```
             ┌──────────────────────────────────────────┐
  topology → │  QoT baseline (once per lightpath)       │ → OpmBaseline
             │  GNPy split-step  or  closed-form GN/EGN │
             └──────────────────────────────────────────┘
                                 │  cached
                                 ▼
             ┌──────────────────────────────────────────┐
   time t →  │  Transient layer (every read)            │ → measurements
             │  EDFA · polarization · EEPN · thermal    │
             └──────────────────────────────────────────┘
```

The split matters: the baseline is expensive and stable, so it is computed once
per service and cached; the transient layer is cheap and is deliberately *not*
cached, so every OPM read reflects the instant it was made.

## 1. QoT baseline

Both backends produce the same backend-agnostic `OpmBaseline` — GSNR, OSNR,
accumulated chromatic dispersion, mean PMD, latency — so downstream code and
results stay comparable.

### GNPy backend (default)

Split-step propagation through the designed network using the `gnpy` library
[[10]](../README.md#ref-10). Practical details that matter:

- Amplifier placement and power design happen once at startup via
  `designed_network(equipment, network)`.
- Propagation uses an **88-channel C-band comb**. This is not cosmetic: GNPy's
  EDFA model derives its slot width from `channel_freq[1] - channel_freq[0]`
  and needs at least two channels to interpolate.
- Path elements are deep-copied before propagation, because propagation mutates
  element state and the cached network must not drift.
- `si.pmd` and `si.latency` are per-channel arrays, not scalars.

### EGN backend

A closed-form Gaussian-Noise model implemented directly in
[`physics/egn_kernel.py`](../src/twinlight/physics/egn_kernel.py), with no
dependency beyond NumPy. For a channel over a chain of amplified spans:

```
GSNR = P_signal / (P_ASE + P_NLI)
```

with per-span contributions accumulated linearly in the noise-power domain
(Carena *et al.* 2012, Eq. 1–3 `[Carena_2012]` [[8]](../README.md#ref-8)), the
EGN correction from Carena *et al.* 2014 `[Carena_2014]`
[[9]](../README.md#ref-9), and the lightpath-GSNR abstraction of Curri 2022
`[Curri_2022]` [[10]](../README.md#ref-10).

Standard SSMF parameters are used as defaults: |β₂| = 21.3 ps²/km,
γ = 1.3 W⁻¹km⁻¹.

The kernel's structure derives from the Cython QoT kernel in
[optical-networking-gym][ong] [[18]](../README.md#ref-18), a project by the same
author.

**Scope limit:** the current kernel models **self-channel NLI only**. Cross-channel
XPM/FWM contributions from neighbouring services are not included, so on a
densely loaded link the EGN backend is optimistic — typically by a few dB of
GSNR relative to the GNPy backend on the bundled scenarios.

**Shared amplifier placement.** The GN model charges ASE *per span*, on the
assumption that each span ends in an amplifier that restores the launch power.
A topology of bare fiber spans therefore cannot be fed to the kernel directly —
a single un-split 336 km span would be modelled as one amplifier making up
~67 dB, and GSNR would land hundreds of dB below anything physical.

To avoid that, when an equipment library is configured the EGN backend runs the
topology through GNPy's `designed_network()` — the same amplifier placement the
GNPy backend uses — and consumes the result. `designed_network()` splits long
fibers into ~90 km sub-spans and inserts an EDFA after each, and the designed
network is serialised back to GNPy JSON (via `network_to_json`) so the EGN
converter reads it unchanged. Both backends therefore propagate over *identical*
spans and amplifiers; they differ only in the noise model applied to them.

The GNPy-format topology remains the single source of truth: `TapiContext`
parses it for the T-API model and routing exactly as before, and the designed,
amplified view is derived from it inside the EGN backend alone — nothing else in
the twin sees the split spans.

Without an equipment library the design step is skipped and the topology is used
as written, which stays correct for topologies that already specify their own
amplifiers and keeps the backend usable with no equipment file at all.

## 2. Transient layer

Four models are composed in
[`physics/transients/cascade.py`](../src/twinlight/physics/transients/cascade.py).
Each contributes an additive delta in dB (or ps) on top of the baseline.

### Two tracks, on purpose

The combiner keeps **separate GSNR and OSNR tracks**:

| Model | → GSNR | → OSNR | Why |
|-------|:------:|:------:|-----|
| EDFA reservoir | ✔ | ✔ | Gain excursions change signal and ASE power alike |
| PDL (polarization) | ✔ | ✔ | A polarization-dependent loss imbalance is an optical-domain effect |
| Environmental loss | ✔ | ✔ | Span loss changes the amplifier's noise contribution |
| Phase noise (EEPN) | ✔ | ✘ | EEPN is a **DSP-domain** impairment: it degrades the recovered constellation without changing the optical noise power an OSNR meter would read |

Getting this routing wrong is a common way for a twin to produce impossible
telemetry (OSNR degrading with no optical cause). It is explicit here so it can
be checked.

### Which reference bandwidth an SNR is quoted against

An SNR means nothing without the bandwidth its noise was measured over, and
this project reports two references. They are not interchangeable and the
difference is not small — 4.08 dB at 32 GBd.

| Field | Reference | Use |
|-------|-----------|-----|
| `gsnr-db` | Signal bandwidth (the baud rate) | What the receiver sees. **This is the one admission compares against `req_gsnr_db`**, and the two must stay on the same reference or the thresholds mean nothing |
| `osnr-db` | Signal bandwidth | The ASE-only counterpart of `gsnr-db`, on the same footing |
| `osnr-01nm-db` | 0.1 nm (12.5 GHz at 1550 nm) | What the literature and an OSA quote. Derived, for comparison with published figures |

The conversion is `10·log10(baud_rate / 12.5 GHz)` — a constant in dB, so it
commutes with the transient layer's additive perturbations and can be applied
once at the end (`modulation.py:osnr_to_01nm_db`). gnpy draws the same
distinction between its `osnr_ase` and `osnr_ase_01nm`.

> **If you quote an OSNR from this twin, say which one.** A reader who sees
> "OSNR 13.4 dB" will almost certainly assume 0.1 nm and conclude the link is
> 4 dB worse than the model actually says. `gsnr-db` is deliberately *not*
> offered at 0.1 nm: a 0.1 nm GSNR compared against the required-GSNR table
> would admit lightpaths that cannot carry traffic.

### Deterministic pseudo-randomness

Every model that needs a per-element random-looking value derives it from an MD5
hash of `(element_uid, metric_name)` — `cascade.py:hash_phase()`. Two
consequences:

- The twin is **reproducible**: the same topology gives the same trajectories on
  every run and every machine, which is what makes a recorded experiment
  re-runnable.
- Values are **decorrelated across elements**, so a cascade does not degenerate
  into every amplifier drifting in lockstep.

### 2.1 EDFA gain reservoir

`physics/transients/edfa_reservoir.py` — the only **stateful** model.

The Giles–Desurvire / Sun–Zyskind reservoir model [[2]](../README.md#ref-2)
describes EDFA gain during channel add/drop. The governing ODE (Bononi & Rusch
1998 Eq. 5 `[Bononi-Rusch]` [[1]](../README.md#ref-1)):

```
dr/dt = −r(t)/τ + Σ Qⱼⁱⁿ(t) · [1 − exp(Bⱼ·r(t) − Aⱼ)]
```

has the exponential step response of their Eq. 19:

```
r(t) = r_ss_new + (r_ss_old − r_ss_new) · exp(−(t − t_event) / τ_e)
```

where τ_e (Eq. 29) is input-power dependent and **asymmetric**: ≈1–10 µs for a
channel add, ≈100–500 µs for a drop. Cascade behaviour follows Sun 1997: the
transient rate scales linearly with amplifier count, `1/T_N = N · 1/T_1`, and
peak excursions can reach ~28 dB p-p without AGC
[[3]](../README.md#ref-3).

**The steady state is zero deviation**, and this is the load-bearing detail.
Between events an AGC-controlled, gain-flattened EDFA delivers its designed
per-channel gain whatever its loading — and that designed operating point is
precisely what the QoT baseline already represents, since `designed_network()`
sets every amplifier's operating point before propagation. So `r_ss_old` and
`r_ss_new` both map to *zero* deviation from the baseline, and what this model
contributes is the excursion between them:

```
Δ(t) = Δ_event · exp(−(t − t_event) / τ_e),   Δ_event = −gain_per_channel_db · Δchannels
```

Adding channels depletes the reservoir, so gain drops and surviving channels
lose GSNR (negative excursion); dropping channels lets gain overshoot
(positive). Treating the steady state itself as a load-dependent penalty would
double-count loading the baseline has already priced in — and, summed over a
long cascade, would swamp it: 30 amplifiers × 0.3 dB × one channel is 9 dB.

`EdfaStateTracker` is owned by `TapiContext` and updated whenever a service is
created or deleted — so an add/drop event on one lightpath perturbs every other
lightpath sharing those amplifiers, which is the whole point of modelling it.
When no tracker is supplied, the model degrades to a stateless sinusoidal
approximation.

### 2.2 Polarization: PMD drift and PDL

`physics/transients/polarization.py`. Two distinct effects.

**PMD.** Per-fiber differential group delay drifts sinusoidally (default ±10 %,
90 s period) and accumulates in quadrature along the path, per the
Gordon–Kogelnik framework `[Gordon-Kogelnik]` [[4]](../README.md#ref-4).

**PDL.** A coherent link is treated as a cascade of N discrete *hinges* —
ROADMs/WSSs and EDFAs — following Zarkosvky & Shtaif 2020
`[Zarkosvky-Shtaif 2020]` [[5]](../README.md#ref-5). Each hinge j contributes a
power transfer (their Eq. 5):

```
aⱼ = (1 + γⱼ·cos θⱼ) / √(1 − γⱼ²)
```

where γⱼ is the hinge's PDL and θⱼ its alignment with the signal SOP. Three
implementation choices give this its behaviour:

1. Each hinge's PDL is drawn from a **Maxwell distribution** seeded by the
   element UID, with the mean taken from `pdl_per_roadm_db` /
   `pdl_per_edfa_db` and the rare upper tail truncated
   ([[15]](../README.md#ref-15), [[16]](../README.md#ref-16)).
2. Each hinge's alignment `cos θⱼ` drifts with an **incommensurate period**
   (base period detuned ±25 % by UID), so over time the joint alignment space is
   covered ergodically without an explicit Monte-Carlo loop.
3. The resulting OSNR penalty depends on where ASE enters relative to the
   hinges — the `ase_distribution` setting (`distributed` / `rx` / `tx`),
   following D'Amico OFC 2023 [[15]](../README.md#ref-15) and Miotto OFC 2025
   [[16]](../README.md#ref-16).

The hinge model is used rather than the closed-form PDL penalty from the IM/DD
literature, which is not valid for coherent systems.

### 2.3 Phase noise (EEPN)

`physics/transients/phase_noise.py`.

For long-haul coherent links the dominant residual phase impairment is
**equalization-enhanced phase noise**: the interaction between local-oscillator
phase noise and accumulated chromatic dispersion, which grows with link length
(Shieh & Ho 2008, Eq. 33–41 `[Shieh-Ho]` [[11]](../README.md#ref-11)):

```
α         = π·c / (2·f₀²) · |D_t| · B · Δν_LO
penalty_dB = 10·log₁₀((GSNR_lin·α + 1) / (1 − α))
```

with D_t the accumulated dispersion [s/m], B the baud rate, Δν_LO the LO
linewidth, f₀ the carrier frequency, and GSNR_lin the **pre-EEPN** GSNR.

Three properties are worth noting because they distinguish EEPN from a naive
linewidth penalty:

- It is **dispersion-dependent** — 1000 km incurs roughly 100× the penalty of
  10 km.
- Only the **LO** linewidth matters; transmitter phase noise largely cancels
  through the fiber-plus-equalizer path. `tx_linewidth_hz` therefore does not
  drive this penalty.
- It **grows with GSNR**, so it bites hardest on the links that would otherwise
  be healthiest.

### 2.4 Environmental drift

`physics/transients/environmental.py`. Disabled by default (`enabled: false`),
because a 24-hour cycle is longer than most experiments.

Ambient temperature follows a diurnal sinusoid (`temp_variation_c`,
`temp_cycle_period_s`, shifted by `timezone_offset`), driving two effects:

- **Chromatic dispersion drift** via dD/dT ≈ 0.002 ps/(nm·km·°C) for G.652 SSMF
  (Kato *et al.* 2000 `[Kato]` [[13]](../README.md#ref-13)).
- **Fiber loss variation** via dα/dT ≈ 2×10⁻⁴ dB/(km·°C) on deployed fiber,
  which penalises both GSNR and OSNR.

## 3. From GSNR to BER and Q

`physics/ber_conversion.py`. After the transient layer perturbs GSNR, pre-FEC
BER and Q-factor are recomputed — never carried over from the baseline.

The Gaussian approximation for coherent DP-M-QAM is used, with per-format
pre-factor and SNR divisor, evaluated through `scipy.special.erfc` and inverted
with `erfcinv` [[14]](../README.md#ref-14). This assumes ideal DSP with no
implementation penalty beyond the GSNR already computed.

### Modulation formats

| Format | Bits/symbol | Required GSNR | Baud rate |
|--------|-------------|---------------|-----------|
| DP-QPSK | 2 | 8.5 dB | 32 Gbaud |
| DP-16QAM | 4 | 14.5 dB | 32 Gbaud |
| DP-64QAM | 6 | 20.5 dB | 32 Gbaud |

Required-GSNR values are the admission thresholds; `rmsa.qot_margin_db` is added
on top before a service is accepted.

**Admission gates on the pristine baseline, not on a transient-inclusive
sample**, and `rmsa.qot_margin_db` (1.5 dB by default) is the documented
allowance for the transient layer — the same role a system margin plays in
network design. This is deliberate: a sample is a point in time, so gating on
one would make admission depend on the phase of the PDL drift at the instant
the request arrived, and two identical requests seconds apart could decide
differently. The margin has to cover the transient layer's realistic excursion;
on the bundled CORONET scenario the layer contributes about −0.1 dB (EEPN) and
±0.3 dB (PDL), comfortably inside it. Widen the margin before enabling a model
whose excursions could exceed it.

## 4. Eye and constellation synthesis

`output/eye_diagram.py` and `output/constellation.py` synthesise diagrams
*statistically* from the current OPM rather than from a waveform simulation:
raised-cosine pulses with Gaussian noise of variance 1/(2·GSNR_lin), timing
jitter σ_t = PMD/√3, and optional amplitude variation from PDL. The approach
follows the time-domain digital-twin framing of OCATA
[[17]](../README.md#ref-17).

These are illustrative and consistent with the reported OPM; they are not a
substitute for DSP-level simulation.

## Known limitations

Stated plainly, because a twin whose approximations are undocumented cannot be
used for research.

| Area | What TwinLight does | What the literature does | Severity |
|------|---------------------|--------------------------|----------|
| **EDFA reservoir** | Exponential step response (Bononi & Rusch Eq. 19/29) with asymmetric τ_add/τ_drop, linear dB cascade | Full ODE integration (their Eq. 5) including spectral hole burning and gain clamping | Low — the step response is accurate for add/drop events; the full ODE would matter for fast repeated events |
| **EDFA excursion visibility** | τ_e is 10–100 µs, so a wall-clock poll essentially always samples the relaxed state and the model reads ~0 dB between events | Same physics — this *is* what AGC does | Low, but easy to mistake for an inactive model: to see the excursion you must sample near an add/drop, which the twin only produces on service create/delete |
| **EDFA excursion magnitude** | Excursion scales with the absolute load step, `gain_per_channel_db × \|Δchannels\|` | Sun 1997 scales it with the *fraction* of channels added or dropped, so one channel added to a full C-band perturbs far less than one added to an empty one | Low at the loadings the bundled scenarios reach; refine before claiming excursion magnitudes on heavily loaded spans |
| **EGN kernel** | Self-channel NLI only | Full GN/EGN including XPM/FWM from neighbouring channels | **Moderate** — optimistic by a few dB on densely loaded links; on the bundled CORONET scenario EGN GSNR runs ~2–4 dB above the GNPy backend on the same designed spans. Use the GNPy backend when spectral loading matters |
| **Phase noise (EEPN)** | Shieh & Ho Eq. 33–41, contributing ≈ −0.1 dB at 2150 km on CORONET | Same | None — implemented per the reference; the penalty scales with accumulated dispersion as Shieh & Ho predict |
| **PMD drift** | Sinusoidal, 10 % amplitude, 90 s period | Maxwell-distributed DGD with a stochastic drift process; field drift timescale is not universal | Low — magnitude and quadrature accumulation are right; the trajectory shape is an approximation |
| **PDL ensemble** | Deterministic incommensurate drift covers the alignment ensemble over time | Explicit Monte-Carlo over hinge alignments | Low — equivalent in the long run, and reproducible, but a short window is not a fair ensemble sample |
| **Post-FEC BER** | Not modelled — pre-FEC only | Soft-decision FEC threshold curves | Known gap |
| **Spectral scope** | C-band only | C+L, multi-band | Known gap — the grid is parameterised but the physics is not |
| **Filtering / ROADM penalties** | Not modelled beyond PDL | Cascaded filter narrowing penalties | Known gap |
| **BER conversion** | Textbook Gaussian approximation per format | Curri 2022 uses measured back-to-back thresholds | Low — standard practice |

## Validating changes

Physics changes are covered by `tests/test_physics/`, which asserts behaviour
rather than snapshot values: monotonicity in the right direction, correct
timescales, deterministic reproducibility from the UID seeds, and correct
GSNR/OSNR track routing. When adding a model, add the equivalent assertions —
a test pinning a magic number is worth much less than one pinning the physics.

[ong]: https://github.com/carlosnatalino/optical-networking-gym
