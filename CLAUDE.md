# MOJAVE Cluster Review

This repo holds two related but distinct things:

1. **The original clustering pipeline** — `cluster_code.py` and `find_clusters.py`.
   These produce per-source clustering results saved under `Results/`.
2. **`mojave_review/`** — a pip-installable Dash web app that lets reviewers
   interactively inspect those saved results and leave structured
   *recommendations* (never edits to the on-disk results).

The original pipeline is read-mostly for the web app: the web app loads its
data products but does not run new clustering models.

## Repository layout

```
Clustering_Review/
├── cluster_code.py              # ~6000 LOC of pipeline + interactive matplotlib review
├── find_clusters.py             # CLI driver that produces Results/<source>_<emin>-<emax>/
├── Results/                     # one folder per source (mirror of Drive `Results/`)
│   └── <source>_<emin>-<emax>/
│       ├── <prefix>.merged_win_results.csv         <-- main per-(epoch,clusterID) table
│       ├── <prefix>.merged_win_results.plotdata.npz<-- epoch_info + raw clean components
│       ├── <prefix>.summary_plots.pdf              <-- reference render (read-only)
│       ├── <prefix>.epoch_overplots.mp4            <-- reference render (read-only)
│       ├── backups/backup_NNN_*.{csv,pdf,mp4,json,txt}  <-- prior models (no npz)
│       ├── alt_models/alt_model_NNN_*.{csv,npz,json,txt} <-- optional alt models (WITH npz)
│       ├── cluster_fits/                                 <-- per-window cluster fits
│       ├── config_win.json
│       ├── history.txt
│       └── run_string.txt
└── mojave_review/               # the web app (pip-installable)
    ├── pyproject.toml
    └── src/mojave_review/
        ├── cli.py                       # `mojave-review` entry point
        ├── app.py                       # Dash factory (points assets_folder at the pkg)
        ├── data/
        │   ├── loader.py                # source/model discovery, CSV+NPZ load
        │   ├── window_fits.py           # cluster_fits/*.npz loader + nwin_choices store
        │   └── fits_cache.py            # MOJAVE URL + on-disk FITS cache
        ├── plots/
        │   ├── summary.py               # Plotly port of make_summary_plots
        │   ├── overlay.py               # FITS + cluster + beam overlay per epoch
        │   └── _extent.py               # initial zoom-box from cluster footprint
        ├── recommendations/
        │   ├── schema.py                # dataclasses for the JSON shape
        │   ├── store.py                 # read/write/list reviewer JSON files
        │   └── apply.py                 # apply a Recommendation to a cluster_df
        ├── assets/                      # Dash auto-loads .js/.css from here
        │   ├── resizable.js             # draggable splitter between panels
        │   └── resizable.css
        └── ui/
            ├── layout.py                # header + body + recommendations panel
            ├── callbacks.py             # source/model/view + selection + summary/overlay
            ├── nwin_panel.py            # admin Window-N review panel (--editN replacement)
            ├── nwin_callbacks.py        # ... and its callbacks (admin-only)
            ├── recommendations_panel.py # the 4-tab bottom panel
            └── recommendations_callbacks.py
```

`<prefix>` = `<source>.<emin>-<emax>` (e.g. `0003-066u.1994.00-2026.00`).
Source folder name = `<source>_<emin>-<emax>` (underscore, not dot).

## Data schemas (worth memorizing)

### `merged_win_results.csv` (one row per epoch × clusterID)

Key columns: `source, band, ep_name, epoch (decimal yr), clusterID,
N_Icc, N_QUcc, avg_x, avg_y, dist, pa, core_x, core_y, fwhm_maj, fwhm_min,
iflux, qflux, uflux, pflux, evpa, select, robust, use_in_fit,
ClusterType, Nclusters, Nepochs, ep_min, ep_max, ref_epoch, origID,
medianFlux, centX, centY, slopeX, slopeY, accelX, accelY, sizeMaj, sizeMin,
sizePA, bmaj, bmin, bpa, inoise, pnoise`.

- `clusterID == -1` → unassigned clean components (still appears as one row per
  epoch for bookkeeping; has avg_x/y = NaN).
- `clusterID == 0` → core (typically). Don't plot PA for the core.
- `clusterID >= 1000` → special / synthetic; treat like unassigned (black `+`).
- `robust`, `use_in_fit`, `select` are booleans (Python booleans after read_csv).

### `merged_win_results.plotdata.npz` (numpy pickle, allow_pickle=True)

Four fields:

- `epoch_info` — structured array, one row per epoch:
  `(epoch_name, epoch_val, band, cc_file, fits_file, inoise, pnoise,
    sigma_cut, sigma_cut_area, bmaj, bmin, bpa, pix_to_mas)`
- `cc_data` — structured array, one row per clean component:
  `(epoch, x, y, stokes, flux, sizex, sizey, group, clusterID)`
- `cc_labels` — int32 array of per-cc cluster labels (parallel to `cc_data`).
- `root_data_dir` — string, where FITS files lived on the machine that
  generated the npz. The web app **ignores this** and fetches FITS from MOJAVE
  instead (see below). `mojave-apply` also overrides it: when regenerating the
  summary PDF + epoch MP4 it passes `$MOJAVE_DATA` (if set) to
  `save_summary_plots`, falling back to this baked-in value only when the env
  var is unset (`cli/apply._resolve_root_data_dir`).

### `source_run_param.csv` (one row per source; local data, gitignored)

NOT under `Results/` — a single CSV the user keeps next to the production data
(discovered via `data/source_params.find_source_params`: cwd → `results_dir`
parent → `results_dir`). Many columns; the web app reads only two:

- `Source` — **band-less** name (`0003+380`, not `0003+380u`); matched to the
  app's source via `split_source_band`.
- `redshift` — float `z`, or **blank when unknown**.

Loaded once into a `{source: z}` map (`load_redshifts`); sources with a blank /
non-positive z are simply absent (→ treated as z=0). Drives the host-frame Tb
`(1+z)` correction and the Kinematics `beta_app` hovers — see "Per-source
redshift" under the summary-plot numerical quirks.

## MOJAVE FITS retrieval

Live URL pattern (from `grab_mojave_image` in `cluster_code.py`):

```
http://www.cv.nrao.edu/2cmVLBA/data/<source>/<epoch_name>/<source>.<band>.<epoch_name>.<stokes>cn.fits.gz
```

`<epoch_name>` is the underscored form: `1995_07_28`.
`<stokes>` ∈ {`i`, `q`, `u`}.
`<band>` is the per-source band code (`u` for U-band).

The web app fetches these on demand and caches under
`~/.mojave_review/cache/<source>/<epoch_name>/...` (overridable via `--cache-dir`).

## Web app: views and conventions

`build_summary_figure(view=...)` produces a 2-row figure for the first four
views, and a **single-plot** figure for `XY Position`:

| View | Top | Bottom |
|---|---|---|
| Position | distance vs epoch (+ polyfit overlay), 1σ error bars | PA vs epoch, 1σ error bars |
| XY Position | *(single plot)* per-cluster centroid track in (x,y) mas vs core, +x reversed, equal scale, 1σ x/y error bars | — |
| Flux | I flux vs epoch (log y-axis, 10ˣ ticks) | Tb vs epoch (log y-axis, 10ˣ ticks; 15.4 GHz, z param) |
| Polarization | P flux vs epoch (log y-axis, 10ˣ ticks) | EVPA vs epoch |
| Kinematics | speed vs distance, 1σ speed error bars, axes anchored at 0 | X/Y velocity vectors w/ arrowheads, +x reversed, autoranged |

The 1σ error bars on Position / XY Position come from
`plots/uncertainty.attach_position_uncertainties` (CC-derived `sig_dx/sig_dy/
sig_dist/sig_pa` columns); see [`docs/uncertainty_estimates.md`](docs/uncertainty_estimates.md).
The **Visualize recommendations** checkbox defaults ON. (The old "Show 3σ
outlines" checkbox was removed; the 3σ-drawing code remains in
`overlay.build_overlay_figure` behind `show_3sigma=False`, just no UI toggle.)

**Active-epoch marker.** The epoch currently shown in the overlay panel is drawn
as a thin vertical line on the epoch-axis summary views (Position / Flux /
Polarization — both subplots), so the left and right panels stay visually
linked while scrubbing. The `_epoch_label` callback publishes the active
*decimal* epoch (`epoch_info[idx]['epoch_val']`) to `dcc.Store(id="active-epoch")`;
a **clientside** callback then sets the figure's `shapes` via `Plotly.relayout`
(refs `x`/`y domain` and `x2`/`y2 domain`) — no trace rebuild, so it's cheap and
preserves zoom (`uirevision` untouched). It also keys on `summary-graph.figure`
so the line re-applies after any server-side rebuild, and resets `shapes` to
`[]` on the non-epoch views (XY Position / Kinematics). Shapes are exclusively
this callback's; no summary view uses layout shapes otherwise. Same
"clientside `Plotly.relayout` + return `no_update`" discipline as the beam
callback (see Don't/gotchas).

### Plotting conventions (always apply to spatial / sky plots)

- **+x to the left** (astronomical convention). Use explicit `range=[hi, lo]`
  to reverse the x-axis rather than `autorange="reversed"` so it composes
  cleanly with `scaleanchor`.
- **Equal mas/pixel** on x[mas] vs y[mas] plots. Initial range from
  `plots/_extent.compute_source_extent` on both axes, PLUS
  `scaleanchor="x"` + `scaleratio=1.0` on the y-axis AND
  `constrain="domain"` on **both** axes. That keeps ellipses (FWHM, 3σ,
  beam) round at every zoom level.
  Reviewer-confirmed trade-off (2026-05-28): the Plotly drag-zoom tool
  is also locked to the panel's current aspect when `scaleanchor` is
  on — there's no native way to keep equal scale AND get a free-form
  drag rectangle, and the reviewer explicitly preferred locked-aspect
  drag over breaking equal scale. Don't try to "fix" this by dropping
  `scaleanchor`; you'd revisit a decision that's already been made.
  (`constrain="range"`, the default, would also force the **post-drag
  range** to expand to match aspect — different mechanism, same lock
  feel; `constrain="domain"` is what we want.)
- **Arrows use `fig.add_annotation(showarrow=True, arrowhead=2, ...)`** —
  never line-mode hacks with a triangle marker at the end.
- Always show a black `×` at `(0,0)` on spatial plots (the core).
- Include `(0,0)` in the auto-range computation.

### Overlay panel (FITS + cluster overlay per epoch)

`overlay_figure_for_epoch(bundle, epoch_int, cache_dir, source_no_band, band)`
returns `(figure, beam_params)`. Layered traces in z-order (bottom to top):

1. Contour of the FITS Stokes-I image. Levels are at `cbase × 2ⁿ` with
   `cbase = 3.5 × inoise` from `epoch_info`. The CLEAN image is already
   convolved with the restoring beam — do NOT apply additional smoothing.
   `line.smoothing=1.0` (Bezier) is the most we add.
2. Clean components, scattered, colored by their cluster (uses
   `cc_labels` mapped through `origID → clusterID`; `origID` is the stable
   join key — `mojave-apply` rewrites `clusterID` but never `origID` and the
   npz isn't regenerated, so a CC's label equals its `origID` and the map
   recolors it to its current cluster, incl. after a `change_clusterID`).
   **Robust styling is per-CLUSTER, not per-epoch.** `robust` can vary across a
   cluster's epochs in the CSV (e.g. cluster 3 in 0003-066 is flagged
   non-robust at three epochs only); the overlay collapses it to one value per
   cluster via `overlay.robust_by_cluster` (the cluster's earliest-epoch value,
   matching the summary's `sub['robust'].iloc[0]`) for BOTH the CC scatter and
   the FWHM/3σ ellipses. Using the raw per-epoch flag made a feature flicker
   between its color and slategray as you scrubbed epochs.
3. Per-cluster 3σ inclusion ellipse: `2.548 × FWHM`, dotted outline,
   `rgba(<cluster>, 0.04)` fill — drawn first so the FWHM layers on top.
   Gated by `show_3sigma` (default False, no UI toggle since the checkbox
   was removed).
4. Per-cluster FWHM ellipse: solid outline, `rgba(<cluster>, 0.15)` fill.
5. Black-text cluster numbers at each cluster center (skipped for the
   core).
6. Black `×` at the core (0, 0).
7. Beam ellipse in the lower-left of the initial zoom — handled by a
   **clientside callback** so it tracks the viewport on every zoom/pan
   without a server round-trip. See "Don't / gotchas".

#### Contour image source (header checkboxes)

The contour background (layer 1) has three modes, in precedence order:

- **Stacked image** (checkbox) — overrides everything. The epoch-averaged
  image: every epoch's Stokes-I clean components are shifted to that epoch's
  fitted core, accumulated on one common grid (median `pix_to_mas`), divided
  by the epoch count, and convolved **once** with the **median beam**
  (median `bmaj`/`bmin`/`bpa` across epochs). Built by
  `synthesize_fits.synthesize_stacked_stokes_i` and cached per
  `(source, model, csv_sha)` via `overlay._stacked_axes_for_bundle` since it
  is epoch-independent. The drawn beam + `beam_params` use the median beam;
  `cbase` uses the median `inoise`. The per-epoch cluster overlay (layers
  2–6) still tracks the epoch slider, so you can scrub epochs against the
  stable averaged background. (Experimental.)
- **Use FITS images** (checkbox) — fetch the real CLEAN FITS for the epoch.
- default — synthesize the single epoch from its clean components + own beam
  (`synthesize_stokes_i`). Both synth paths share `_render_image` /
  `_beam_kernel`.

### Cluster styling (ported from cluster_code.py)

```python
cl_colors  = ["b", "g", "r", "m", "y", "gray"]    # cycle by clusterID
cl_markers = ["x","o","s","o","s","p","*","^","v",
              "*","^","v","X","D","P","D","1"]
cl_fill    = ["none","full","none","none","full","none","full","none","full",
              "none","full","none","full","none","full","full","none"]
```

- Non-robust clusters → all slategray (`#708090`), but keep marker/fill from
  the rotation. (Original matplotlib used cyan; on a white background it
  washed out, so the web app moved to slategray for legibility.) They DO
  appear in the legend (alongside robust clusters) so they can be toggled /
  isolated by legend click. The **Hide non-robust clusters** checkbox beside
  the "Summary plots" header (wired to `build_summary_figure(hide_non_robust=)`)
  drops them from both the plots and the legend. The unassigned cluster (-1)
  is treated as non-robust for this checkbox and is hidden too; synthetic
  (`>=1000`) clusters are unaffected. (Experimental — may be revoked.)
- `clusterID == -1` (unassigned) → black `+`, and IS in the legend so it can
  be toggled. `clusterID >= 1000` (synthetic) → black `+`, never in legend.
  `_add_cluster_traces` gates legend membership on `cid == -1 or 0 <= cid < 1000`.
- `use_in_fit == False` → black slash overlay on the marker.
- `select == True` → gold open-diamond overlay.

### Numerical quirks ported from `make_summary_plots`

- **PA de-wrap**: if `|pa[i] - pa[i-1]| > 300°`, nudge by ±360°.
- **PA shift** (`shift_pa` flag): if median |pa| > 120° across non-core
  clusters, add 360 to any PA < -60. Keeps southern jets readable.
- **EVPA de-wrap**: same idea with period 180°, jump 150°.
- **Size floor**: `size = max(sqrt(fwhm_maj * fwhm_min), 0.1)` mas.
- **Tb formula**: `1.22e12 * flux * (1+z) / (15.4² * size²)` K (U-band fixed).
  `z` is the per-source redshift (see "Per-source redshift" below). With a known
  z the `(1+z)` factor puts Tb in the host-galaxy frame and the axis label reads
  "Tb host-frame [K]"; with z=0 (unknown) it's the observed value, "Tb obs [K]".
- **Position polyfit / projected motion** (`_motion_fit`): computed for every
  robust cluster with ≥5 valid `use_in_fit` points. By default the fit is drawn
  for ALL such clusters (Position fit line + Kinematics points/vectors). The
  `>3σ OR (speed < 0.05 with combined error < 0.05 mas/yr)` test is no longer a
  gate — it's recorded as `_MotionFit.significant`, and the **"Hide uncertain
  motions"** checkbox (`only-3sigma-checkbox` → `build_summary_figure(only_3sigma=)`)
  filters the drawn fits down to the significant ones to restore the old
  behavior. (Label is "Hide uncertain motions" rather than "3σ" because the
  kept set also includes slow-but-tightly-constrained fits.) `_MotionFit.speed_err` (1σ on speed, propagated from the two slope
  variances) drives the error bars on the Kinematics speed-vs-distance plot.

### Per-source redshift (`source_run_param.csv`)

A `source_run_param.csv` kept alongside the production data (NOT tracked in
git — gitignored) carries a per-source `redshift` column (band-less `Source`
names like `0003+380`; blank = unknown). [`data/source_params.py`](mojave_review/src/mojave_review/data/source_params.py)
loads it once (`find_source_params` checks cwd, then `results_dir`'s parent,
then `results_dir`) into a `{source: z}` map; `_refresh_summary` looks up
`z = redshift_for(map, src.source) or 0.0` and passes it to
`build_summary_figure(z=)`. Two uses:

- **Host-frame Tb**: the `(1+z)` factor in the Tb formula (above).
- **`beta_app` on Kinematics hovers**: apparent speed in units of c,
  `beta_app = (1+z)·µ·D_A/c` (µ = angular speed mas/yr, D_A = angular-diameter
  distance). `source_params.beta_app` computes it with astropy + the MOJAVE
  standard cosmology (flat ΛCDM, H0=71, Ωm=0.27); shown on both the
  speed-vs-distance and velocity-vector hovers, and omitted when z is unknown.

## Recommendations

### Source picker labels (`build_source_options`, `ui/layout.py`)

Each dropdown option is `<source>   <reviewer-status>   [badge]` — built by
`build_source_options(results_dir, recommendations_dir, reviewer)` and refreshed
live by `_refresh_source_badges` (on reload-counter / submit-trigger; the
initial layout builds it too, so a returning reviewer sees their state on a
fresh tab). Rich `html.Span` labels carry the italic/plain/bold; a `search`
field (= source name) keeps type-to-filter working.

- **Source name only** — the date range is dropped (same for every source in
  this review; revisit if mixed ranges ever appear).
- **Per-reviewer status note** (`_reviewer_status`, scoped to the current
  reviewer so each sees their own progress / where to resume): *needs review*
  (italic) when the source is `open` (Stage 2 done) and untouched;
  "review in progress" (plain) when there's a **non-empty** `current/` draft and
  no submission (an empty draft does NOT count); **submitted** (bold) when a
  submission exists. Locked (`stage1`/`stage2`) and `final` sources get no note
  (not actionable — the badge says why).
- **Bracket badge** (`store.source_badge`) — `[N]` open submitted recs /
  `[final]` / `[final − M]` / `[stage 1]` / `[stage 2]`, unchanged.

Reviewer feedback lives in per-(source, model, reviewer) JSON files at
`<recommendations-dir>/<source>/<model>/<reviewer-slug>.json`. The app
NEVER modifies anything under `Results/`. The on-disk shape (full schema
in [`recommendations/schema.py`](mojave_review/src/mojave_review/recommendations/schema.py)):

```jsonc
{
  "source": "0003-066u", "model": "current",
  "model_sha": "<sha256 of the CSV reviewer saw>",
  "reviewer": "Reviewer Name", "updated_at": "2026-05-28T14:09:32+00:00",
  "source_comment": "...",
  // "I sign off on the model's robust flags as-is": even if the table has
  // stale entries, no derived set_robust edits are emitted while this is true.
  "no_robustness_changes": false,
  "cluster_feedback": {
    "3": {"recommended_robust": false, "comment": "merges with 4"}
  },
  "epoch_feedback": {"2003.10": {"comment": "ragged structure"}},
  "edits": [
    {"op": "change_clusterID", "scope": "single"|"all_epochs", ...},
    {"op": "set_use_in_fit",    "scope": "single"|"epoch",       ...}
  ]
}
```

- `recommended_robust`: `true`=Robust, `false`=Non-robust, `null`=no opinion.
  When ≠ the model's current robust flag for that cluster, a synthetic
  `set_robust` edit is *derived* at render/apply time — it is NOT written
  into the JSON's `edits[]` array (your post-processor should derive it).
- `use_in_fit` scopes: `single` (one point) + `epoch` (whole epoch). No
  "cluster-in-epoch" scope.
- `change_clusterID` scopes: `single` (one row) + `all_epochs` (renumber a
  cluster everywhere, multiple cids in a single batch = merge).

### Panel layout (4 tabs)

`Robustness` (default) — `ID / use-in-fit Edits` — `Source Notes` — `Epoch Notes`.

| Tab | What it shows |
|---|---|
| Robustness | "No changes suggested" checkbox at top + editable table of *eligible* clusters (≥ 5 epochs with `use_in_fit=True`). Columns: Eligible Clusters, Current Robust Status, Recommended Changes (—/Robust/Non-robust), Comment. Cluster 0 (the core) has its dropdown locked to "—" (the core is always robust); its Comment cell stays editable. |
| ID / use-in-fit Edits | Selection-driven action panel (visible only when summary-plot points are selected) + pending-edits list (manual + derived `set_robust` together). When no selection: instruction to click a summary point. Manual edits get a `[remove]` button; derived ones are read-only and tagged "from Clusters tab". |
| Source Notes | One textarea, `source_comment`. |
| Epoch Notes | One **real `dcc.Textarea`** per epoch (`build_epoch_rows`), NOT a DataTable cell — so the comment edits like a normal field (cursor / arrows / backspace / click-to-position / LTR). `epoch-feedback-table` is now a `dcc.Store` mirroring the old `.data` shape (`[{epoch, comment}]`) so every consumer is unchanged; `_sync_epoch_store` bridges the textareas (on `n_blur` → commit-on-blur, snappy typing) into that store. `_do_submit` reads the textareas' live `value` directly (the bridge is a server round-trip the submit clientside's microtask can't await). **The Robustness tab is still a DataTable** (its dropdown / live highlighting / resync / derived edits) — a follow-up will convert its comment cell the same way. |

The panel auto-saves on every field change. Read-only modes:

- model `current`: editable, autosaves.
- model `backup_NNN`: locked, empty (recommendations only valid against current).
- model `Rec: <slug>`: locked, displays *that* reviewer's recommendations from
  `<recs>/<source>/submitted/<slug>.json` so anyone can view another reviewer's
  submitted feedback. (Only submitted recs surface here; in-progress drafts
  under `<source>/current/` stay private until the reviewer clicks Submit.)

Visual lock = `opacity: 0.7; pointer-events: none` on the whole
`#recommendations-panel`.

### Reset Recommendation dialog

A **Reset Recommendation** button sits beside **Submit** (current model only;
hidden otherwise, managed by `_submit_button_state`). It opens a 3-choice
modal (`#reset-rec-modal`, a custom modal since `dcc.ConfirmDialog` only
offers OK+Cancel):

- **Reset to last submitted** — loads `<recs>/<source>/submitted/<own-slug>.json`,
  rewrites it as the working draft (`current/`), and repopulates the panel.
  Disabled (with a note) when no submission exists.
- **Delete draft & submitted** — `store.delete_recommendation` (the `current/`
  draft) + `store.delete_submission` (the `submitted/` file). The panel is
  blanked; autosave does NOT recreate the draft because the empty rec is never
  written (`Recommendation.is_empty()`), and autosave never writes `submitted/`.
- **Cancel** — closes the modal.

Both actions bump `rec-reset-counter`, an Input on `_submit_button_state`, so
the Submit label re-evaluates (Resubmit ⇄ Submit) without a source/model
change. Reset only ever touches the current reviewer's own files.

### Selection-driven edits

The summary plots carry `customdata=[clusterID, epoch]` on every cluster
point. **`_customdata` must return plain Python lists, NOT a numpy array** —
plotly.py 6 base64-encodes numpy arrays as typed arrays by default, so each
point's `customdata` reaches the browser as a `Float64Array` and Dash relays
it into `clickData` as an *object* (`{"0": cid, "1": epoch}`) instead of a
list; the click callback's `cd[0]`/`cd[1]` indexing then silently fails and
clicking stops selecting. The cluster scatter traces are also SVG `go.Scatter`
(not `Scattergl`) so click hit-testing is reliable and doesn't fight the SVG
gold-halo overlay. Two callbacks write to `dcc.Store(id="selection-store")`:

- **clickData** → toggle that one (cid, epoch) in the store. *The callback
  also resets `clickData` to None* so a repeat click on the same point
  fires (Dash only fires on Input *value change*; identical clickData would
  silently no-op).
- **selectedData** (box/lasso) → replace the store contents.

Selection is only meaningful on Position / Flux / Polarization views; the
event handlers no-op on Kinematics. The store clears automatically on
source or model change.

Highlight: the `cluster_df["select"]` column is set per row from the store
before passing to `build_summary_figure`; the existing gold open-circle
overlay in `_add_cluster_traces` renders the halo. **Open symbols
(`*-open`) use `marker.color` as the OUTLINE, not `marker.line.color` —
setting `marker.color="rgba(0,0,0,0)"` makes the entire halo invisible.**
Use a real color (`"gold"`) and SVG `go.Scatter` (not `Scattergl`) for
the overlay traces.

Action buttons on the Edits tab transform a selection into edits:

| Button | Generated edits |
|---|---|
| Mark use_in_fit=False on selected points | N × `set_use_in_fit / single` |
| Mark whole epoch use_in_fit=False | K × `set_use_in_fit / epoch` (K = unique epochs) |
| Renumber selected points to ID X | N × `change_clusterID / single` |
| Renumber all epochs of selected clusters to ID X | M × `change_clusterID / all_epochs` (M = unique cids; multiple cids ⇒ merge) |

Each click attaches the optional Comment field to every generated edit;
the comment input is reset after apply, the selection persists.

### Visualize-recommendations + multi-reviewer

A header checkbox "Visualize recommendations" controls whether
[`recommendations/apply.py`](mojave_review/src/mojave_review/recommendations/apply.py)
mutates the `cluster_df` before the summary + overlay are built. State
table:

| Model | Checkbox state | Plots show |
|---|---|---|
| `current` | enabled, off (default) | raw current data |
| `current` | enabled, on | current + user's in-progress UI-state recs applied |
| `backup_NNN` | disabled, off | raw backup data |
| `alt_model_NNN` | disabled, off | raw alternate-model data |
| `Rec: <slug>` | disabled, on (forced) | current + that reviewer's JSON recs applied |

The model dropdown is populated by `_populate_models`: `current` + any
`backup_*` + any `alt_model_*` + a `Rec: <slug>` entry per other-reviewer
*submitted* JSON file at `<recs-dir>/<source>/submitted/*.json` (excluding the
current user's slug). In-progress drafts under `<source>/current/` are never
surfaced.

**Alternate models (`alt_models/`).** Some sources carry an optional
`alt_models/` subdir parallel to `backups/`, holding alternate clustering runs
(`alt_model_NNN_merged_win_results.csv`). `list_models` surfaces them with key
`alt_model_NNN`, after the backups. Unlike a backup, an alt model **ships its
own `alt_model_NNN_plotdata.npz`**, so the loader sets its `npz_path` and the
overlay panel renders the alt model's *own* clean components / labels rather
than borrowing current's. Behaviour-wise they are treated exactly like backups:
read-only (recommendations only apply to `current`), so every `model_key !=
"current"` gate (panel lock, autosave skip, submit block, visualize disabled,
the empty Robustness/Edits panel in `_load_for_source`) already covers them —
no `alt_model_`-specific branching was needed.

### Stage-3 aggregation panel (admin only)

A collapsible **"🧩 Aggregate reviews (Stage 3 — admin)"** panel (rendered only
when `--admin`) lets the builder reconcile every reviewer's *submitted*
recommendation for the current source into one model. Pure logic lives in
[`recommendations/aggregate.py`](mojave_review/src/mojave_review/recommendations/aggregate.py)
(unit-tested, no Dash); the panel body is built by
[`ui/aggregation.py`](mojave_review/src/mojave_review/ui/aggregation.py).

- **Robustness decisions** — one row per cluster any reviewer voted on, with a
  column per reviewer (side-by-side) and a single **Final** dropdown
  (—/Robust/Non-robust). Default = `default_final_robust`: the **majority of
  the reviewer votes plus the current model as one equal vote**; a tie defaults
  to the current flag. Optional per-row Reason.
- **Cross-ID / use-in-fit edits** — one row per *unique* edit (identical edits
  from multiple reviewers collapse to one row, proposers listed; comments are
  excluded from the dedupe key). An **Accept** checkbox (default off) + optional
  Reason. Edits are ordered change_clusterID-before-use_in_fit for a
  deterministic apply order.
- **Reviewer comments** — read-only context (source/cluster/epoch comments).
- **Preview** — `_compose_agg` turns the decisions into one `Recommendation`
  (`compose_aggregated`) and, while **"Preview aggregated"** is ticked,
  publishes it to `dcc.Store(id="agg-preview-rec")`. That store is an Input on
  the summary + overlay callbacks; in `_resolve_df_for_plot` an `agg_rec` takes
  precedence over the reviewer's own Visualize on the `current` model. The
  store is always present (non-admin leaves it `None`).

- **Apply** — the **"Apply aggregated decisions (Stage 3)"** button opens a
  confirm modal whose action **generates a copy-paste `mojave-apply` command**
  (NOT an in-app subprocess — that inherited the app's env and hit the
  `MOJAVE_CODE` pitfall; same cut-n-paste model as the Stage-2 baseline apply).
  `ui/callbacks._apply_aggregated` composes the rec → writes
  `recommendations/<source>/stage3/aggregated.json` + a **sidecar**
  `aggregated.stage3.json` (`considered_slugs`, the pre-rendered
  `stage3_ledger_entry` with a `{{BACKUP_REF}}` placeholder + run N counted from
  the ledger, and the target `status`), then shows the command in the shared
  Stage-2 `apply-cmd-modal`. The command is
  `mojave-apply --recommendation …/aggregated.json --stage3-meta …/aggregated.stage3.json`
  (+ `--results-dir/--source/--recommendations-dir/--production-code-dir`, no
  `--no-confirm` so the terminal prompts). The app writes only under
  `recommendations/`, never `Results/`. **`mojave-apply --stage3-meta`**
  (`cli/apply._apply_stage3_meta`) does the bookkeeping atomically after the
  apply: backs up + regenerates `Results/` (PDF/MP4 only when the source
  carries them — plots are opt-in via `find_clusters --make_plots` now, and
  `--skip-plots` force-skips; skipped plots are MOVED into the backup),
  archives the JSON to `applied/`,
  moves the folded `submitted/*.json` → `considered/<date>/`, appends the
  ledger entry (resolving `{{BACKUP_REF}}` to the backup it cut), and sets
  Status → `Stage 3 done · applied <date>`. After running it, the admin clicks
  ↻ Reload. The `agg-apply-status` span reports the generated command (run N);
  it's separate from `agg-summary`, which `_compose_agg` owns.

- **Repeat applies (run N).** Stage 3 can be applied again after a source is
  finalized — a reviewer re-submits (badge → `[final − N]`), the panel
  repopulates, the admin re-decides and applies. Everything **appends**:
  `stage3_ledger_entry(run_index=)` numbers the heading `(run N)` (N counted
  from existing "Stage 3 reconciliation" headings in the ledger), `mojave-apply`
  archives each run to `applied/<date>__aggregated[_n].json` and cuts a new
  backup, and considered submissions go to `considered/<date>/`. When a source
  is `final` with no open submissions, `_build_agg_panel` shows a hint that the
  panel is waiting for a follow-up round (rather than looking broken).

- **Dated note box** (in the 🧩 panel). A textarea + **"Add dated note to log"**
  appends a free-text `### <date> — Note (by <admin>)` entry to the ledger
  (`notes.dated_note_entry` + `append_ledger`; never touches Status). It is
  **seeded** with every pending reviewer comment from the open submissions
  (`notes.render.pending_notes_seed` — source/cluster/epoch/edit comments,
  tagged `(reviewer)` with PARENTHESES not `[]`, since `[...]` in the ledger
  markdown would render as a link). **"Reseed from submissions"** re-pulls.
  Lets reviewer notes be preserved into the permanent `.md` before their JSONs
  are archived.

### Stage 2 vs Stage 3 apply — two distinct admin paths

Two admin "apply" actions, deliberately separated so they can't be confused.
**Both now use the same cut-n-paste model** — generate a `mojave-apply` command
the admin runs in a terminal (correct env + a confirm prompt); the app never
shells out and never writes `Results/`:

- **Stage 2 — baseline apply**: the **"Generate baseline apply command
  (Stage 2)"** button (in the Stage-2 admin block, next to the Stage-2 notes
  editor) produces `mojave-apply --recommendation <the admin's own
  current/submitted JSON>`. Applies the builder's **own single** recommendation.
  (`recommendations_callbacks_admin._do_generate`.)
- **Stage 3 — aggregated apply**: the **"Apply aggregated decisions (Stage 3)"**
  button (in the 🧩 panel) produces `mojave-apply --recommendation
  …/aggregated.json --stage3-meta …/aggregated.stage3.json` — the
  `--stage3-meta` sidecar folds in the considered-archive + ledger + Status.
  See above.

Both populate the shared `apply-cmd-modal` (copy-paste + Copy button).

**Stage-gated visibility** keeps only the relevant one on screen, keyed on
`store.source_phase`: in `stage1`/`stage2` phases the baseline-apply button
shows and the 🧩 panel is hidden; from `Stage 2 done` onward (`open`/`final`)
the baseline-apply button is hidden and the 🧩 panel shows
(`_toggle_btn_visibility` + `_toggle_agg_panel`).

The Stage-2 notes editor also has a **"Seed from submission summary"** button:
fills the editor with the admin's own submission's `format_submission_text`,
cleaned by `notebook_format.strip_for_notes` (drops the `─` rule lines, unwraps
the `[Submission for …]` header brackets so markdown doesn't make a link).

## Window-N review (admin — the `--editN` replacement)

A collapsible **"🔢 Window-N review"** panel (rendered only with `--admin`)
replaces the interactive matplotlib `--editN` session in `find_clusters.py`.
The pipeline caches a fit for **every** candidate N per time window under
`Results/<source>/cluster_fits/<source>.<first_ep>-<last_ep>.{npz,csv}`; the
panel just browses those cached fits and records per-window N choices —
no clustering runs in the app.

- **Data layer** ([`data/window_fits.py`](mojave_review/src/mojave_review/data/window_fits.py)):
  `list_window_fits` discovers the files; `window_bundle(src, ref, N)` adapts
  one (window, N) fit into a `SourceBundle` (the window's `cluster_epoch_df`
  plays cluster_df, its `labels` the cc_labels), so
  `overlay_figure_for_epoch` renders it with zero plotting changes (always
  `image_source="synthesize"`). `bic_table` reproduces the pipeline's
  BIC* = ln(Ndata)·k + complex·Ndata·⟨d²⟩/⟨Σbeam²⟩ from the per-window CSV
  `complex` is the CURRENT model's value (`load_complex_factor`:
  `config_win.json`, falling back to `config.json` — find_clusters rewrites
  it on every save), NOT whatever the source was first run with: the window
  CSVs carry only the BIC* ingredients, never a baked-in complex. The
  BIC*-vs-N curve uses a log y-axis whenever all values are positive — the
  N=1 point can sit an order of magnitude above the minimum (e.g.
  0415+379's 1997.19-2002.18 window) and flatten the interesting region on
  a linear axis. `build_window_meta` adds the current model's N per window
  (core row at the window's ref epoch — the same lookup as the pipeline's
  `get_previous_Nclusters_labels`).
- **UI**: window / N / epoch sliders (epoch defaults to the window's
  reference epoch), a BIC*-vs-N curve + an N-per-window strip chart
  (click a strip-chart point to jump to that window), and the overlay
  panel for the displayed (window, N, epoch). The N slider seeds to:
  recorded choice > current model N > BIC* suggestion.
- **Fixed zoom** (reviewer-requested): the overlay's initial view is ONE
  source-wide box covering every candidate cluster of every window × every
  N (`window_fits.global_window_extent`, from the cheap per-window CSVs'
  core-relative `centX`/`centY` ± 2·sizeMaj ± 1.5·median-beam + 5% pad;
  stored in `WindowMeta.extent`). `build_window_overlay` overrides the
  per-(window, N) ranges with it and repositions the beam trace +
  `beam_params` extents to its corner. Identical ranges every render + a
  constant uirevision (`nwin-overlay:<folder>:<reset-counter>`) = the view
  never jumps while scrubbing windows / N / epochs, and a manual drag-zoom
  persists across all three. The matplotlib `N_win_edit` fixed its limits
  once (from the most complex window) the same way; all-N union is the
  strictly-safe version. **Reset view** button (`nwin-reset` →
  `nwin-reset-counter` folded into uirevision) restores the default box —
  needed because a double-click autoranges to the current window's data
  and won't toggle back (scaleanchor + uirevision quirk, same as the main
  overlay's Reset view).
- **Keyboard** (matplotlib `N_win_edit` parity): while `#nwin-details` is
  OPEN, `assets/keyboard.js` routes ←/→ to `nwin-win-prev/next` (time
  window), ↑/↓ to `nwin-n-up/down` (N ± 1), and `r` to `nwin-record-btn`
  (record N for this window) — capture-phase + `stopImmediatePropagation`
  like the epoch arrows. Panel closed → ←/→ step the main epoch overlay
  again; ↑/↓ and `r` are only claimed while open. All are skipped when
  focus is in a text input (the `inEditableTarget` guard), so typing an
  `r` into the choice-comment field doesn't fire a record.
- **Resizable split**: a draggable vertical divider (`#nwin-split-handle`
  between `#nwin-left-panel` / `#nwin-right-panel`) resizes the BIC*/strip
  panel vs the overlay. Same `assets/resizable.js` + `.split-handle` CSS
  class as the main `#split-handle` — the JS now wires a list of splitter
  configs (the Window-N one is skipped in non-admin mode) and reflows the
  Plotly charts on drag.
- **Choices** autosave to
  `<recs>/<source>/nwin_edits/nwin_choices.json` on every record/clear
  (file deleted when the last choice is cleared; `model_sha` records the
  merged CSV the choices were made against). The directory name is
  deliberately stage-agnostic — N editing is *technically* a Stage-2
  activity the builder has been doing offline, so it is NOT named
  `stage1/`/`stage2/`. There is **no on-screen list** of recorded choices
  (it grew with edit count and shrank the plots) — the recorded N values
  are the red dots on the N-per-window strip chart, and each window's
  comment is loaded into the comment box by `_nwin_load_comment` when you
  arrive at that window (empty box ⇒ placeholder invites one; the status
  line also echoes it). The comment `dcc.Input` is `debounce=False` so a
  type-then-record (button or `r`) never saves a stale/empty comment — no
  callback keys on comment keystrokes, so per-keystroke updates are free.
  Schema (what `find_clusters.py --N_win_file`
  consumes; bare int when there's no comment):

  ```jsonc
  { "source": "0003-066u", "model_sha": "<sha256>",
    "choices": { "1995.57-2000.03": 6,
                 "2001.83-2006.51": {"N": 4, "comment": "..."} } }
  ```

- **Hand-off** is the usual cut-n-paste model: **"Generate rerun command"**
  reads the source's `run_string.txt`, strips `--editN` / `--show_results` /
  `--recalc_*` / any old `--N_win_file`, and appends `--recalc_IDs
  --N_win_file <abs path to nwin_choices.json>`. `--recalc_IDs` is added
  back unconditionally (exactly one, even if the run_string had none)
  because an N change usually wants the cross-window labels fully
  re-matched; it's cheap (cached fits are reused) and the user can delete
  it from the drafted command. The admin runs the command in the production
  working directory; cached fits make the rerun fast. Without `--recalc_IDs`
  the pipeline still invalidates + re-matches cross-IDs only for windows
  whose N changed (see `load_N_win_choices` / `cluster_window_matching` in
  cluster_code.py); with it, all cross-IDs are recomputed. Unmatched window
  labels are a hard error there — regenerate the choices file if the
  windowing changed.
- `cluster_fits/` is **excluded from the server sync**
  (server_sync/server_update_exclude.txt), so the panel is effectively
  local-only; on a server deploy it shows a hint instead of the body.
- All component ids are prefixed `nwin-`; callbacks register only when
  `--admin` ([`ui/nwin_callbacks.py`](mojave_review/src/mojave_review/ui/nwin_callbacks.py)).
  The beam-repositioning clientside callback is a copy of the main
  overlay's (same restyle-and-`no_update` discipline, different ids).

## Running the web app

```bash
cd mojave_review && pip install -e .          # one-time
mojave-review --results-dir ../Results        # opens http://127.0.0.1:8050
```

Useful flags: `--reviewer "Name"`, `--port 8050`, `--no-browser`,
`--cache-dir`, `--recommendations-dir`.

For Drive-mirrored data, reviewers should use Google Drive for Desktop and
point `--results-dir` at the local mirror path — no in-app Drive auth needed.

## Phase plan

1. **Phase 1** — pip-installable local app. ✓ Read-only viewer, ✓ FITS
   overlay, ✓ recommendations capture (selection-driven edits +
   Robustness/Edits/Notes tabs + multi-reviewer view). Keyboard shortcuts
   for the matplotlib-key parity (`n/b/i/a/u/r`) are the remaining
   optional polish item.
2. **Phase 2** — host on the user's university web server with **per-user
   static tokens** (small trusted group, ~6 reviewers). Same codebase;
   deploy differences are reviewer identity (from a `tokens.yaml`
   config keyed by the cookie / `?token=…` URL param) and per-user
   recommendations dirs. Full plan in
   [`docs/deployment_phase2.md`](docs/deployment_phase2.md). Google
   OAuth was evaluated and rejected as overkill at this group size.

### Phase 2 hosting requirements (for the IT conversation)

- Python ≥ 3.10 in user space (`pyenv`/`uv` OK).
- Long-lived process bound to a local port (`gunicorn` + systemd).
- Reverse-proxy line under existing nginx/Apache: `https://<host>/mojave-review/` → `http://127.0.0.1:<port>/`.
- Writable persistent dir (~50 GB) for FITS cache + recommendations.
  Nightly backup of just `recommendations/` desirable.
- Outbound HTTPS allowed to `www.cv.nrao.edu` (FITS only — **no
  Google endpoints needed**).
- No OAuth callback URL needed; no database; no special packages.

## Don't / gotchas

- **`robust` is a per-CLUSTER property — keep it uniform across a cluster's
  epochs.** A per-epoch-inconsistent `robust` flag is a latent bug (it made the
  overlay flicker a feature's colour). `apply.apply_recommendation` enforces this
  on every apply (`_normalize_robust_per_cluster`, canonical = earliest-epoch
  value, **core forced True**), so `mojave-apply` output is always consistent.
  The viewer collapses any stray inconsistency per-cluster too (summary's
  `iloc[0]`, `overlay.robust_by_cluster`). For the legacy back-catalog, the app
  shows a read-only ⚠ banner on load (`_robust_warning`) and
  **`mojave-review-audit-robust --results-dir … [--apply]`** sweeps/repairs
  existing CSVs (CSV-only, backs up the prior CSV + logs to `history.txt`;
  detect via `apply.robust_inconsistencies`).
- **Don't write back into `Results/`.** Recommendations are the only output.
- **`grab_mojave_image` in `cluster_code.py` opens local files**, not URLs.
  The web app has its own fetcher; don't try to reuse that function.
- **`.plotdata.npz` uses `allow_pickle=True`**. The CSV is the safer
  authoritative source for the cluster-level table; the npz is required
  only for FITS overlay (epoch_info + cc_data + cc_labels).
- **A backup CSV (`backup_NNN_*.csv`) has no matching npz.** Loader returns
  `plotdata=None` in that case — overlay panel must handle it gracefully.
- **`autorange="reversed"` + explicit `range=[hi, lo]`** fight. Pick one;
  the codebase uses explicit reversed ranges so `scaleanchor` composes.
- **Don't smooth the FITS image before contouring.** CLEAN images are
  already convolved with the restoring beam; additional smoothing blurs
  real structure. Use `line.smoothing` on the contour trace if you need
  to soften jaggy contour lines.
- **Clientside callbacks that "patch" a figure must call `Plotly.restyle`
  directly and return `no_update`**, not return a modified figure. The
  beam-positioning callback (`assets/`/inline in `ui/callbacks.py`) does
  this. Returning a modified figure breaks `uirevision`-driven zoom
  persistence across server callbacks — Dash treats it as a fresh
  figure replacement and resets the user's zoom. See the comment block
  near the clientside callback for the full story.
- **Dash's `assets_folder` defaults to `./assets`** relative to CWD, not
  the package install dir. `app.py` explicitly sets
  `assets_folder=<package-dir>/assets` so `pip install` ships the JS/CSS.
- **`*-open` Plotly markers use `marker.color` as the outline color.**
  Setting `marker.color="rgba(0,0,0,0)"` (transparent) erases the entire
  outline — the marker becomes invisible. Use a real color string. This
  bit the selection-halo overlay (see [`plots/summary.py`](mojave_review/src/mojave_review/plots/summary.py)
  `_add_cluster_traces`). Also: use SVG `go.Scatter` for thin-outline
  overlays — `Scattergl` is unreliable for open symbol outlines.
- **`clickData` doesn't change between identical clicks.** Dash callbacks
  fire on Input value-change, so re-clicking the same point silently
  no-ops. The click-toggle callback in [`ui/callbacks.py`](mojave_review/src/mojave_review/ui/callbacks.py)
  outputs `clickData=None` at the end of every invocation so the next
  click is seen as `None → {...}`. Guard against the resulting re-fire
  with `if not click_data: return no_update`.
- **Dash callbacks with `allow_duplicate=True` get hashed output keys.**
  curl-based smoke tests need the hash suffix (look it up via
  `app.callback_map`); browser dispatch handles routing automatically.
- **Recommendations against non-`current` models are not permitted.**
  Backup CSVs and other-reviewer JSON views are read-only. The
  recommendation panel applies an opacity/pointer-events lock and the
  autosave skips when `model_key != "current"`.

## Known issues

### RESOLVED: epochs "vanish" from the overlay timeline (keyboard arrows)

**Symptom (reviewer-reported, 2026-06):** epochs intermittently appeared to
go missing from the right-hand overlay timeline — stepping seemed to skip
over them. Sometimes they came back on their own, sometimes **Reset view**
fixed it. The decisive detail: **arrow keys sometimes advanced TWO epochs
per press**, dropping back to one per press after clicking a ◀/▶ button or
Reset view; and the dates under the timeline were sometimes wrong.

**Root cause:** `assets/keyboard.js` maps ◀/▶ arrows to clicking
`#epoch-prev` / `#epoch-next`, but it originally listened on `document` in
the **bubble** phase. The epoch control is a `dcc.Slider` whose focusable
handle *also* handles arrow keys natively. So when the slider handle had
focus, one keypress fired BOTH the slider's native step AND the
button-click handler → **+2 epochs**. After clicking a button / Reset view,
focus left the handle, so only `keyboard.js` fired → +1 ("self-heal").
Compounding it, the slider uses `updatemode="mouseup"`, so the native
keyboard move didn't reliably push its value to Dash — desyncing the handle
from the displayed epoch/date ("wrong dates").

**Fix:** `keyboard.js` now listens in the **capture phase** and calls
`stopImmediatePropagation()` before clicking the button, so the arrow never
reaches the slider's native handler. Every arrow press routes through the
single `#epoch-prev`/`#epoch-next` → `_step_epoch` path = exactly one step,
handle stays in sync with the Dash value. Text-field arrows are still
ignored (the editable-target guard returns before the stop). Reset view
remains as a manual backup.

**Earlier dead end (do not repeat):** this was first mis-read as a
contour/trace-render problem, and overlay traces were given stable `uid`s.
That backfired — a `uid` on the contour + plotly 6's base64-encoded `z`
made `Plotly.react` skip the contour redraw, freezing the image. That
commit was reverted. **Do not add `uid`s to the contour trace.**

## Useful local commands

```bash
# Audit (and repair) per-epoch robust inconsistencies in saved CSVs
mojave-review-audit-robust --results-dir ./Results            # dry-run report
mojave-review-audit-robust --results-dir ./Results --apply    # repair + backup

# Smoke test the loader + figure
python3 -c "
from pathlib import Path
from mojave_review.data.loader import list_sources, load_bundle
from mojave_review.plots.summary import build_summary_figure
srcs = list_sources(Path('Results'))
b = load_bundle(str(srcs[0].folder), 'current')
fig = build_summary_figure(b.cluster_df, view='Position')
print(len(fig.data), 'traces')
"

# Hit a Dash callback without a browser
curl -fsS -X POST http://127.0.0.1:8050/_dash-update-component \
  -H 'Content-Type: application/json' \
  -d '{"output":"summary-graph.figure",
       "outputs":{"id":"summary-graph","property":"figure"},
       "inputs":[
         {"id":"source-picker","property":"value","value":"<absolute folder path>"},
         {"id":"model-picker","property":"value","value":"current"},
         {"id":"view-picker","property":"value","value":"Position"},
         {"id":"vector-scale","property":"value","value":1.0}
       ],
       "changedPropIds":["view-picker.value"]}'
```
