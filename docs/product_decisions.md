# Product and architecture decisions

The `mojave-review` web app — a Plotly Dash tool that lets a small trusted
group of astronomers review the project's MOJAVE clustering models and
submit structured recommendations. Lives in `mojave_review/` (subdirectory
of the clustering repo, not a separate repo).

## Locked decisions

- **Phase 1** = pip-installable local app, no auth, single reviewer per
  launch. Reviewers run `mojave-review --results-dir ...` on their own
  machine.
- **Phase 2** = host on the user's university web server with **per-user
  static tokens** for authorization (small trusted group, ~6
  reviewers). Same codebase; deploy differences are reviewer identity
  (from the token in the cookie) and per-user recommendations dirs.
  The full plan including IT talking points is in
  [`deployment_phase2.md`](deployment_phase2.md). Google OAuth was
  evaluated but rejected as overkill for the group size; IP-based
  allowlist was rejected because home/office IP churn would create
  ongoing maintenance.
- **Plotly Dash** chosen over Streamlit / FastAPI+React. Reasons: purpose-
  built for interactive Plotly UIs; single Python codebase reuses
  `cluster_code.py` data prep; manageable maintenance burden.
- **Drive sync** in phase 1 is handled by Google Drive for Desktop —
  reviewers mirror the shared `Results/` folder locally and point the app
  at it. No in-app Drive auth required.
- **FITS images** are fetched live from the MOJAVE NRAO archive (see
  [references.md](references.md)) and cached locally under
  `~/.mojave_review/cache/...`.
- **Recommendations never modify on-disk results.** The app writes
  per-(source, reviewer) JSON files to a `recommendations/` directory.
  The author downloads a merged report and processes it manually.
- **`use_in_fit` toggle scopes:** `single` (one point) and `epoch` (whole
  epoch). Exactly these two — no "cluster within epoch" scope.
- **`change_clusterID` scopes:** `single` (one row) and `all_epochs`
  (renumber a cluster across the whole source). Mirrors the `i` and `a`
  keys in `cluster_code.py`'s interactive review.

## Phase 2 hosting requirements (summary)

Full plan + IT talking points in
[`deployment_phase2.md`](deployment_phase2.md). The short list:

- Python ≥ 3.10 in user space (`pyenv` / `uv` OK).
- Long-lived process bound to a local port (`gunicorn` + systemd or
  supervisord).
- Reverse-proxy line under the existing nginx/Apache mapping
  `https://<host>/mojave-review/` → `http://127.0.0.1:<port>/`. WebSocket
  upgrade headers ideal but not required.
- Writable persistent directory (~50 GB) for FITS cache and
  recommendations. Nightly backup of just the `recommendations/`
  subdir.
- Outbound HTTPS allowed to `www.cv.nrao.edu` (for FITS fetches) —
  **no Google endpoints needed**.

No database. No special system packages. No OAuth setup. Authorization
is per-user static tokens read from a `tokens.yaml` config file with
hot-reload on disk change.

## Status as of 2026-05-28

Phase 1 is **feature-complete**. The application is suitable for daily use
by a small group of reviewers. Live-tested end-to-end on the example
sources.

- Package + CLI install (`mojave-review`) ✓
- Source / model discovery (current + backups + multi-reviewer) ✓
- CSV + NPZ load ✓
- Plotly summary plots — Position / Flux / Polarization / Kinematics ✓
- Kinematics view: arrowheads, +x left, equal mas/pixel + free zoom,
  core marker, user-tunable vector scale ✓
- FITS cache + epoch overlay panel ✓
  - Live fetch from `cv.nrao.edu` with on-disk caching
  - Contour image (no smoothing — beam-convolved already)
  - Per-cluster FWHM ellipse (filled) + 3σ inclusion outline (dotted, fill at 0.04)
  - Black cluster-number labels, core marked with `×`
  - Beam ellipse: clientside-tracked so it stays in the visible corner on zoom
  - Equal mas/pixel + free-form zoom rectangle (scaleanchor + constrain="domain")
  - Initial range from `compute_source_extent` (cluster footprint)
- Draggable splitter between summary and overlay panels ✓
- `uirevision`-preserved zoom across epoch / source / model changes ✓
- Recommendations panel (4 tabs: Robustness / ID-use-in-fit Edits /
  Source Notes / Epoch Notes) ✓
- Selection-driven edit generation on click + box/lasso, gold halo
  highlight, comment field per action ✓
- "No changes suggested" Robustness checkbox + cluster-0 dropdown lock ✓
- "Visualize recommendations" checkbox: apply your in-progress recs to the
  plots before they render ✓
- Multi-reviewer view: other reviewers' JSON files appear in the model
  dropdown as `Rec: <slug>`; selecting one shows their recommendations
  applied to the plots, with the recommendations panel locked read-only ✓
- Recommendations only writable against `current`; backup_NNN and Rec:
  models lock the panel to read-only ✓

## Agreed next-chunk order (do not widen scope)

1. ~~FITS cache + epoch overlay panel.~~ Done.
2. ~~Recommendations sidebar + JSON store.~~ Done.
3. ~~Reference PDF / MP4 tab on the overlay side.~~ **Dropped** —
   reviewer decided this isn't needed.
4. Keyboard shortcuts (mirror the matplotlib keys `n / b / i / a / u / r`).
   Optional polish; phase 1 is usable without it.

When that's done (or skipped), the codebase is ready to move to Phase 2
(university web server deploy + per-user static tokens) — see
[`deployment_phase2.md`](deployment_phase2.md).
