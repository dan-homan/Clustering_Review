# MOJAVE Cluster Review — Changelog

Changes are grouped by feature area, newest first.

---

## 2026-07-22 — Position hover shows fitted speed

- **Distance-vs-epoch tooltips now report the fitted proper motion.** Hovering a
  point in the Position view's top panel appends the cluster's fitted speed in
  `mas/yr` (± 1σ) and, when the source redshift is known, the apparent speed in
  units of `c` (β_app ± 1σ) — the same information the Kinematics speed-vs-
  distance plot already shows. Shared `_beta_str` / `_motion_hover_extra`
  helpers in `plots/summary.py`; the β line now carries its propagated 1σ error
  in the Kinematics hovers too.

---

## 2026-07-22 — XVIII comparison page: shared controls + point markers

- **Point-like fits render as a bold `+`.** Any component whose size
  (`√(fwhm_maj·fwhm_min)`) is 0 or below 0.05 mas now draws a bold `+` in the
  cluster colour at its location instead of an invisible collapsed ellipse
  (`plots/overlay.POINT_SIZE_MAS`). Applies to every overlay; XVIII Gaussian
  fits are occasionally exactly point-like.
- **Shared FITS toggle.** The per-panel "Use FITS" checkboxes are replaced by a
  single `cmp-use-fits` above both panels, so the choice drives both sides at
  once.
- **Contour base set to 3σ everywhere.** `cbase_factor` default is now 3.0
  (`cbase = 3.0 × inoise`) in every overlay path — main page and both compare
  panels. Close enough to the previous 3.5×, so the short-lived per-page 3×
  toggle was removed.
- **Lock display areas.** New `cmp-lock-axes` mirrors zoom/pan between the two
  panels (clientside), so a change to one produces the same change on the other
  and they always frame the same plotting area. Ranges are synced by reading the
  source graph's live `_fullLayout` (not the `relayoutData` payload, which the
  equal-aspect letterbox clobbers with a domain-only event) — the fix for the
  lock not working on the Epoch overlay panels. Domains are left per-panel.

## 2026-07-21 — XVIII comparison page

- **New `/compare` page** (read-only, header link on the review page): the old
  MOJAVE Paper XVIII Gaussian fits side-by-side with the current clustering
  fits. Two panels, each a clone of the main page's right pane — a view
  selector (Position / Position Angle / Flux / Kinematics / Epoch overlay; no
  Polarization). Sources offered = present in both datasets **and** finalized
  (Stage 3 done).
- Both panels share **one epoch stepper** (same epoch shown on both sides; a
  side lacking that epoch shows a blank map) and an **identical XY zoom box**.
  The shared epoch draws a vertical marker on each side's summary epoch-axis
  views.
- The XVIII table (`MOJAVE_XVIII_apjac230ft4_mrt.txt`) is bundled with the
  package and parsed into our cluster-DataFrame schema (`data/xviii.py`), so the
  existing summary + overlay renderers work unchanged. `--xviii-table` overrides
  the bundled copy.
- The XVIII overlay reuses the same clean-component (or FITS) background image
  as the clustering side, with the XVIII Gaussian FWHM ellipses drawn on top and
  the clean components shown as faint grey context dots.
- **Active-epoch vertical marker**: added to the main page's right-hand summary
  pane (it already existed on the left pane) for the epoch-axis views.

---

## 2026-07-09 — Admin workflow polish

- **Auto-balance**: crediting prior completed reviews is now opt-in (unchecked
  by default). Normal balancing schedules on current pending load only; the
  "Consider completed reviews" checkbox restores the old first-round behaviour.
- **Admin source picker ordering**: triage groups are now — needs discussion
  (‼), ready to aggregate (★, open + ≥ 2 reviews), **stage 1 / stage 2**
  (baseline work), open needs-review sources, finalized. Stage 1/2 sources
  now appear ahead of the needs-review set so admins see in-progress baselines
  without scrolling past all the open sources.
- **Stage 2 done → target date**: clicking "Save & set Stage 2 done" now
  automatically sets the source's reviewer target date to today + 14 days
  (the standard two-week review window), saved into `assignments.json`.
- **Window-N overlay equal aspect**: the admin Window-N review overlay now
  correctly preserves equal X/Y scale on zoom/pan, matching the behaviour of
  the standard epoch overlay. The `equal_aspect.js` letterbox was not wired
  to `nwin-overlay-graph`; fixed.

---

## 2026-07-06 — Source picker triage ordering (admin)

- Admin source picker previously listed all non-flagged sources
  alphabetically. Now sorted into triage groups: needs discussion (‼), ready
  to aggregate (★), everything else — each group alphabetical.

---

## 2026-07-03 — Summary plot colour + auto-balance checkbox

- Unassigned / non-robust cluster traces changed from gray to **darkorange**
  so they are clearly distinct from robust clusters at a glance.
- Dashboard **"Only unassigned sources"** checkbox added to the auto-balance
  modal: fills slots only on sources no reviewer is currently slated to
  review, while still counting all existing assignments as load.

---

## 2026-07-01 — Letterbox equal-aspect zoom (overlay + summary XY)

- **Epoch overlay**: replaced `scaleanchor` (which locks the zoom box to a
  fixed aspect) with a **letterbox** mechanism (`equal_aspect.js`). Equal
  mas/pixel is re-imposed after every draw or zoom by narrowing an axis
  domain, so the plot box gets padding on its short side rather than
  constraining the drag shape. Free-form zoom now works on the overlay.
- **Summary Position XY panel**: same treatment — the bottom centroid-track
  subplot drops `scaleanchor` and gains the horizontal-only letterbox (the
  vertical split is owned by the resizable divider).
- **Self-healing**: a bad domain (e.g. from a Plotly double-click reset) can
  never stick — the script recomputes on both `plotly_afterplot` and
  `plotly_relayout`, and the re-entrancy guard is bulletproofed so it can
  never latch on.
- **Reset view** buttons added to both the overlay and the summary panel;
  these bump the `uirevision` counter, the only way to flush a stuck domain.

---

## 2026-07-01 — Split-pane view selectors

- Left and right summary panes now each have a **dropdown view selector**
  where the panel title used to be. The right pane can show any summary view
  (Position, PA, Flux, Polarization, Kinematics) or the epoch overlay, for
  side-by-side comparisons.
- Right pane is **read-only for selection** — highlights follow along, but
  clicks don't originate edits.
- PA view restored as an independent panel; XY centroid track moved to the
  bottom of the Position view.

---

## 2026-06-30 — Overlay: MOJAVE montage link

- Each epoch's header in the overlay panel now links to its MOJAVE archive
  montage page (opens in a new tab), for quick reference to the full-quality
  published image.

---

## 2026-06-28 — Robustness tab UX fix

- Robust/Non-robust controls are now **inline radio buttons** inside the
  Robustness tab, replacing a dropdown that rendered off-screen inside the
  scrollable panel. Comment fields are plain text inputs.

---

## 2026-06-27 — Dashboard bug fixes

- Fixed dashboard admin apply actions (auto-balance, rebalance, redistribute)
  that were visually appearing unsaved, reloading endlessly, or firing
  spuriously on render. Apply now refreshes the page in place.

---

## 2026-06-25 — Server deployment (Phase 2)

- **Per-user static token auth**: `mojave-review-tokens` CLI (`add`,
  `rotate`, `revoke`, `list`, `show`, `url`). Tokens live in `tokens.yaml`;
  a reviewer hits a `?token=…` URL once and gets a long-lived cookie.
- **Config file**: `config.yaml` / `MOJAVE_REVIEW_*` env vars for all paths
  and settings, so production deployments need no CLI flags.
- **WSGI entry point** (`mojave_review.wsgi`) for gunicorn; systemd unit
  template included.
- **Rotating-file logging** with auth audit trail.
- **Reverse-proxy path-prefix** support: `--url-base-prefix /mojave-review/`
  makes every in-app link prefix-aware.
- **Manual rsync** workflow for syncing `Results/` from laptop to server.
- Deployment runbook in `deploy/README.md`.

---

## 2026-06-20 — Assignment Dashboard

A new **Assignment Dashboard** (`/dashboard`) page tracks reviewer workload
and lets the admin manage assignments.

- **Difficulty scoring**: each source gets a score (`N_epochs × mean features
  per epoch`) mapped to a ★–★★★★★ rating; balancing weight = √score.
- **Auto-balance** (LPT scheduler): fills open reviewer slots on Stage-2-done
  sources, with preview before apply.
- **Top-up rebalance**: moves pending assignments to even out load; optional
  "Consider completed reviews" weighting.
- **Redistribute**: spread one reviewer's pending queue across the pool
  (break coverage).
- **Move / Reassign / Set target dates / Manage team / Pause reviewers**.
- **Stat tiles**: team-wide and per-reviewer counts (total, finalized, ready
  for completion, ready for review, Stage 1/2).
- **My queue** and **Sample Status** tables with pagination.
- **Reviewer summary** (admin): expandable per-reviewer queue + lifetime
  completed count.
- Source names are deep-links that select the source in the main picker.
- Assignments stored in `recommendations/_admin/assignments.json` (syncs with
  the rest of `recommendations/`).

---

## 2026-06-15 — Stage 3 aggregation

- **Admin aggregation panel**: reconciles all reviewers' submitted
  recommendations for a source into one model. Robustness decisions, ID/use-
  in-fit edits, and reviewer comments shown side-by-side.
- **Live preview**: "Preview aggregated" checkbox applies the composed
  recommendation to both summary panes in real time.
- **Apply**: writes `stage3/aggregated.json` + sidecar meta, generates a
  copy-paste `mojave-apply --stage3-meta` command. The app never shells out
  directly.
- **No-changes finalize**: bookkeeping fully in-app when there are no model
  changes (no `Results/` mutation).
- **Needs Discussion** flag: marks a source for further discussion without
  finalizing; visible to all reviewers.
- **Repeat applies**: every Stage-3 apply appends a new ledger entry and
  backup; run index tracked.
- **Dated note box** with "Seed from submissions" and "Add dated note to log".

---

## 2026-06-12 — Stage 2 editor (admin)

- Admin can edit the Stage 2 notes section directly in the app.
- Two save buttons: **Save Stage 2 notes** (marks in-progress) and **Save &
  set Stage 2 done** (opens source for reviewer recommendations).
- "Seed from submission summary" pre-fills the editor from the admin's own
  submitted recommendation.

---

## 2026-06-10 — Source notes panel

- Read-only in-app view of per-source lab notebook (`notes/<source>.md`).
- Notes seeded from a Google Docs Markdown export of the Step-1 review
  spreadsheet; `mojave-review-notes seed` CLI.
- Stage 2 notes section imported automatically.

---

## 2026-06-07 — Summary + overlay enhancements

- **Kinematics view**: zero-anchored speed/distance axes, median-based vector
  scaling, `beta_app` on hover (requires `source_run_param.csv` redshift).
- **Per-source redshift**: `source_run_param.csv` → host-frame Tb `(1+z)` and
  apparent speed `β_app` on Kinematics hovers.
- **Active-epoch marker**: thin vertical line on epoch-axis subplots links the
  overlay's current epoch to the summary plots while scrubbing; clientside
  (no server round-trip, preserves zoom).
- **Source picker badges**: `[N]` / `[stage 1]` / `[stage 2]` / `[final]`;
  stage locks recommendations for non-admin reviewers.
- **Log axes** on Flux / Tb / P-flux panels; epoch-name on hover.
- **Subplot badges** replace panel titles (tracked to axis domain so they
  survive the resizable divider).
- **Resizable left-pane divider**: drag to rebalance the two summary subplots.
- **Stacked overlay image**: accumulate all epochs' CCs on a common grid,
  convolve with the median beam; toggled by a header checkbox.
- **Synthesis-first overlay**: single-epoch CC synthesis is now the default
  (no FITS fetch needed); "Use FITS images" checkbox falls back to real CLEAN
  FITS.
- `mojave-apply` conditional plot regeneration (PDF/MP4 opt-in).

---

## 2026-06-03 — Window-N review panel (admin)

- Web replacement for `find_clusters.py --editN`. Browses cached per-window
  cluster fits (`cluster_fits/*.npz`) without running new clustering.
- **BIC\* vs N** curve + **N-per-window strip chart** (click to jump).
- **Epoch overlay** for each (window, N, epoch) using the standard overlay
  renderer.
- **Fixed source-wide zoom** covering all candidate clusters of all windows.
- **Keyboard shortcuts**: ←/→ step windows, ↑/↓ step N, `r` records choice.
- **Draggable divider** between BIC* chart and overlay.
- **Per-window comments** saved alongside N choices.
- **"Generate rerun command"** builds the `find_clusters.py --N_win_file`
  command from `run_string.txt`.
- Choices saved to `recommendations/<source>/nwin_edits/nwin_choices.json`.

---

## 2026-05-31 — Robust flag consistency

- `robust` is now enforced as a **per-cluster** property: `mojave-apply`
  normalises any per-epoch inconsistencies (canonical = earliest epoch; core
  always forced True). `mojave-review-audit-robust` CLI audits and optionally
  repairs existing CSVs.
- Overlay collapses per-epoch robust flags to the earliest epoch so cluster
  styling doesn't flicker while scrubbing.

---

## 2026-05-30 — Reset Recommendation dialog

- Button beside Submit (current model only) opens a 3-choice modal: **Reset
  to last submitted**, **Delete draft & submitted**, or **Cancel**.

---

## 2026-05-29 — Multi-reviewer view + `Rec:` dropdown

- Model dropdown now includes a `Rec: <slug>` entry per other reviewer's
  **submitted** JSON. Selecting it shows their recommendation applied to the
  current model (read-only).
- Visualize-recommendations checkbox gated per model: on by default for
  `current`; forced on for `Rec:` entries; disabled for backups.

---

## 2026-05-28 — Initial release

- Pip-installable Dash web app (`mojave-review`) loading clustering pipeline
  results from `Results/` without re-running clustering.
- **Summary plots**: Position, PA, Flux, Polarization, Kinematics views
  (Plotly port of `make_summary_plots`).
- **Epoch overlay**: FITS or synthesized Stokes-I contours + clean components
  coloured by cluster + FWHM ellipses + beam.
- **Recommendations panel** (4 tabs): Robustness, ID/use-in-fit Edits, Source
  Notes, Epoch Notes. Auto-saves on every change.
- **Selection-driven edits**: click or box/lasso on summary points to select,
  then renumber cluster IDs or toggle `use_in_fit`.
- **`mojave-apply`** CLI: applies a reviewer's JSON recommendation to a source
  CSV, backs up the prior model, optionally regenerates plots.
- Backup and alt-model support: all prior models browseable in the dropdown.
