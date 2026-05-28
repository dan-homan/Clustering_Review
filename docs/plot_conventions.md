# Plot conventions

These conventions apply to **any** plot in this codebase whose axes are
sky-plane coordinates (mas, mas, RA, Dec, X/Y position, FITS image,
velocity vectors, ...). Apply them by default when adding new plots.

1. **Positive x points to the LEFT** (astronomical convention — RA
   increases to the left in standard sky projection). In Plotly, use an
   explicit `range=[hi, lo]` to reverse the x-axis. **Do not use**
   `autorange="reversed"` together with an explicit `range` — they fight,
   and `scaleanchor` does not compose cleanly with the former.

2. **Equal mas/pixel scale.** Set explicit `range` on both axes from
   `plots/_extent.compute_source_extent` so each source opens framed on
   its jet. Apply `scaleanchor` + `scaleratio=1.0` on the y-axis AND
   `constrain="domain"` on **both** axes:
   ```python
   fig.update_xaxes(constrain="domain", ...)
   fig.update_yaxes(scaleanchor="x", scaleratio=1.0,
                    constrain="domain", ...)
   ```
   Keeps mas/pixel equal everywhere (a square in data is a square on
   screen; FWHM / 3σ / beam ellipses stay round). Plotly compresses the
   panel domain rather than the data range to reconcile the constraint
   with the container size.

   **Known limitation, reviewer-accepted (2026-05-28):** with
   `scaleanchor` on, Plotly's box-zoom *drag tool* also locks the
   rectangle to the panel's current aspect — there is no native way to
   keep equal scale AND get a free-form drag. The reviewer chose
   locked-aspect drag over breaking equal scale. Don't drop
   `scaleanchor` to "fix" the drag; the decision is already settled.

3. **Real arrowheads on vector / quiver plots.** Use
   `fig.add_annotation(showarrow=True, arrowhead=2, arrowwidth=2,
   arrowcolor=...)`, one annotation per vector, with `axref`/`ayref`
   matching the subplot's axes. Line-mode hacks with a marker at the end
   are not acceptable.

4. **Mark the core at (0, 0)** on any X/Y mas plot — a black `×` (Plotly
   symbol `"x-thin"`, size 14, line width 2). Include `(0, 0)` in the
   range computation so the core is always visible even when no cluster
   sits near it.

5. **Vector lengths must be user-tunable.** Auto-fit (longest arrow ≈ 25%
   of panel span) is a reasonable default, but at least one MOJAVE source
   has vectors too small to see at auto-fit scale. Expose a slider /
   keyboard shortcut for the multiplier.

## Layout preference (broader)

Prefer larger, fewer panels over dense 2×2 / 3×2 grids. Reviewers are
scanning for subtle anomalies (mis-clustered points, suspect epochs,
broken polarization fits) — cramped panels fight that. The current
`build_summary_figure` is split into four physics-grouped 2-row views
(Position / Flux / Polarization / Kinematics) for exactly this reason.

## Why these rules exist

Most of these were corrections to a first-pass implementation that used
matplotlib idioms ported naively to Plotly. Each rule maps to a specific
visual misread or missing affordance the reviewer would otherwise hit.
