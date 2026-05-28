# External references

## MOJAVE FITS archive

URL pattern at NRAO:

```
http://www.cv.nrao.edu/2cmVLBA/data/<source>/<epoch_name>/<source>.<band>.<epoch_name>.<stokes>cn.fits.gz
```

- `<source>`  — B1950 form without band suffix, e.g. `0003-066`.
- `<epoch_name>` — underscored date, e.g. `1995_07_28`.
- `<band>`  — e.g. `u` (U-band, 15 GHz).
- `<stokes>` — `i`, `q`, or `u`.

Files are gzipped FITS. The web app fetches them on demand and caches them
under `~/.mojave_review/cache/<source>/<epoch_name>/...` (overridable via
`--cache-dir`).

The on-disk `grab_mojave_image` function in `cluster_code.py` looks similar
but actually opens a *local* file from `root_data_dir`; only the web app
fetches from this URL.

## Google Drive sharing (current workflow)

The project author shares the `Results/` folder with a small group of
collaborators via Google Drive. Today's review is by hand against the
`.pdf` (summary plots) and `.mp4` (epoch overplots) files in that folder
— the limitation that motivates this tool.

In phase 1, the web app does not authenticate against Drive. Reviewers use
Google Drive for Desktop to maintain a local mirror and pass that path via
`--results-dir`. Phase 2 (hosted) can either continue to mirror the
folder server-side via a service account, or rsync from a known location.

## Original pipeline code

`cluster_code.py` (~6000 LOC) and `find_clusters.py` in the repo root
are the production clustering pipeline. They are intentionally *not*
imported by the web app — the web app only consumes their output files.

The pipeline files are kept in the repo for reference but are gitignored
to keep the web-app history clean. See `../.gitignore`.
