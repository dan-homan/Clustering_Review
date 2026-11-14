# Project documentation

This directory holds project notes that travel with the repo. The day-to-day
"how the code is laid out" reference is [`../CLAUDE.md`](../CLAUDE.md);
these files capture the *why* and the *who*.

| File | Topic |
|---|---|
| [audience.md](audience.md) | Who this tool is for and who's building it. |
| [plot_conventions.md](plot_conventions.md) | Astronomical plot conventions (always apply to new plots). |
| [product_decisions.md](product_decisions.md) | Locked product/architecture decisions and phase plan. |
| [syncing_results.md](syncing_results.md) | **Read before setting up a sync.** Getting `Results/` to the server per project without destroying `recommendations/` — it mirrors with `--delete`. |
| [multi_project.md](multi_project.md) | **Decided, not yet implemented.** Serving several projects (own Results/recommendations/notes) from one install: one process, apps mounted per path prefix. |
| [deployment_phase2.md](deployment_phase2.md) | Phase 2 hosting plan (university web server + per-user static tokens). Lift sections directly for the IT conversation. |
| [astrometric_reference.md](astrometric_reference.md) | The absolute-astrometry origin option: data format, sign convention, what it touches. |
| [references.md](references.md) | External pointers: MOJAVE FITS URL pattern, Google Drive sharing flow. |
