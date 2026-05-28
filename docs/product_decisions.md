# Product and architecture decisions

The `mojave-review` web app — a Plotly Dash tool that lets a small trusted
group of astronomers review the project's MOJAVE clustering models and
submit structured recommendations. Lives in `mojave_review/` (subdirectory
of the clustering repo, not a separate repo).

## Locked decisions

- **Phase 1** = pip-installable local app, no auth, single reviewer per
  launch. Reviewers run `mojave-review --results-dir ...` on their own
  machine.
- **Phase 2** = host on the user's university web server with Google
  OAuth + email allowlist (small trusted group). Same codebase; deploy
  differences are reviewer identity (from login) and per-user
  recommendations dirs.
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

## Phase 2 hosting requirements (for the IT conversation)

- Python ≥ 3.10 in user space (`pyenv` / `uv` OK).
- Long-lived process bound to a local port (`gunicorn` + systemd or
  supervisord).
- Reverse-proxy line under the existing nginx/Apache mapping
  `https://<host>/mojave-review/` → `http://127.0.0.1:<port>/`. WebSocket
  upgrade headers ideal but not required.
- Writable persistent directory (~10 GB) for FITS cache and
  recommendations. Nightly backup desirable.
- Outbound HTTPS allowed to `www.cv.nrao.edu` (for FITS fetches).
- One OAuth callback URL registered if/when Google login is added.

No database. No special system packages.

## Status as of 2026-05-28

Phase 1 skeleton landed and live-tested end-to-end on the example sources:

- Package + CLI install (`mojave-review`) ✓
- Source / model discovery (current + backups) ✓
- CSV + NPZ load ✓
- Plotly summary plots — Position / Flux / Polarization / Kinematics ✓
- Kinematics view: arrowheads, +x left, equal aspect, core marker,
  user-tunable vector scale ✓

Right panel (FITS overlay) is still a placeholder.

## Agreed next-chunk order (do not widen scope)

1. FITS cache + epoch overlay panel.
2. Recommendations sidebar + JSON store.
3. Reference PDF / MP4 tab on the overlay side.
4. Keyboard shortcuts (mirror the matplotlib keys `n / b / i / a / u / r`).
