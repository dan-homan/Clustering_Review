# MOJAVE Cluster Review — Changelog

Changes are grouped by feature area, newest first.

---

## 2026-10-11 — Window-N rerun command skips `update_cluster_df.py` run strings

- **The bug.** `build_rerun_command` read `Results/<source>/run_string.txt`
  verbatim and assumed it was a `find_clusters.py` command. The new
  `src/update_cluster_df.py` (rebuilds the merged CSV from the cached fits
  without re-fitting) overwrites that file with its **own** command line, so
  after running it the Window-N panel drafted a rerun built from
  `update_cluster_df.py` flags — a command that can't do the rerun.
- **The fix.** New `window_fits.find_clusters_run_string(folder)` returns the
  most recent genuine `find_clusters.py` run string as `(text, path)`:
  `run_string.txt` when it is one, else the highest-numbered
  `backups/backup_NNN_run_string.txt` that is (`update_cluster_df.py` copies the
  prior run string there before overwriting, and backup numbers increase with
  time, so the first hit scanning down is the most recent). Detection matches
  the script **basename**, like `src/rerun.py`, so `../src/find_clusters.py`
  and absolute paths count. `build_rerun_command` starts from that; everything
  downstream (flag stripping, `--recalc_IDs`, `--N_win_file`) is unchanged.
- **The drafted command says so.** When the run string came from a backup, a
  comment line names it — the command predates the saved CSV, and the reviewer
  should see that rather than wonder why it looks stale.
- **`src/rerun.py` has the same exposure** (it `sys.exit`s with "does not look
  like a find_clusters.py command"), left alone — that's pipeline code and it
  fails loudly rather than silently.

---

## 2026-09-18 — "Hide uncertain motions" actually hides them

- **The checkbox no longer keeps marginal slow motions.** The gate behind
  `_MotionFit.significant` was ported verbatim from `cluster_code.py`:
  `SNR ≥ 3 OR (speed < 0.05 AND sqrt(var_vx+var_vy) < 0.05)`. The second clause
  is there so genuinely stationary clusters survive — a non-moving cluster has
  a low SNR by construction — but it cuts on the best-fit speed and the slope
  errors *separately*, in absolute terms, so it also admitted marginal
  detections. On `2022+171u` all three fits cleared it (cluster 1 at 2.2σ,
  cluster 2 at 1.5σ) and ticking the box produced a byte-identical figure,
  which read as a broken checkbox.
- **Slow now means *confidently* slow**: `speed + 3·speed_err <
  SLOW_SPEED_MAS_YR` (0.05 mas/yr), i.e. the motion has to clear the threshold
  at 3σ, not merely in its best-fit value. The ≥3σ branch is unchanged, and so
  is the default (unticked) behaviour of drawing every qualifying fit.
- **Effect.** `2022+171u` hides cluster 1 and keeps the core and cluster 2
  (0.010 ± 0.0065 mas/yr — a confident non-detection). Across all 3306 motion
  fits in `MOJAVE_1996.00-2026.00`, 273 (8.3%) newly hide when the box is
  ticked; every one is sub-3σ *and* sub-0.05 mas/yr.
- **The app is now deliberately stricter than the pipeline.** The reference
  `summary_plots.pdf` renders under `Results/` still show those marginal
  motions; the equivalent change in `cluster_code.py` is the gate at its line
  3038, left alone by request.

---

## 2026-09-16 — Recommendations panel: robustness picks no longer lost on load

- **Switching source could silently wipe a reviewer's non-robust picks.** The
  Robustness radios reach the rest of the panel through a `dcc.Store`
  (`cluster-feedback-table`) filled by a separate bridge callback. Dash's
  `getReadyCallbacks` blocks a callback only on pending **Input** edges — State
  is never waited on — so a callback reading that store dispatches in the *same*
  batch as the bridge and sees the previous source's rows, or nothing at all on
  a fresh page load. `_load_for_source` publishes the rendered rows and
  `edits-store` in one response, so on any source with staged edits the resync
  fired against the stale store, rebuilt every radio at the model's current
  status, and autosave wrote that wiped panel over `current/<slug>.json`. The
  picks vanished; comments and edits survived (production: `1828+487u`,
  `1514-241u`).
- **The stores are now triggers, never a data source.** `_resync_table_with_
  effective` and `_autosave` rebuild their rows from the live components
  (`_cluster_rows_from_components`), the dodge `_do_submit` already used. The
  stores stay as Inputs so a radio click or blurred comment still fires a save,
  but their payloads are ignored — which also fixes a latent second case where a
  source switch saved the previous source's feedback under the new source's
  name. Wiring pinned by `tests/test_rec_panel_state_sources.py`.

---

## 2026-08-31 — Auto-balance prunes work on finalized sources

- **Nobody is left reviewing a source whose Stage 3 is already applied.** Every
  auto-balance now drops queue entries on a **finalized** source the reviewer
  never submitted — pending *and* unsubmitted `current/` drafts (the draft file
  itself is untouched; only the queue entry goes). No opt-out, and every removal
  is listed in the preview modal before Apply commits it. Submitted records stay
  (they credit completed work), as do `manual_reviews` credits.
- **Pure planner + committer**: `plan_finalized_prune` returns the
  `(reviewer, source)` removals, `apply_removals` commits them via the existing
  `remove_assignment`. The prune mutates the in-memory store before additions
  are planned, so the balancer schedules against post-prune queues; Apply
  re-plans from the freshly-loaded store, so a review submitted between preview
  and apply is never pruned.

---

## 2026-08-05 — Comment audit: in-flight vs lost, and a Stage-2 warning

- **Open ≠ lost.** The first production sweep flagged 263 comments, but 155 were
  open submissions still awaiting Stage 3 — in flight, not lost. `SourceAudit`
  now splits `missing` (from an archived `considered/` or `applied/` submission,
  which nothing in the app surfaces, so an unrecorded one is gone) from
  `open_only` (a live `submitted/` rec, rendered under "Open suggestions" and
  destined for the Stage-3 ledger entry). The CLI lists only the lost ones and
  summarizes the rest; `--include-open` lists them; `--apply` never writes an
  in-flight comment. Late submissions on a `final` source are ordinary open work.
- **The real gap was Stage 2.** All 46 archived-but-unrecorded comments in
  production were the builder's own: the Stage-2 apply writes no ledger entry
  and the Stage-2 prose is hand-written, so skipping "Seed from submission
  summary" leaves them only in `applied/<date>__<slug>.json`. **"Save & set
  Stage 2 done" now warns** with the specific comments and an **"Add them to
  Stage 2 notes"** button (`notes.audit.builder_comments_missing`) that appends
  them in the canonical bullet form. Non-blocking — the save has already
  happened. Chosen over auto-appending a Stage-2 ledger entry, which would break
  the ledger-is-Stage-3-only invariant.
- **Phantom sources.** `_source_dirs` skipped `_`-prefixed directories but not
  dot-dirs, so four `.unison.*.tmp` sync-scratch directories were enumerated as
  sources, polluting the audit and reviewer stats. Their contents were stale
  copies of already-considered submissions, so nothing was at risk.
- **Post-split production sweep**: 108 lost across 64 sources (62 Stage-3
  `considered/`, 46 Stage-2 `applied/`), 151 in flight.

---

## 2026-08-05 — Stage 3 always records reviewer comments

- **The usual way a round ends was dropping every reviewer comment.**
  `stage3_ledger_entry` took no comments at all, so the apply-with-decisions
  path wrote none into `notes/<source>.md` — and applying also moves
  `submitted/*.json` into `considered/<date>/`, so the prose left the app
  entirely. The other two Stage-3 endings (finalize-with-no-changes, Needs
  Discussion) did fold them in, which is why it looked inconsistent.
  `reviewer_comments_block` is now shared by every Stage-3 ledger renderer and
  all three endings pass the round's comments, so a future path can't drop them.
- **One extraction, one rendering.** `notes.render` grows `CommentEntry` /
  `comment_entries`, used by every path *and* by the audit tool, so they always
  agree on what "recorded" means. Only `submitted/` counts; unsubmitted
  `current/` drafts stay private.
- **No duplicates.** A comment already in the notes isn't repeated
  (`unrecorded_entries`), so a dated note the admin already pasted is left
  alone. Matching is on the comment **prose**, not our bullet format — the
  Stage-2 section quotes it as `Cluster 3: …` prose, a dated note as
  `- (rv) cl 3: …`. Short comments (< 12 non-space chars) additionally need
  their cluster/epoch identifier on the same line, since "OK" alone proves
  nothing; a *reworded* comment is recorded again, since preserving the
  reviewer's own words is the point.
- **New `mojave-review-audit-notes`** recovers what the old path lost: rebuilds
  the comment set from every submission on disk (`submitted/`,
  `considered/<date>/`, Stage-2 `applied/<date>__<slug>.json`; `__aggregated`
  skipped — those are admin decisions, not reviewer prose) and reports each
  comment whose text is nowhere in the notes. `--apply` appends a dated
  "Recovered reviewer comments" entry, origin as a **suffix** so re-runs
  recognize it (idempotent). Against the live production mirror: 30 raw hits, 28
  already recorded as Stage-2 prose (the reason matching is prose-level), 2
  genuine.

---

## 2026-08-05 — XY panel: active-epoch squares + epoch-range filter

- **The XY centroid track now shows which points are the current epoch.** The
  Position view's bottom panel has no epoch axis to carry the vertical epoch
  line, so the track points at the Epoch overlay's epoch get a cyan open square
  instead. Filled into an empty placeholder trace; the epoch arrives from the
  existing clientside epoch-line callbacks via `window.mojaveXY.setEpoch`.
- **Zooming the distance panel in time thins the XY plot.** Points outside the
  epoch range visible in the distance-vs-epoch panel above are hidden — on busy
  sources (`0415+379u`: 389 tracks / 1481 points) every epoch at once is
  unreadable. No UI toggle and no opt-out: unzoomed, the top panel autoranges
  over every point, so it's a no-op until the reviewer zooms. Filters on **x
  (epoch) only**, so a distance zoom hides nothing, and the XY axis ranges still
  span every point so the frame is fixed and survivors don't move (reviewer
  decisions).
- **Both are `Plotly.restyle` on the drawn figure, never a server rebuild** —
  cheap enough to scrub epochs with, and `uirevision`/zoom plus the letterboxed
  domains survive. Each XY trace carries `meta = {xy, ep, x0, y0}` (the drawn
  arrays are lossy once masked), which keeps `assets/xy_epoch.js` stateless: a
  rebuild ships fresh originals, so there's no cache to invalidate. Applies to
  both main panes and both `/compare` Position panels.
- **Two hazards found against real Plotly**, both now in `CLAUDE.md`: a pass
  arriving during our own restyle was dropped (stale mask after a reset — now
  remembered and re-run), and comparing against `gd.data` instead of
  `gd._fullData` made every trace look changed after each rebuild (plotly 6
  leaves arrays base64-encoded in `gd.data`), costing a full pointless redraw.

---

## 2026-07-22 — Compare page: consistent vector frame + zoom across epochs

- **Kinematics vector panels now share a frame, not just a scale.** Sharing only
  the arrow scale (previous entry) wasn't enough: each panel still autoranged to
  its own footprint, so equal speeds rendered at different on-screen lengths
  (and a large shared scale could overflow a small panel). Both panels are now
  framed to the same shared box (`build_summary_figure(kin_extent=)` =
  `_shared_extent`), so a vector of a given length means the same speed on both
  sides — always, with or without the display-lock.
- **Locked zoom survives blank epochs.** Stepping through an epoch where one side
  has no data (XVIII goes blank past ~2013) used to reset the zoom, because the
  blank map had no `uirevision`. `_blank` / `_empty_overlay` now carry the
  panel's overlay `uirevision`, so a locked, zoomed-in view stays put as you
  step from epoch to epoch.

---

## 2026-07-22 — Compare page: shared Kinematics vector scale

- **Both compare panels now use one velocity-vector scale.** A vector of a
  given length means the same speed on the XVIII and clustering sides — the two
  panels previously auto-scaled independently (off each side's own median
  speed), so identical motions drew at different lengths, which was confusing.
  The shared absolute scale is computed over BOTH sides' motion fits
  (`summary.kinematics_vector_stats` + `shared_vector_scale_abs`) and passed as
  `build_summary_figure(vector_scale_abs=)`. New `vector_scale_abs` param fixes
  the base arrow scale (mas per mas/yr) instead of per-panel auto-fitting; the
  per-panel scale slider still multiplies on top.

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
