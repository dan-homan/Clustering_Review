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
│       ├── backups/backup_NNN_*.{csv,pdf,mp4,json,txt}  <-- alternate models
│       ├── cluster_fits/                                 <-- per-window cluster fits
│       ├── config_win.json
│       ├── history.txt
│       └── run_string.txt
└── mojave_review/               # the web app (pip-installable)
    ├── pyproject.toml
    └── src/mojave_review/
        ├── cli.py               # `mojave-review` entry point
        ├── app.py               # Dash factory
        ├── data/loader.py       # source/model discovery, CSV+NPZ load
        ├── plots/summary.py     # Plotly port of make_summary_plots
        ├── ui/{layout,callbacks}.py
        └── recommendations/     # (planned) JSON store
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
  instead (see below).

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

`build_summary_figure(view=...)` produces a 2-row figure for one of:

| View | Top | Bottom |
|---|---|---|
| Position | distance vs epoch (+ polyfit overlay) | PA vs epoch |
| Flux | log₁₀(I flux) vs epoch | log₁₀(Tb) vs epoch (15.4 GHz, z param) |
| Polarization | log₁₀(P flux) vs epoch | EVPA vs epoch |
| Kinematics | speed vs distance | X/Y velocity vectors w/ arrowheads, +x reversed |

### Plotting conventions (always apply to spatial / sky plots)

- **+x to the left** (astronomical convention). Use explicit `range=[hi, lo]`
  to reverse the x-axis rather than `autorange="reversed"` so it composes
  cleanly with `scaleanchor`.
- **Equal aspect** on any x[mas] vs y[mas] plot. Set `scaleanchor="x2"` (or
  appropriate axis ref) and `scaleratio=1.0` on the y-axis, plus
  `constrain="domain"` on both.
- **Arrows use `fig.add_annotation(showarrow=True, arrowhead=2, ...)`** —
  never line-mode hacks with a triangle marker at the end.
- Always show a black `×` at `(0,0)` on spatial plots (the core).
- Include `(0,0)` in the auto-range computation.

### Cluster styling (ported from cluster_code.py)

```python
cl_colors  = ["b", "g", "r", "m", "y", "gray"]    # cycle by clusterID
cl_markers = ["x","o","s","o","s","p","*","^","v",
              "*","^","v","X","D","P","D","1"]
cl_fill    = ["none","full","none","none","full","none","full","none","full",
              "none","full","none","full","none","full","full","none"]
```

- Non-robust clusters → all cyan, but keep marker/fill from the rotation.
- `clusterID < 0` or `>= 1000` → black `+`, never in legend.
- `use_in_fit == False` → black slash overlay on the marker.
- `select == True` → gold open-diamond overlay.

### Numerical quirks ported from `make_summary_plots`

- **PA de-wrap**: if `|pa[i] - pa[i-1]| > 300°`, nudge by ±360°.
- **PA shift** (`shift_pa` flag): if median |pa| > 120° across non-core
  clusters, add 360 to any PA < -60. Keeps southern jets readable.
- **EVPA de-wrap**: same idea with period 180°, jump 150°.
- **Size floor**: `size = max(sqrt(fwhm_maj * fwhm_min), 0.1)` mas.
- **Tb formula**: `1.22e12 * flux * (1+z) / (15.4² * size²)` K (U-band fixed).
- **Position polyfit** is only drawn if ≥5 valid `use_in_fit` points AND
  (motion is >3σ OR speed < 0.05 with combined error < 0.05 mas/yr).

## Recommendations (planned — not yet implemented)

Reviewer actions map to entries in a per-(source, reviewer) JSON file under
`<results-parent>/recommendations/<source>/<reviewer>.json`. The app never
modifies anything under `Results/`. Schema sketch:

```jsonc
{
  "source": "...", "model": "current" | "backup_NNN",
  "model_sha": "...", "reviewer": "...", "updated_at": "...",
  "source_comment": "...",
  "cluster_feedback": {"3": {"robust_agree": false, "comment": "..."}},
  "epoch_feedback": {"2003.10": {"comment": "..."}},
  "edits": [
    {"op": "change_clusterID", "scope": "single"|"all_epochs", ...},
    {"op": "set_use_in_fit",    "scope": "single"|"epoch", ...}
  ]
}
```

`use_in_fit` has exactly two scopes: `single` (one point) and `epoch`
(whole epoch). No "cluster_in_epoch" scope.

`change_clusterID` scopes: `single` (one row) and `all_epochs` (renumber a
cluster everywhere). These mirror the `i` and `a` keys in
`cluster_code.py`'s interactive review.

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

1. **Phase 1 (current)** — pip-installable local app. Read-only viewer first,
   then recommendations capture, then keyboard shortcuts. No auth.
2. **Phase 2** — host on the user's university web server. Adds Google OAuth
   + email allowlist. Same codebase; deploy differences are reviewer
   identity (from login) and per-user recommendations dirs.

### Phase 2 hosting requirements (for the IT conversation)

- Python ≥ 3.10 in user space (`pyenv`/`uv` OK).
- Long-lived process bound to a local port (`gunicorn` + systemd).
- Reverse-proxy line under existing nginx/Apache: `https://<host>/mojave-review/` → `http://127.0.0.1:<port>/`.
- Writable persistent dir (~10 GB) for FITS cache + recommendations.
- Outbound HTTPS allowed to `www.cv.nrao.edu`.
- One OAuth callback URL registered if/when Google login is added.

## Don't / gotchas

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

## Useful local commands

```bash
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
