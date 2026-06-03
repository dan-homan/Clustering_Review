# Cluster position uncertainty estimates

How the web app estimates the 1σ uncertainty of each cluster's centroid
position **relative to the core**, and turns that into uncertainties on
distance and position angle (PA) for the error bars on the Position and
XY Position plots.

These are computed in `mojave_review/plots/uncertainty.py`
(`compute_position_uncertainties` / `attach_position_uncertainties`) from the
clean components in `*.plotdata.npz`. They are **derived in the app, not read
from the CSV** — see "Future work" for moving them upstream.

## Inputs

Per epoch, per cluster, we use that cluster's **Stokes-I clean components**
`{(xᵢ, yᵢ, fᵢ)}` (mas positions and I flux). Clean components are mapped to
the *current* cluster IDs via `cc_labels[i] → origID → clusterID` (the same
mapping the overlay uses), so the uncertainties follow the model actually
being plotted, including any applied / visualized cross-ID edits.

- **Weights** `wᵢ = fᵢ` (the I flux). MOJAVE Stokes-I CLEAN components are
  positive in practice; the code still guards `Σwᵢ > 0`.
- **N** = number of Stokes-I clean components in the cluster that epoch.

The flux-weighted centroid below equals the CSV `avg_x`/`avg_y` (verified), and
the core centroid equals `core_x`/`core_y`, so the error bars are centered
exactly on the plotted points.

## Step 1 — flux-weighted centroid

$$\bar{x}_w=\frac{\sum_i w_i x_i}{\sum_i w_i},\qquad
  \bar{y}_w=\frac{\sum_i w_i y_i}{\sum_i w_i}$$

## Step 2 — unbiased weighted standard error of the centroid (per axis)

Individual clean-component position errors are unknown, so we estimate the
spread of the components about the weighted mean with the **unbiased weighted
variance**, then divide by N for the standard error of the mean:

$$s_w^2=\frac{\sum_i w_i\,(x_i-\bar{x}_w)^2}{\frac{N-1}{N}\sum_i w_i},
  \qquad SE = \frac{s_w}{\sqrt N}.$$

These combine to the compact form the code uses (per axis):

$$SE_x^2=\frac{\sum_i w_i\,(x_i-\bar{x}_w)^2}{(N-1)\,\sum_i w_i},\qquad
  SE_y^2=\frac{\sum_i w_i\,(y_i-\bar{y}_w)^2}{(N-1)\,\sum_i w_i}.$$

Computed for each cluster ($SE_{c,x}, SE_{c,y}$) and for the **core**
cluster 0 ($SE_{0,x}, SE_{0,y}$) in the same epoch.

## Step 3 — uncertainty of the position *relative to the core*

The cluster and core centroids are independent measurements, so the
uncertainty of the difference adds in quadrature:

$$\sigma_{\Delta x}=\sqrt{SE_{c,x}^2+SE_{0,x}^2},\qquad
  \sigma_{\Delta y}=\sqrt{SE_{c,y}^2+SE_{0,y}^2},$$

with $\Delta x=\text{avg\_x}-\text{core\_x}$,
$\Delta y=\text{avg\_y}-\text{core\_y}$ and $d=\sqrt{\Delta x^2+\Delta y^2}$
taken from the CSV.

## Step 4 — propagate to distance and PA

The app's position angle is $\mathrm{pa}=\operatorname{atan2}(\Delta x,\Delta y)$
(measured from +y / north). Standard first-order propagation, treating
$\Delta x$ and $\Delta y$ as independent (diagonal covariance):

$$\sigma_d=\frac{\sqrt{\Delta x^{2}\sigma_{\Delta x}^{2}
                    +\Delta y^{2}\sigma_{\Delta y}^{2}}}{d}$$

$$\sigma_{\mathrm{pa}}^{\mathrm{rad}}
  =\frac{\sqrt{\Delta y^{2}\sigma_{\Delta x}^{2}
             +\Delta x^{2}\sigma_{\Delta y}^{2}}}{d^{2}},
  \qquad
  \sigma_{\mathrm{pa}}^{\deg}=\frac{180}{\pi}\,\sigma_{\mathrm{pa}}^{\mathrm{rad}}.$$

The four results — $\sigma_{\Delta x}, \sigma_{\Delta y}, \sigma_d,
\sigma_{\mathrm{pa}}$ (deg) — are attached to the cluster table as
`sig_dx, sig_dy, sig_dist, sig_pa` and drawn as 1σ error bars (distance and
PA on the Position view; X and Y on the XY Position view).

## Choices made (and why)

- **Weight = I flux.** Brighter clean components localize the centroid better.
  Fluxes are positive in practice; `Σw > 0` is enforced.
- **Unbiased weighted variance** exactly as in the reference formula
  (the `(N−1)/N · Σw` denominator), then `SE = s_w/√N`.
- **Cluster − core combined in quadrature** (independent centroids); distance
  and PA via first-order error propagation.
- **Diagonal covariance (per-axis).** We treat the centroid's x and y errors
  as independent — matching the per-axis reference formula. The cross-term
  $\mathrm{Cov}_w(x,y)$ is **omitted** for now; it would matter for strongly
  elongated clean-component distributions and can be added later (see below).
- **N < 2 or Σw ≤ 0 → undefined (NaN).** A single clean component gives no
  spread estimate; such points get no error bar.
- **d = 0 → undefined (NaN).** At/near the core, $\sigma_d$ and
  $\sigma_{\mathrm{pa}}$ diverge (the $1/d$, $1/d^2$ factors). The core itself
  and exactly-coincident points get no bar; genuinely near-core clusters
  correctly get large PA uncertainty.
- **If the core has < 2 clean components that epoch**, $SE_0$ is NaN, so every
  cluster's relative uncertainty that epoch is NaN (no bars) rather than
  silently wrong.
- **Current-model mapping.** CC→cluster membership follows the plotted model
  (applied/visualized cross-ID edits included), so the bars match what's shown.

## Future work

These estimates may later be computed inside the production pipeline
(`cluster_code.py` / `find_clusters.py`) and written to
`merged_win_results.csv` as columns. If/when that happens, the app should
prefer the CSV columns (`sig_dx`, …) when present and fall back to computing
them here only when they're absent. The full 2×2 weighted covariance
(including the $\mathrm{Cov}_w(x,y)$ cross-term) is the natural upgrade at that
point.
