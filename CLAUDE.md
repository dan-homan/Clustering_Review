# MOJAVE Cluster Review

Two related but distinct things:

1. **The clustering pipeline** — `cluster_code.py` (~6000 LOC, pipeline +
   interactive matplotlib review) and `find_clusters.py` (CLI driver). Produce
   per-source results under `Results/`.
2. **`mojave_review/`** — a pip-installable Dash web app that inspects those
   saved results and captures structured *recommendations*. It loads the
   pipeline's data products but **never** runs new clustering and **never**
   writes back into `Results/`.

Deeper design rationale lives in `docs/` (`review_workflow.md`,
`plot_conventions.md`, `product_decisions.md`, `uncertainty_estimates.md`,
`deployment_phase2.md`). Keep those in sync when behavior changes; keep this
file terse.

## Repository layout

```
Clustering_Review/
├── cluster_code.py              # pipeline + interactive matplotlib review
├── find_clusters.py             # CLI driver → Results/<source>_<emin>-<emax>/
├── Results/                     # one folder per source (mirror of Drive)
│   └── <source>_<emin>-<emax>/
│       ├── <prefix>.merged_win_results.csv          # main per-(epoch,clusterID) table
│       ├── <prefix>.merged_win_results.plotdata.npz # epoch_info + raw clean components
│       ├── <prefix>.summary_plots.pdf / .epoch_overplots.mp4  # reference renders (read-only)
│       ├── backups/backup_NNN_*.{csv,pdf,mp4,json,txt}    # prior models (NO npz)
│       ├── alt_models/alt_model_NNN_*.{csv,npz,json,txt}  # optional alt models (WITH npz)
│       ├── cluster_fits/                                  # per-window cluster fits
│       ├── config_win.json / history.txt / run_string.txt
└── mojave_review/               # the web app (pip-installable)
    ├── pyproject.toml
    └── src/mojave_review/
        ├── cli.py               # `mojave-review` entry point
        ├── app.py               # Dash factory (assets_folder → pkg dir; page router)
        ├── data/
        │   ├── loader.py        # source/model discovery, CSV+NPZ load
        │   ├── window_fits.py   # cluster_fits/*.npz loader + nwin_choices store
        │   ├── fits_cache.py    # MOJAVE URL + on-disk FITS cache
        │   ├── source_params.py # per-source redshift from source_run_param.csv
        │   ├── difficulty.py    # per-source difficulty score + stars
        │   ├── assignments.py   # _admin/assignments.json store + balancer
        │   ├── xviii.py         # Paper XVIII MRT parser → our cluster_df schema
        │   └── MOJAVE_XVIII_apjac230ft4_mrt.txt  # bundled XVIII Gaussian-fit table
        ├── plots/
        │   ├── summary.py       # Plotly port of make_summary_plots
        │   ├── overlay.py       # FITS + cluster + beam overlay per epoch
        │   ├── compare_overlay.py # XVIII overlay on the shared CC/FITS image
        │   ├── synthesize_fits.py # single-epoch + stacked Stokes-I synthesis
        │   ├── uncertainty.py   # CC-derived 1σ position/PA error bars
        │   └── _extent.py       # initial zoom-box from cluster footprint
        ├── recommendations/
        │   ├── schema.py        # dataclasses for the JSON shape
        │   ├── store.py         # read/write/list reviewer JSON files
        │   ├── apply.py         # apply a Recommendation to a cluster_df
        │   └── aggregate.py     # Stage-3 reconciliation (pure logic, tested)
        ├── assets/              # Dash auto-loads .js/.css (resizable, keyboard, etc.)
        └── ui/
            ├── layout.py        # header + body + recommendations panel
            ├── callbacks.py     # source/model/view + selection + summary/overlay
            ├── urls.py          # rel(): prefix-aware in-app links (reverse proxy)
            ├── dashboard.py / dashboard_callbacks.py  # Assignment Dashboard page
            ├── compare.py / compare_callbacks.py      # /compare XVIII-vs-clustering page
            ├── nwin_panel.py / nwin_callbacks.py      # admin Window-N review
            ├── aggregation.py                          # Stage-3 aggregate panel body
            └── recommendations_panel.py / *_callbacks.py  # 4-tab bottom panel
```

`<prefix>` = `<source>.<emin>-<emax>` (e.g. `0003-066u.1994.00-2026.00`).
Source folder = `<source>_<emin>-<emax>` (underscore, not dot).

## Data schemas (worth memorizing)

### `merged_win_results.csv` (one row per epoch × clusterID)

Key columns: `source, band, ep_name, epoch (decimal yr), clusterID,
N_Icc, N_QUcc, avg_x, avg_y, dist, pa, core_x, core_y, fwhm_maj, fwhm_min,
iflux, qflux, uflux, pflux, evpa, select, robust, use_in_fit,
ClusterType, Nclusters, Nepochs, ep_min, ep_max, ref_epoch, origID,
medianFlux, centX, centY, slopeX, slopeY, accelX, accelY, sizeMaj, sizeMin,
sizePA, bmaj, bmin, bpa, inoise, pnoise`.

- `clusterID == -1` → unassigned CCs (one row per epoch; avg_x/y = NaN).
- `clusterID == 0` → core (typically). Don't plot PA for the core.
- `clusterID >= 1000` → special/synthetic; treat like unassigned (black `+`).
- `robust`, `use_in_fit`, `select` are Python booleans after read_csv.

### `merged_win_results.plotdata.npz` (numpy pickle, `allow_pickle=True`)

- `epoch_info` — structured array, one row per epoch: `(epoch_name, epoch_val,
  band, cc_file, fits_file, inoise, pnoise, sigma_cut, sigma_cut_area, bmaj,
  bmin, bpa, pix_to_mas)`.
- `cc_data` — structured array per clean component: `(epoch, x, y, stokes,
  flux, sizex, sizey, group, clusterID)`.
- `cc_labels` — int32 per-cc cluster labels (parallel to `cc_data`).
- `root_data_dir` — where FITS lived on the generating machine. The web app
  **ignores this** and fetches FITS from MOJAVE. `mojave-apply` overrides it
  when regenerating plots: passes `$MOJAVE_DATA` if set, else the baked-in
  value (`cli/apply._resolve_root_data_dir`).

### `source_run_param.csv` (one row per source; local data, gitignored)

NOT under `Results/` — a CSV next to the production data (found via
`data/source_params.find_source_params`: cwd → `results_dir` parent →
`results_dir`). App reads only `Source` (band-less, e.g. `0003+380`, matched via
`split_source_band`) and `redshift` (float `z`, blank = unknown). Loaded once
into `{source: z}` (`load_redshifts`); blank/non-positive z → treated as z=0.
Drives host-frame Tb `(1+z)` and Kinematics `beta_app`.

## MOJAVE FITS retrieval

URL pattern (from `grab_mojave_image` in `cluster_code.py`):

```
http://www.cv.nrao.edu/2cmVLBA/data/<source>/<epoch_name>/<source>.<band>.<epoch_name>.<stokes>cn.fits.gz
```

`<epoch_name>` = underscored form `1995_07_28`; `<stokes>` ∈ {i,q,u};
`<band>` = per-source code (`u` for U-band). Fetched on demand, cached under
`~/.mojave_review/cache/<source>/<epoch_name>/...` (`--cache-dir`).

## Web app: views and conventions

`build_summary_figure(view=...)` → a 2-row figure for four views, single-plot
for `Position Angle`:

| View | Top | Bottom |
|---|---|---|
| Position | distance vs epoch (+ polyfit), 1σ bars | centroid track (x,y) mas vs core, +x reversed, equal scale (letterbox), 1σ bars |
| Position Angle | *(single)* PA vs epoch, 1σ bars | — |
| Flux | I flux vs epoch (log y) | Tb vs epoch (log y; 15.4 GHz, z) |
| Polarization | P flux vs epoch (log y) | EVPA vs epoch |
| Kinematics | speed vs distance, 1σ bars, axes at 0 | X/Y velocity vectors w/ arrowheads, +x reversed |

1σ bars come from `plots/uncertainty.attach_position_uncertainties`
(`sig_dx/sig_dy/sig_dist/sig_pa`); see `docs/uncertainty_estimates.md`. The
**Visualize recommendations** checkbox defaults ON.

**Active-epoch marker.** The overlay's current epoch is drawn as a thin vertical
line on epoch-axis subplots so left/right panels stay linked while scrubbing.
`_epoch_label` publishes the decimal epoch to `dcc.Store(id="active-epoch")`; a
**clientside** callback sets `shapes` via `Plotly.relayout` (no trace rebuild →
cheap, preserves zoom). A per-view `epochAxes` map picks subplots: Position/PA →
`[x]`, Flux/Polarization → `[x, x2]`, Kinematics → none (resets shapes to `[]`).
Also keys on `summary-graph.figure` to re-apply after server rebuilds. Shapes
are exclusively this callback's.

**No subplot/figure titles** (they duplicated axes + ate space). Instead
top-left badges:
- Summary: `build_summary_figure(source_label=)` draws the source name above
  each subplot, anchored to the **axis domain** (not paper) so it tracks the
  resizable divider. Kinematics vector panel folds its meaning into the bottom
  badge: `<source>,  X/Y Vector Plot`.
- Overlay: three-line badge — `<b>source</b>` / `epoch (val) · cbase` (+ mapping
  caveat) / provenance (`Clean Component Convolution` [`-Stacked · N epochs ·
  median beam`] or `FITS Image`, set as `image_source_label`). Anchored to
  `x/y domain`; top margin 52 to clear 3 lines.

**Resizable left-pane divider.** `assets/subplot_resize.js` floats a horizontal
grab bar over `#summary-graph`; drag re-balances the two subplots' `yaxis.domain`
via `Plotly.relayout`. Domains are independent of ranges, so zoom/legend survive.
Split fraction lives in a module var, re-applied on `plotly_afterplot` (guarded
against recursion). Default `0.50` reproduces `vertical_spacing=0.10`. Single-plot
views have no `yaxis2`; handle hides.

**View selectors (both panes are dropdowns in the panel title).** The left-pane
view selector is `dcc.Dropdown(id="view-picker")` sitting where the "Summary
plots" `<h4>` was (relocated from the header radios — same id + value strings, so
`_refresh_summary`, `_toggle_scale_row`, the click-selection callback and the
clientside active-epoch marker are all unchanged). Its five values are the
`build_summary_figure` views (labels = view names as-is).

**Right-pane mode selector.** A `dcc.Dropdown(id="right-pane-mode")` sits where
the "Epoch overlay" `<h4>` was, mirroring the left selector. `"overlay"`
(default) shows the per-epoch overlay exactly as before; any other value is a
`build_summary_figure` view string (same set as `view-picker`, plus the extra
`"overlay"` option) rendering a **second summary** in
`summary-graph-right` for side-by-side view comparison. `_toggle_right_pane`
flips `display` on `#overlay-mode-container` (wraps the epoch controls +
`overlay-graph`) vs `#summary-right-container` — so the epoch controls hide in
summary mode; the ◀/▶ buttons stay in the DOM (hidden), so `keyboard.js` is
untouched. Both summaries share `_build_summary_fig` (df resolution + error bars
+ selection highlight); distinct `uirevision` prefixes (`summary:` vs
`summary-right:`) keep their zoom independent. The right pane is **read-only for
selection** (highlights ride along via the resolved `select` column, but clicks
don't originate edits — the click-toggle is bound to `summary-graph`).
`_toggle_scale_row` shows the vector-scale slider when **either** pane is
Kinematics. `subplot_resize.js` stays bound to `#summary-graph` (no intra-pane
resize on the right).

### Plotting conventions (spatial/sky plots) — also `docs/plot_conventions.md`

- **+x to the left** (astronomical). Use explicit `range=[hi, lo]`, not
  `autorange="reversed"`, so it composes with `scaleanchor` and so the letterbox
  (below) can read a concrete range.
- **Equal mas/pixel**: keep px/mas equal so a square in data is a square on
  screen and ellipses stay round at every zoom. Two mechanisms coexist:
  - **`scaleanchor`** (`scaleratio=1.0` + `constrain="domain"`) — used by the
    **Kinematics** vector panel. Equal scale, but drag-zoom is **locked to the
    panel aspect** (can't isolate a tall-skinny / wide-flat region). Reviewer
    accepted this there (2026-05-28) — **don't "fix" it by dropping
    `scaleanchor`** without the letterbox below.
  - **Letterbox** (`assets/equal_aspect.js`) — used by the **Position view's XY
    bottom panel**, the **epoch overlay** (reviewer-requested 2026-07-01), and
    the **admin Window-N overlay** (`nwin-overlay-graph`, same overlay figure).
    Those figures DROP `scaleanchor` (free-form / arbitrary-shape zoom) and the
    script re-imposes equal units by narrowing an axis *domain* so px/mas match
    the current ranges. Mode is chosen by `layout.meta` (titles can't
    disambiguate — Kinematics' bottom shares `X/Y [mas]` axes but keeps
    `scaleanchor`):
    - `meta == "xy-bottom"` (Position bottom subplot): **horizontal-only** —
      narrows `xaxis2.domain` only. `subplot_resize.js` owns the vertical split
      (`yaxis`/`yaxis2.domain`); disjoint props → no conflict. Equal units hold
      while the box has enough width; a wide-flat zoom may need a top/bottom
      divider nudge to stay equal.
    - `meta == "overlay-equal"` (overlay single panel — standard **and** admin
      Window-N overlay): **full-2D** — narrows whichever of `xaxis.domain` /
      `yaxis.domain` is roomier. Safe alongside the beam callback: that uses
      `Plotly.restyle` (no relayout event) and ignores domain-only relayouts
      (keys on `xaxis.range[*]`/`autorange`). `equal_aspect.js`'s `GRAPH_IDS`
      must list every such graph; `nwin-overlay-graph` renders inside a
      collapsed `<details>` so the wiring poll backs off to 1 Hz instead of
      giving up at 8 s. `wire` skips graph ids absent from the DOM (non-admin).
    Do NOT restore `scaleanchor` on the XY or overlay panels.
    - **Self-healing (why a stuck zoom can't survive):** the written domains are
      preserved by the figure's constant `uirevision` (for zoom persistence), so
      a bad domain would otherwise stick — and the modebar **home / double-click
      reset ranges but NOT domains** and don't bump `uirevision`, so they can't
      clear it. The script therefore recomputes on **both** `plotly_afterplot`
      AND `plotly_relayout` (the latter fires once with the *settled* ranges
      after any interaction, incl. a reset), rejects non-finite/transient inputs,
      and the re-entrancy guard is bulletproofed (try/catch + `finally` +
      watchdog) so it can never latch on. So the letterbox always re-converges
      and `uirevision` only ever preserves a *good* domain. Belt-and-braces: the
      **"Reset view"** buttons bump a counter folded into `uirevision`
      (`overlay-reset-counter`; `summary-reset-counter` for BOTH summary panes)
      → full axis re-init, the only one-click flush that also survives a stuck
      domain since it changes the key.
- Arrows: `fig.add_annotation(showarrow=True, arrowhead=2, ...)` — never
  line-mode + triangle-marker hacks.
- Always show black `×` at `(0,0)` (core); include `(0,0)` in auto-range.

### Overlay panel (FITS + cluster overlay per epoch)

`overlay_figure_for_epoch(bundle, epoch_int, cache_dir, source_no_band, band)`
→ `(figure, beam_params)`. Layers, bottom to top:

1. Contour of FITS Stokes-I. Levels at `cbase × 2ⁿ`, `cbase = 3.5 × inoise`.
   CLEAN image is already beam-convolved — **do NOT smooth before contouring**;
   `line.smoothing=1.0` (Bezier) is the most we add.
2. Clean components, colored by cluster (`cc_labels` mapped `origID →
   clusterID`; `origID` is the stable join key — `mojave-apply` rewrites
   `clusterID` but never `origID`, npz isn't regenerated). **Robust styling is
   per-CLUSTER**: `robust` can vary across a cluster's epochs; overlay collapses
   to the earliest-epoch value via `overlay.robust_by_cluster` (matches
   summary's `iloc[0]`) for CC scatter AND ellipses. Raw per-epoch flag made
   features flicker while scrubbing.
3. 3σ ellipse `2.548 × FWHM`, dotted, faint fill. Gated by `show_3sigma`
   (default False, no UI toggle).
4. FWHM ellipse: solid outline, faint fill. A **point-like** fit — size
   (`√(fwhm_maj·fwhm_min)`) 0 or `< POINT_SIZE_MAS` (0.05 mas) — draws a bold
   `+` (`symbol="cross"`) in the cluster colour instead (its ellipse would be
   invisible; XVIII Gaussian fits are occasionally exactly point-like).
5. Black cluster-number labels (skip core).
6. Black `×` at core.
7. Beam ellipse (lower-left) — **clientside callback** tracks the viewport on
   zoom/pan without a server round-trip (see gotchas).

**Contour source (header checkboxes)**, precedence order:
- **Stacked image** — overrides all. Every epoch's Stokes-I CCs shifted to that
  epoch's fitted core, accumulated on a common grid (median `pix_to_mas`),
  divided by epoch count, convolved once with the **median beam**. Built by
  `synthesize_fits.synthesize_stacked_stokes_i`, cached per `(source, model,
  csv_sha)`. Beam/`cbase` use medians. Cluster overlay still tracks the slider.
- **Use FITS images** — real CLEAN FITS for the epoch.
- default — synthesize the single epoch from its CCs + own beam
  (`synthesize_stokes_i`). Both synth paths share `_render_image`/`_beam_kernel`.

### Cluster styling (ported from cluster_code.py)

```python
cl_colors  = ["b", "g", "r", "m", "y", "gray"]    # cycle by clusterID
cl_markers = ["x","o","s","o","s","p","*","^","v","*","^","v","X","D","P","D","1"]
cl_fill    = ["none","full","none","none","full","none","full","none","full",
              "none","full","none","full","none","full","full","none"]
```

- Non-robust clusters → all slategray (`#708090`), keep marker/fill. In the
  legend (toggle/isolate by click). **Hide non-robust clusters** checkbox
  (`build_summary_figure(hide_non_robust=)`) drops them from plots + legend;
  unassigned (-1) counts as non-robust here, synthetic (`>=1000`) unaffected.
- `clusterID == -1` → black `+`, in legend. `>= 1000` → black `+`, never in
  legend. `_add_cluster_traces` gates legend on `cid == -1 or 0 <= cid < 1000`.
- `use_in_fit == False` → black slash overlay. `select == True` → gold
  open-diamond overlay.

### Numerical quirks ported from `make_summary_plots`

- **PA de-wrap**: `|pa[i]-pa[i-1]| > 300°` → nudge ±360°.
- **PA shift** (`shift_pa`): if median |pa| > 120° across non-core clusters, add
  360 to any PA < -60.
- **EVPA de-wrap**: period 180°, jump 150°.
- **Size floor**: `size = max(sqrt(fwhm_maj*fwhm_min), 0.1)` mas.
- **Tb**: `1.22e12 * flux * (1+z) / (15.4² * size²)` K (U-band). `z` per-source;
  known z → host-frame ("Tb host-frame [K]"), z=0 → observed ("Tb obs [K]").
- **Position polyfit / projected motion** (`_motion_fit`): every robust cluster
  with ≥5 valid `use_in_fit` points. Drawn for ALL such by default; the
  `>3σ OR (speed<0.05 & err<0.05)` test is recorded as `_MotionFit.significant`,
  not a gate. **Hide uncertain motions** checkbox (`only_3sigma=`) filters to
  significant. `_MotionFit.speed_err` drives Kinematics speed error bars.

### Per-source redshift (`source_run_param.csv`)

`data/source_params.py` loads `{source: z}` once; `_refresh_summary` passes
`z = redshift_for(map, src.source) or 0.0` to `build_summary_figure(z=)`. Uses:
host-frame Tb `(1+z)`, and `beta_app = (1+z)·µ·D_A/c` on Kinematics hovers
(`source_params.beta_app`, astropy + flat ΛCDM H0=71 Ωm=0.27; omitted when z
unknown).

## Recommendations

Per-(source, model, reviewer) JSON at
`<recommendations-dir>/<source>/<model>/<reviewer-slug>.json`. The app NEVER
modifies `Results/`. Full schema in `recommendations/schema.py`:

```jsonc
{
  "source": "0003-066u", "model": "current",
  "model_sha": "<sha256 of the CSV reviewer saw>",
  "reviewer": "Reviewer Name", "updated_at": "2026-05-28T14:09:32+00:00",
  "source_comment": "...",
  "no_robustness_changes": false,   // sign off on robust flags as-is
  "cluster_feedback": {"3": {"recommended_robust": false, "comment": "..."}},
  "epoch_feedback": {"2003.10": {"comment": "..."}},
  "edits": [
    {"op": "change_clusterID", "scope": "single"|"all_epochs", ...},
    {"op": "set_use_in_fit",    "scope": "single"|"epoch",       ...}
  ]
}
```

- `recommended_robust`: `true`/`false`/`null` (no opinion). When ≠ the model's
  current flag, a `set_robust` edit is **derived** at render/apply time — NOT
  written into `edits[]`.
- `use_in_fit` scopes: `single`, `epoch`. `change_clusterID` scopes: `single`,
  `all_epochs` (multiple cids in one batch = merge).

### Source picker labels (`build_source_options`, `ui/layout.py`)

Option = `<source>   <reviewer-status>   [badge]`, plain strings (see the
`dcc.Dropdown` gotcha) with a `search` field = source name for type-to-filter.
Refreshed by `_refresh_source_badges`. Date range dropped (uniform). Status
(`_reviewer_status`, per reviewer): *needs review* (open + untouched), "review
in progress" (non-empty `current/` draft, no submission), **submitted** (bold).
Locked (`stage1`/`stage2`) and `final` get no note. Source-level **needs
discussion** flag (bold-orange) overrides all per-reviewer status. Badge
(`store.source_badge`): `[N]` / `[final]` / `[final − M]` / `[stage 1]` /
`[stage 2]`.

**Ordering.** Reviewers: their outstanding (unsubmitted) assignments first
(`★`-prefixed), then the rest, each group alphabetical. Admin
(`build_source_options(admin=True)`): triage queue — **needs discussion**
(`‼`) first, then **open + ≥2 submitted reviews** (`★`, ready to aggregate),
then **stage 1 / stage 2** (baseline work), then the remaining **open** sources
(needs review), then everything else (finalized), each group alphabetical.

### Panel layout (4 tabs)

`Robustness` (default) — `ID / use-in-fit Edits` — `Source Notes` — `Epoch
Notes`. Auto-saves on every field change.

| Tab | Contents |
|---|---|
| Robustness | "No changes suggested" checkbox + a row per *eligible* cluster (≥5 epochs `use_in_fit=True`) via `build_cluster_rows` (NOT a DataTable): clusterID, inline Robust/Non-robust radio **preloaded to current**, comment. Only a pick DIFFERING from current is recorded (row highlights soft-red). Core (0): radio disabled (always robust), comment editable. |
| ID / use-in-fit Edits | Selection-driven action panel (visible when summary points are selected) + pending-edits list (manual + derived `set_robust`). Manual edits get `[remove]`; derived are read-only, tagged "from Clusters tab". |
| Source Notes | One textarea, `source_comment`. |
| Epoch Notes | One `dcc.Textarea` per epoch (`build_epoch_rows`), NOT a DataTable cell. |

**Store-bridge pattern (both Robustness & Epoch Notes):** the DataTables were
replaced by real inputs because dropdown/edit popups rendered off-screen inside
the scroll panel. `cluster-feedback-table` / `epoch-feedback-table` are now
`dcc.Store`s mirroring the old `.data` shape, so consumers are unchanged.
`_sync_cluster_store` / `_sync_epoch_store` bridge inputs into the stores
(radios immediate on `value`; comments commit-on-blur on `n_blur`). `_do_submit`
reads the **live** input values directly (the bridge is a server round-trip the
submit microtask can't await). Radios preloaded to current → `build_rec_from_ui_
state` records an opinion only when a pick differs (else `None`), keeping
`is_empty()` / vote aggregation correct.

**Read-only modes:** `current` = editable/autosaves; `backup_NNN` /
`alt_model_NNN` = locked, empty; `Rec: <slug>` = locked, shows that reviewer's
**submitted** JSON (drafts under `current/` stay private). Visual lock =
`opacity:0.7; pointer-events:none` on `#recommendations-panel`.

### Reset Recommendation dialog

Button beside Submit (current model only; `_submit_button_state`). 3-choice
modal (`#reset-rec-modal`): **Reset to last submitted** (loads
`submitted/<own-slug>.json` → `current/`; disabled when none), **Delete draft &
submitted** (`store.delete_recommendation` + `delete_submission`; empty rec is
never re-written so autosave won't recreate it), **Cancel**. Both actions bump
`rec-reset-counter` (Input on `_submit_button_state`) to re-evaluate Submit ⇄
Resubmit. Only touches the current reviewer's own files.

### Selection-driven edits

Summary points carry `customdata=[clusterID, epoch]`. **`_customdata` must
return plain Python lists, NOT numpy** — plotly.py 6 base64-encodes numpy as
typed arrays, so `customdata` reaches the browser as an object `{"0":cid,"1":ep}`
and `cd[0]` indexing silently fails. Cluster scatter uses SVG `go.Scatter` (not
`Scattergl`) for reliable click hit-testing + gold-halo overlay.

Two callbacks write `dcc.Store(id="selection-store")`: **clickData** toggles one
(cid, epoch) and **resets `clickData` to None** (else a repeat click no-ops);
**selectedData** (box/lasso) replaces contents. No-ops on Kinematics. Store
clears on source/model change. Highlight: set `cluster_df["select"]` per row from
the store; gold open-circle overlay renders the halo. **Open symbols use
`marker.color` as the outline** — transparent color erases it; use `"gold"` +
`go.Scatter`.

Action buttons (Edits tab) turn a selection into edits:

| Button | Edits |
|---|---|
| Mark use_in_fit=False on selected points | N × `set_use_in_fit / single` |
| Mark whole epoch use_in_fit=False | K × `set_use_in_fit / epoch` |
| Renumber selected points to ID X | N × `change_clusterID / single` |
| Renumber all epochs of selected clusters to ID X | M × `change_clusterID / all_epochs` (merge if >1 cid) |

Each click attaches the optional Comment to every generated edit; comment resets
after apply, selection persists.

### Visualize-recommendations + multi-reviewer

Header checkbox controls whether `recommendations/apply.py` mutates `cluster_df`
before summary + overlay build:

| Model | Checkbox | Plots show |
|---|---|---|
| `current` | off (default) | raw current |
| `current` | on | current + user's in-progress UI recs |
| `backup_NNN` / `alt_model_NNN` | disabled/off | raw that-model data |
| `Rec: <slug>` | forced on | current + that reviewer's JSON recs |

Model dropdown (`_populate_models`): `current` + `backup_*` + `alt_model_*` + a
`Rec: <slug>` per **other-reviewer submitted** JSON (drafts never surfaced).

**Alt models (`alt_models/`)** ship their own `alt_model_NNN_plotdata.npz`, so
the loader sets `npz_path` and the overlay renders the alt model's own CCs.
Otherwise treated exactly like backups (read-only) — every `model_key !=
"current"` gate already covers them; no alt-specific branching.

### Stage-3 aggregation panel (admin only)

Rendered only with `--admin`. Reconciles every reviewer's submitted rec for the
source into one model. Pure logic in `recommendations/aggregate.py` (tested);
body in `ui/aggregation.py`. See `docs/review_workflow.md` for the full stage
model.

- **Robustness decisions** — row per voted cluster, column per reviewer, single
  **Final** dropdown. Default = `default_final_robust` (majority of reviewer
  votes + current model as one equal vote; tie → current). Optional Reason.
- **Cross-ID / use-in-fit edits** — row per *unique* edit (dedupe excludes
  comments; proposers listed). **Accept** checkbox (default off) + Reason.
  Ordered change_clusterID-before-use_in_fit.
- **Reviewer comments** — read-only context.
- **Preview** — `_compose_agg` → one `Recommendation` (`compose_aggregated`);
  while **Preview aggregated** is ticked, published to
  `dcc.Store(id="agg-preview-rec")`, which `_resolve_df_for_plot` prefers over
  the reviewer's own Visualize on `current`. Store always present (None for
  non-admin).
- **Apply** — confirm modal forks on `Recommendation.is_empty()`:
  - **Has decisions** → **generates a copy-paste `mojave-apply` command** (NOT
    an in-app subprocess — that hit the `MOJAVE_CODE` env pitfall).
    `_apply_aggregated` writes `stage3/aggregated.json` + sidecar
    `aggregated.stage3.json` (`considered_slugs`, pre-rendered
    `stage3_ledger_entry` with `{{BACKUP_REF}}` placeholder + run N, target
    `status`), then shows the command:
    `mojave-apply --recommendation …/aggregated.json --stage3-meta
    …/aggregated.stage3.json` (+ dirs, no `--no-confirm`). `mojave-apply
    --stage3-meta` (`cli/apply._apply_stage3_meta`) does the bookkeeping
    atomically: backup + regenerate `Results/` (PDF/MP4 opt-in via
    `--make-plots`; default SKIPS regen and MOVES prior plots into the backup),
    archive JSON → `applied/`, move `submitted/*.json` → `considered/<date>/`,
    append ledger (resolve `{{BACKUP_REF}}`), Status → `Stage 3 done · applied
    <date>`. Admin then clicks ↻ Reload.
  - **No decisions** → bookkeeping fully in-app (no `Results/` mutation):
    append `stage3_no_change_ledger_entry` (folds `pending_notes_seed`), Status
    → `Stage 3 done · finalized (no changes) <date>`, archive `submitted/*.json`
    → `considered/<date>/`, bump `reload-counter`.
- **Needs Discussion** (`_mark_needs_discussion`) — flags for discussion without
  finalizing; source stays Stage 2 (`open`, nothing archived, `Results/`
  untouched). Adds `· needs discussion <date>` to Status (idempotent) +
  ledger entry (folds `pending_notes_seed`). Detected by
  `store.source_needs_discussion`; `_reviewer_status` checks it before
  per-reviewer branches. Refuses unless phase is `open`. Cleared by a later
  Stage-3 apply.
- **Repeat applies (run N)** — everything **appends**: `stage3_ledger_entry
  (run_index=)`, `applied/<date>__aggregated[_n].json`, new backup,
  `considered/<date>/`. `final` + no open submissions → panel shows a
  waiting-for-next-round hint.
- **Dated note box** — textarea + "Add dated note to log" appends `### <date> —
  Note (by <admin>)` (`notes.dated_note_entry`). Seeded with pending reviewer
  comments (`notes.render.pending_notes_seed`, tagged `(reviewer)` with
  PARENTHESES — `[...]` would render as a markdown link). "Reseed from
  submissions" re-pulls.

### Stage 2 vs Stage 3 apply — two distinct admin paths

Both use the cut-n-paste model (generate a `mojave-apply` command; the app never
shells out and never writes `Results/`):
- **Stage 2 baseline** — "Generate baseline apply command" (next to Stage-2
  notes editor) → `mojave-apply --recommendation <admin's own current/submitted
  JSON>`. `recommendations_callbacks_admin._do_generate`.
- **Stage 3 aggregated** — "Apply aggregated decisions" (🧩 panel) → the
  `--stage3-meta` command above.

Both populate the shared `apply-cmd-modal`. **Stage-gated visibility**
(`store.source_phase`): `stage1`/`stage2` → baseline button shows, 🧩 hidden;
`open`/`final` → baseline hidden, 🧩 shows (`_toggle_btn_visibility` /
`_toggle_agg_panel`). Stage-2 notes editor also has "Seed from submission
summary" (`format_submission_text` cleaned by `notebook_format.strip_for_notes`).

## Window-N review (admin — the `--editN` replacement)

Rendered only with `--admin`. Browses cached per-window fits and records per-window
N choices — no clustering runs in the app. The pipeline caches a fit for every
candidate N per window under `Results/<source>/cluster_fits/`.

- **Data** (`data/window_fits.py`): `list_window_fits` discovers files;
  `window_bundle(src, ref, N)` adapts one (window, N) fit into a `SourceBundle`
  so `overlay_figure_for_epoch` renders it unchanged (always synthesize).
  `bic_table` reproduces `BIC* = ln(Ndata)·k + complex·Ndata·⟨d²⟩/⟨Σbeam²⟩`;
  `complex` is the CURRENT model's value (`load_complex_factor`:
  `config_win.json` → `config.json`), NOT baked into window CSVs. BIC*-vs-N uses
  log y when all values positive. `build_window_meta` adds current N per window.
- **UI**: window/N/epoch sliders (epoch defaults to window ref epoch), BIC*-vs-N
  curve + N-per-window strip chart (click to jump), overlay for (window,N,epoch).
  N slider seeds: recorded choice > current N > BIC* suggestion.
- **Fixed zoom**: one source-wide box over every candidate cluster of every
  window × N (`window_fits.global_window_extent`, stored in
  `WindowMeta.extent`). `build_window_overlay` overrides per-(window,N) ranges +
  repositions beam. Constant uirevision
  (`nwin-overlay:<folder>:<reset-counter>`) → view never jumps; drag-zoom
  persists. **Reset view** (`nwin-reset` → `nwin-reset-counter` in uirevision)
  restores the box (needed: double-click autoranges and won't toggle back).
- **Keyboard** (while `#nwin-details` OPEN): `assets/keyboard.js` routes ←/→ →
  `nwin-win-prev/next`, ↑/↓ → `nwin-n-up/down`, `r` → `nwin-record-btn`
  (capture-phase + `stopImmediatePropagation`; skipped in text inputs). Panel
  closed → ←/→ step the main epoch overlay.
- **Resizable split**: `#nwin-split-handle` via the shared `assets/resizable.js`.
- **Choices** autosave to `<recs>/<source>/nwin_edits/nwin_choices.json` on
  every record/clear (deleted when last cleared; `model_sha` = merged CSV).
  Directory is stage-agnostic (N editing is offline Stage-2 work). No on-screen
  list — recorded N = red dots on the strip chart; per-window comment loads via
  `_nwin_load_comment`. Comment `dcc.Input` is `debounce=False` so
  type-then-record never saves a stale comment. Schema (what
  `find_clusters.py --N_win_file` consumes; bare int when no comment):
  ```jsonc
  { "source": "0003-066u", "model_sha": "<sha256>",
    "choices": { "1995.57-2000.03": 6,
                 "2001.83-2006.51": {"N": 4, "comment": "..."} } }
  ```
- **Hand-off**: "Generate rerun command" reads `run_string.txt`, strips
  `--editN`/`--show_results`/`--recalc_*`/old `--N_win_file`, appends
  `--recalc_IDs --N_win_file <abs nwin_choices.json>`. `--recalc_IDs` added
  unconditionally (N changes usually want full cross-window re-match; cheap with
  cached fits; user can delete). Run in the production working dir. Unmatched
  window labels are a hard error there — regenerate choices if windowing changed.
- `cluster_fits/` is **excluded from server sync**
  (`server_sync/server_update_exclude.txt`) → panel is local-only; server deploy
  shows a hint. All ids prefixed `nwin-`; callbacks register only with `--admin`
  (`ui/nwin_callbacks.py`).

## Assignment Dashboard (`/dashboard`)

Second page (header link, `target="_blank"`), built by `ui/dashboard.py`; admin
actions in `ui/dashboard_callbacks.py` (registered unconditionally, bound to
admin-only components). Router in `app.py` on `pathname.endswith("/dashboard")`.

### Storage — `recommendations/_admin/assignments.json`

One JSON store (`data/assignments.py`), **under `recommendations/`** so the
normal sync carries it to the server (admin curates locally, syncs). Atomic
write + rotating snapshot to `_admin/backups/` (last 10). Schema versioned (v5);
`to_dict` emits full shape, `touch()` bumps version, so load→save upgrades.
Fields:
- `assignments: {reviewer_name: [AssignmentRecord]}` — keyed by full reviewer
  **name** (from `tokens.yaml` `name:`); must match deployed tokens.
- `source_target_dates: {source: "YYYY-MM-DD"}`. **"Save & set Stage 2 done"**
  (`_save_stage2`) auto-sets this to **today + 14 days** — opening a source for
  review gives reviewers a standard two-week window.
- `team_members: [name]` (v4) — manual roster (`prune_collision_reviewers`
  strips phantom `<base>_<N>`).
- `manual_reviews: {reviewer_name: [source]}` (v5) — explicit Stage-2 credit for
  sources advanced with no artifact. Counted completed, never in-progress.
- `paused_reviewers: [name]` — excluded from auto-balance, queue kept.

### Difficulty score (`data/difficulty.py`)

`score = N_epochs × mean(features_per_epoch)`, mapped to star cutoffs (★…★★★★★)
with ⚠ outlier flag. `balance_weight = sqrt(score)` compresses the tail; the
balancer schedules on `balance_weight`.

### Roster identity — names vs slugs (recurring gotcha)

Store keys on **names**; on-disk files key on **slugs** (`reviewer_slug(name)`).
Bridges translate explicitly. **Collision artifacts**: re-archived submission →
`considered/<date>/<slug>_2.json`; the `_N` suffix is folded to the base slug
everywhere identity comes from a filename (`all_submitting_reviewers`,
`all_review_submitters`, `reviewer_submitted_sources`,
`dashboard.known_reviewers`'s `_fold_collision_names`). Forgetting the fold mints
a phantom reviewer.

### Page anatomy

- Two stat-tile banners: **Team** (Total / Finalized / Ready for Completion /
  Ready for Review / Stage 1/2) and **My**. "Ready for Completion" = open with ≥
  `target` reviews **beyond the viewer's own** (viewer-relative); "Ready for
  Review" = open but short.
- **My queue** — assigned sources (admin: every Stage 1/2 source). Paginates 10.
- **Sample Status** — every source under a phase filter: Source / Rating /
  Reviews needed / Reviews (all-time) / Pending Reviews / Target. Paginates 10.
- **Reviewer summary** (admin) — expandable per-reviewer: current queue +
  lifetime **Completed** (≥ Submitted), off-queue drafts flagged `✎N`.

Source names are markdown deep-links to `<root>/?source=<name>`
(`_deeplink_source` selects the picker option). Picker lists a reviewer's
outstanding assignments first, `★`-prefixed (labels are plain strings — see
gotcha — so `★` stands in for bold).

### `assignment_status(recs, source, reviewer)`

`submitted` / `in_progress` / `pending`. **`submitted` = submitted at any time**
(open `submitted/` OR Stage-3 `considered/` OR Stage-2
`applied/<date>__<slug>.json`) — so a finalized review with a stale `current/`
draft reads as done. Only `pending` is eligible to move in a rebalance.

### Admin balancing actions (preview-then-apply; move PENDING only)

In `dashboard_callbacks.py`; each writes only `store.assignments`, navigates via
`url.href` (prefix-aware):
- **🔀 Auto-balance** — fill open slots (LPT on `balance_weight`); additions
  only. Schedules on current load alone by default. **"Consider completed
  reviews"** checkbox (off by default) → `credit_prior_submissions` pre-seeds
  completed load (past contributors get a lighter share; first-round only).
  Admin excluded.
  **"Only unassigned sources"** checkbox → `auto_balance(only_sources=)`:
  fills slots only on open sources with no outstanding (non-`submitted`)
  assignment; full scored list still seeds load.
- **⚖ Top-up rebalance** — *move* pending to even out load. "Consider completed"
  checkbox → `rebalance_pending(base_load=)`.
- **🏖 Redistribute (break)** — spread one queue across the pool, optional cap +
  auto-pause.
- **↔ Move source**, **↪ Reassign queue**, **📅 Set target dates**, **👥 Manage
  team**, **✓ Credit my Stage-2 reviews**.

## XVIII Comparison page (`/compare`)

Read-only third page (header link on the review page, `target="_blank"`),
router in `app.py` on `pathname.endswith("/compare")`. Shows the old **MOJAVE
Paper XVIII** Gaussian fits (left) beside the current **clustering** fits
(right). Two panels, each a right-pane clone: a mode dropdown (Position /
Position Angle / Flux / Kinematics / Epoch overlay — **no Polarization**, XVIII
has none). Panel id prefixes `cmp-x` (XVIII) / `cmp-c` (clustering). Callbacks
registered unconditionally in `ui/compare_callbacks.py` (built per-prefix in a
loop; inert off-page).

- **Shared epoch axis.** ONE stepper (`cmp-epoch-slider` + ◀/▶ above both
  panels) drives both sides so they always show the same epoch. The master
  list is the **union** of both sides' epochs (`_master_epochs`; clustering
  names win); a side missing the selected epoch renders a **blank map**
  (clustering is the superset, so blanks fall on the XVIII side past ~2013).
  `cmp-active-epoch` publishes the decimal year → a per-panel **clientside
  vertical marker** (`_MARKER_JS`) on the summary epoch-axis views (Position /
  PA / Flux; none on Kinematics/overlay), mirroring the main page's marker.
- **Shared XY extent.** `_shared_extent` = union of both sides' cluster
  footprints, passed as `extent`/`extent_override` to both overlays (new param
  on `overlay_figure_for_epoch` / `build_overlay_figure` / `build_xviii_overlay`)
  so both panels frame identically. Per-panel `Reset view` stays independent.
- **Shared display controls** (`controls_bar`, above both panels — NOT per-panel):
  - `cmp-use-fits` — one FITS toggle drives BOTH overlays.
  - `cmp-lock-axes` — mirror zoom/pan between the two panels so they always show
    the same plotting area. Clientside `_SYNC_JS` (per source→target graph;
    overlay↔overlay + summary↔summary). **It reads the SOURCE graph's live
    `_fullLayout` axis ranges — NOT `relayoutData`** — because the overlay's
    equal-aspect letterbox (`equal_aspect.js`) fires a domain-only
    `Plotly.relayout` right after a zoom and Dash keeps only the LATEST
    relayout event, so a payload-based range filter sees nothing (this was the
    "lock doesn't work on overlays" bug). Only ranges are copied (domains left
    alone → each panel keeps its own letterbox). A global `window.__cmpAxisSync`
    guard (400 ms) + a per-axis near-equal check break the echo loop (the
    target's own relayout and the letterbox's follow-up domain event).
    `_SYNC_ENABLE_JS` mirrors left→right on toggle-on. Dummy `cmp-sync-*` stores
    are the outputs.
- **Contour base is 3σ everywhere.** `cbase_factor` defaults to **3.0**
  (`cbase = 3.0 × inoise`) in `build_overlay_figure` /
  `overlay_figure_for_epoch` / `build_xviii_overlay` — used by the main page and
  both compare panels. (Was 3.5; a per-page 3× toggle was tried and dropped as
  needless — 3.0 is close enough and now universal.)
- **Source list** (`compare.compare_source_options`): XVIII sources ∩ current
  Results ∩ **phase == `final`** (`store.source_phase`). One shared
  `cmp-source-picker` drives both panels.
- **XVIII → our schema** (`data/xviii.py`, `build_xviii_cluster_df`): parses the
  bundled MRT (`data/MOJAVE_XVIII_apjac230ft4_mrt.txt`, `--xviii-table`
  overrides) into the *exact* cluster_df columns `build_summary_figure` reads.
  `F`→`clusterID` (0=core), `I`/1000→`iflux`, `r,PA`→`avg_x/avg_y`
  (`x=r·sinPA, y=r·cosPA`), `MajAxis`→`fwhm_maj`, `MajAxis·Ratio`→`fwhm_min`,
  `MajPA`→`cpa/sizePA`, `f_F=='a'`→`use_in_fit=False`, `Robust?`→`robust`
  (per-feature, earliest non-blank). **Registration (MRT Note 3):** the core
  feature's `r,PA` is from the **map center** (= our `core_x/core_y`), non-core
  from the **core**. So `avg_x/avg_y` hold absolute map positions
  `X0 = (r0·sinPA0, r0·cosPA0)` for the core and `X0 + (rk·sinPAk, rk·cosPAk)`
  for feature k; `core_x/core_y = X0` (the *summary* frame — `avg−core` = the
  pure XVIII core-relative offset, core at 0). Each epoch is snapped to the
  nearest MOJAVE observation in the npz (shared background image). `pflux/evpa
  = NaN`.
- **XVIII overlay** (`plots/compare_overlay.build_xviii_overlay`): reuses
  `overlay.build_overlay_figure` with `cc_labels=None` — background = our clean
  components synthesized (default) or real FITS (`Use FITS` checkbox), our CCs
  drawn as faint grey context dots, **ellipses/labels from the XVIII df**. It
  **re-registers** by overriding the df's `core_x/core_y` with the fitted
  clustering core, so the Gaussians sit on the clustering-core-centered image
  at their true `X0−core` offset (the small XVIII-vs-clustering core-fit
  registration difference is preserved, not zeroed). The clustering side just
  calls `overlay.overlay_figure_for_epoch` unchanged. Both
  overlay graphs (`cmp-x-overlay-graph`, `cmp-c-overlay-graph`) are added to
  `equal_aspect.js` `GRAPH_IDS` and get the beam-reposition clientside callback
  (`_BEAM_JS` templated per graph id).

### Reverse-proxy path prefix

`--url-base-prefix /mojave-review/` (or `MOJAVE_REVIEW_URL_BASE_PREFIX`) makes
Dash serve under the prefix; every in-app link routes through `ui/urls.py`
`rel()` (= `dash.get_relative_path`, no-op at root). **Proxy must preserve the
prefix** (`proxy_pass …:<port>;` NO trailing slash). Root-path hosting needs
none. See `docs/deployment_phase2.md`.

## Running the web app

```bash
cd mojave_review && pip install -e .          # one-time
mojave-review --results-dir ../Results        # http://127.0.0.1:8050
```

Flags: `--reviewer "Name"`, `--port`, `--no-browser`, `--cache-dir`,
`--recommendations-dir`, `--admin` (Dashboard balancing + Stage-3), `--url-base-
prefix /mojave-review/`. For Drive data: use Google Drive for Desktop and point
`--results-dir` at the local mirror.

## Phase plan

1. **Phase 1** — pip-installable local app. ✓ viewer, overlay, recommendations.
   Keyboard-key parity (`n/b/i/a/u/r`) is the remaining optional polish.
2. **Phase 2** — host on the university server with per-user static tokens
   (~6 reviewers) from `tokens.yaml` (cookie / `?token=…`), per-user recs dirs.
   Full plan + IT/hosting requirements in `docs/deployment_phase2.md`. Google
   OAuth evaluated and rejected as overkill.

## Don't / gotchas

- **`robust` is a per-CLUSTER property** — keep it uniform across a cluster's
  epochs. A per-epoch-inconsistent flag is a latent bug (overlay flicker).
  `apply.apply_recommendation` enforces this (`_normalize_robust_per_cluster`,
  canonical = earliest-epoch, **core forced True**). Viewer collapses strays too
  (summary `iloc[0]`, `overlay.robust_by_cluster`). Legacy CSVs: read-only ⚠
  banner (`_robust_warning`); repair with `mojave-review-audit-robust
  --results-dir … [--apply]` (CSV-only, backs up + logs to `history.txt`).
- **Don't write back into `Results/`.** Recommendations are the only output.
- **`grab_mojave_image` opens local files, not URLs.** The web app has its own
  fetcher; don't reuse that function.
- **`.plotdata.npz` uses `allow_pickle=True`.** CSV is the authoritative
  cluster-level table; npz is needed only for the FITS overlay.
- **A backup CSV has no matching npz** → loader returns `plotdata=None`; overlay
  must handle it gracefully.
- **`autorange="reversed"` + explicit `range=[hi, lo]` fight.** Use explicit
  reversed ranges so `scaleanchor` composes.
- **Don't smooth the FITS image before contouring** (already beam-convolved).
  Use `line.smoothing` on the contour if needed.
- **Clientside callbacks that "patch" a figure must call `Plotly.restyle/
  relayout` directly and return `no_update`**, not a modified figure — returning
  a figure resets `uirevision` zoom. (Beam + active-epoch callbacks do this.)
- **Dash `assets_folder` defaults to `./assets` relative to CWD** — `app.py`
  sets it to the package dir so `pip install` ships the JS/CSS.
- **`*-open` markers use `marker.color` as the outline** — transparent color
  erases them. Use a real color + SVG `go.Scatter` (not `Scattergl`) for
  thin-outline overlays.
- **`clickData` doesn't change between identical clicks** → callbacks no-op.
  The click-toggle callback outputs `clickData=None` at the end; guard the
  re-fire with `if not click_data: return no_update`.
- **`allow_duplicate=True` callbacks get hashed output keys** — curl smoke tests
  need the hash (from `app.callback_map`); browsers route automatically.
- **Recommendations against non-`current` models are not permitted** (backups /
  other-reviewer views are read-only; panel locks, autosave skips).
- **`dcc.Dropdown` option labels must be `string | number`, never a component.**
  A component emits a dict → **React error #31**, which Dash's error boundary
  catches by **blanking the surrounding render pass** (so an unrelated panel
  silently shows nothing). Convey status in label text + use the `search` field.
- **Multi-output callbacks need `allow_duplicate=True` on EVERY duplicated
  output.** Missing one passes server registration but the browser rejects it
  with "Duplicate callback outputs", killing the ENTIRE client callback graph
  for that mode — invisible to curl/`test_client` (they bypass the client
  graph).
- **Readers of `submitted/` fire off `submit-trigger`** alongside `_do_submit`
  (which writes it) with no ordering guarantee — a reader can miss the user's
  own just-submitted rec until the next ↻ Reload. A post-write signal store was
  tried and reverted; if re-attempting, verify it doesn't break the admin
  callback graph.
- **Overlay epoch-stepping (RESOLVED, do not regress):** `assets/keyboard.js`
  must listen in the **capture phase** and `stopImmediatePropagation()` before
  clicking `#epoch-prev/next` — otherwise a keypress with the `dcc.Slider`
  handle focused fires BOTH the slider's native step AND the button (+2 epochs;
  self-heals after a button click moves focus off the handle). **Do NOT add
  `uid`s to the contour trace** — a `uid` + plotly 6's base64 `z` makes
  `Plotly.react` skip the contour redraw, freezing the image. (Both were real
  bugs; the `uid` attempt was a reverted dead end.)

## Useful local commands

```bash
# Audit (and repair) per-epoch robust inconsistencies in saved CSVs
mojave-review-audit-robust --results-dir ./Results [--apply]

# Per-source difficulty scores + star distribution
mojave-review-difficulty --results-dir ./Results

# Smoke test the loader + figure
python3 -c "
from pathlib import Path
from mojave_review.data.loader import list_sources, load_bundle
from mojave_review.plots.summary import build_summary_figure
b = load_bundle(str(list_sources(Path('Results'))[0].folder), 'current')
print(len(build_summary_figure(b.cluster_df, view='Position').data), 'traces')
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
