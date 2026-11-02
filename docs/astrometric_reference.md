# Absolute astrometric reference

Every position the app plots is an offset from the **core**: `avg − core`, with
the core at `(0, 0)` by construction. The origin therefore rides along with
whatever the core fit did in that epoch's map — a feature that sat still on the
sky appears to move if the core moved, and vice versa.

Where an **absolute** astrometric position of the core exists, we can do
better: put the origin at a point that is *fixed on the sky*, so the core is
free to wander and the features carry absolute-frame motion. The
**"Astrometric reference"** header checkbox does exactly that, per source, for
the epochs that have one.

Implementation: `mojave_review/data/astrometry.py`
(`load_astrometry` / `apply_astrometric_reference`), wired into the plots
through `ui/callbacks._resolve_df_for_plot`.

## The data — `astrometry_mojave.dat`

Not under `Results/` — a fixed-layout text file the user keeps alongside the
production data (gitignored), one line per (source, astrometric observation):

```
0003-066   20211023 h  4  EPOCH: 2021.81167 \
    R: 00 06 13.89288712 -+ 0.0422  D: -06 23 35.3355558 -+ 0.0941 \
    Corr: -0.0210  Obs: 151 / 157  Sca: 0 / 0
```

Found the same way as `source_run_param.csv` — cwd → `results_dir` parent →
`results_dir` — or pointed at explicitly with `--astrometry-file`. Without it
the checkbox is disabled everywhere and nothing else changes.

Fields used: the **band-less source name**, the **observation date**
(`YYYYMMDD`, which is our `ep_name` once underscored — this is the join key,
*not* the decimal epoch, which differs in the last few digits), the sexagesimal
**RA/Dec**, and their **1σ errors**.

Coverage as of 2026-10-29: 301 sources in the file, 271 of the 487 under
`Results/`. Within a covered source the coverage is usually partial — e.g.
`0003-066u` 10 of 37 epochs, `0415+379u` 103 of 175. A handful of astrometric
dates match no MOJAVE epoch of ours (2 of 105 for `0415+379`); they are ignored
for plotting but still count toward the mean (below).

## The arithmetic

With `astro(e)` the absolute core position at epoch *e* and the mean taken over
every epoch the file has for that source:

```
corr(e)   = astro(e) − mean(astro)          # the astrometric correction
reference = core(e) − corr(e)               # in epoch e's map frame
plotted   = avg − reference = avg − core + corr(e)
```

So the core itself plots at `+corr(e)` rather than exactly `(0, 0)`, and every
feature in that epoch shifts by the same `+corr(e)`.

**The sign is the part worth checking.** The origin is the *mean* position, a
fixed point on the sky. If at epoch *e* the core is 0.1 mas north of its mean
absolute position, then relative to that fixed origin the core must plot 0.1 mas
north — hence `+corr`, and hence `reference = core − corr`. (Stated as
"core + correction" the core would wobble the wrong way.) Reviewer decision
2026-10-29.

Conventions, both verified against the data:

- `dx` is **+east** (= +RA), matching the app's `+x`; the plots then reverse the
  axis so east shows on the left. `dy` is +north.
- The file's RA offsets *and RA errors* are **coordinate** angles, so they need
  the `cos(dec)` factor to become true angles. Empirical confirmation: at
  dec +81.6° (`0014+813`) the RA error column runs ~6.5× the Dec one, and the
  ratio collapses to ~1 once scaled.

Magnitudes: ~0.09 mas rms in x, ~0.14 mas in y sample-wide; the 90th percentile
largest single offset is ~0.68 mas. Sub-beam, but well above the CC centroid
errors the 1σ bars show — and a real effect on slow features.

### Mean over which epochs

Over **every** epoch in the file for that source, not just the ones the loaded
model happens to contain. That makes the reference frame a source-level
constant: it never shifts when a model gains epochs or the epoch range changes.
The cost is that the plotted core positions don't average to exactly `(0, 0)`
when some astrometric dates don't join. Reviewer decision 2026-10-29.

## What re-referencing touches

`apply_astrometric_reference` rewrites `core_x` / `core_y` and nothing else,
which is all it takes — every consumer derives positions from `avg − core`:

| Consumer | Effect |
|---|---|
| Position view | distance vs epoch + XY track shift; the **core gets a real, non-zero track** (it was a flat zero line), and is drawn in the XY panel too (see below) |
| Position Angle | PA shifts; near-core clusters move most |
| Kinematics | motion fits re-fit in the absolute frame; the core now gets a genuine fit instead of an exactly-zero one |
| Flux / Polarization | unaffected (no positions) |
| Epoch overlay | image axes, CC scatter, ellipses and labels all shift together; the core CC cluster no longer sits exactly on `(0, 0)` |
| Stacked image | each epoch's CCs are accumulated at their **absolute** position, so the stacked core smears by the ~0.1 mas wobble — the physically consistent stack for this frame |
| Zoom extent | `compute_source_extent` follows automatically |
| 1σ bars | the astrometric position error is added in quadrature (below) |

It is applied **last** in `_resolve_df_for_plot`, after any recommendations: a
`change_clusterID` to 0 rewrites `core_x`/`core_y` for its epoch, and the
correction goes on top of whatever core the rendered model ends up with.

Epochs with no astrometric position are left alone and keep the plain core
reference, so a partially covered source is plotted in **two frames**. That is
unavoidable, and it is why the UI reports coverage in two places (below).

### The core in the XY panel

`_draw_xy` skips the unassigned cluster (−1) always, and skips the **core (0)**
only while its track sits on the origin — `_has_xy_offset`, a `> 1e-6 mas`
test on `hypot(xpos, ypos)` (in the plain core frame `avg_x` *is* `core_x`, to
~1e-16). So:

- plain core reference → hidden, as before: its track is the origin marker, and
  one marker per epoch piled under it is clutter with no information in it;
- astrometric reference → drawn, with the same style, dotted connector, error
  bars, legend group and browser-side epoch linkage as any other cluster;
- `/compare` → hidden, since both panels there are core-referenced.

Keying on the **data** rather than on the checkbox means any future reference
shift gets this for free and no flag has to be threaded through. Reviewer
decision 2026-10-29.

Epochs with no astrometric position are kept in the track at exactly `(0, 0)`
— in those the reference genuinely *is* the core, and dropping them would make
the dotted connector join non-adjacent epochs. In practice astrometric coverage
is contiguous at the recent end, so the track reads as an origin cluster
(earlier epochs) joined once to the wobble cloud (covered epochs); zooming the
distance panel above in time thins the origin pile via the usual epoch-range
filter.

The core is **not** added to the Kinematics view (`_draw_kinematics` keeps its
`mf.cid > 0` filter). Absolute core drift is a different quantity from
jet-feature motion, and on a partially covered source the fit mixes real
astrometric offsets with zero-offset uncovered epochs, so the resulting speed
is not a clean proper motion. Reviewer decision 2026-10-29.

### Uncertainties

The origin is now a fixed sky position known only to the accuracy of the
astrometric fit, so that error is a third independent term:

```
σ_Δx = sqrt( SE_cluster,x² + SE_core,x² + σ_astro,x² )
```

(`astro_sig_x` / `astro_sig_y` on the cluster table; `σ_astro,x` includes the
`cos(dec)` factor). It is added **only where finite** — `np.hypot` would
propagate the NaN of an uncovered epoch and wipe out its bars. The error on the
*mean* position is ignored: it is a common offset and cancels in every
differential comparison. See [uncertainty_estimates.md](uncertainty_estimates.md).

One visible side effect: the core, whose `σ_d` and `σ_pa` were NaN because
`d ≡ 0`, now has a non-zero distance and so gets real bars.

## UI

- **Header checkbox** "Astrometric reference", next to "Stacked image",
  default **off**. Disabled (with `(none for this source)` / `(no astrometry
  file)`) where there is nothing to apply, so ticking it is never a silent
  no-op.
- **Status span** beside it: `(10 / 37 epochs)` — epochs that actually *join*,
  not the file's row count.
- **Summary badge** reads `<source>  ·  astrometric ref`, so a screenshot is
  never ambiguous about what `(0, 0)` means.
- **Overlay badge** gets a fourth line naming the frame of the epoch on screen,
  with the correction applied:
  `Origin: astrometric (mean of 10 epochs) · core offset +0.095, +0.044 mas`,
  or `Origin: fitted core (no astrometry this epoch)`. Per-epoch, because a
  reviewer scrubbing a partially covered source crosses the boundary with no
  other visible cue.

The toggle is deliberately **not** folded into either figure's `uirevision`, so
ticking it keeps the current zoom and the shift is visible in place.

## Scope

Main review page only — both summary panes and the epoch overlay. The
`/compare` page stays core-referenced: the XVIII side has no astrometry, so
re-referencing one panel would put the two in different frames and break the
shared extent / registration the page is built on. The admin Window-N panel is
also unaffected (its per-window frames carry no `ep_name` to join on, and
`apply_astrometric_reference` no-ops on such a frame by design).

## Not done

- No per-epoch opt-out, and no way to choose a reference epoch instead of the
  mean.
- The Kinematics view ignores the core (above), so absolute core drift is
  visible only as the XY track and the distance-vs-epoch curve.
- Nothing is written back into `Results/`, and the setting is not recorded in a
  recommendation — it is a view option, like "Use FITS images".
- The pipeline (`cluster_code.py`) is untouched; this is a review-app frame
  change only.
