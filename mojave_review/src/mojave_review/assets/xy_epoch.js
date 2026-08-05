// Two browser-side behaviours for the Position view's XY centroid-track panel
// (the bottom subplot, layout.meta == "xy-bottom"):
//
//   1. ACTIVE-EPOCH SQUARES — the track points belonging to the epoch shown in
//      the Epoch overlay get a cyan open square, the XY counterpart of the
//      vertical gray epoch line the epoch-axis panels get (ui/callbacks.py).
//   2. EPOCH-RANGE FILTER — points whose epoch falls outside the range
//      currently visible in the distance-vs-epoch panel ABOVE are hidden, so
//      zooming that panel in time thins the XY plot to just those epochs.
//      Unzoomed, the top panel autoranges over every point, so nothing is
//      hidden: the filter is a no-op until the reviewer actually zooms, and
//      double-click / "Reset view" restores everything. Only the epoch (x)
//      range filters — a distance (y) zoom deliberately does NOT hide points.
//
// Both are per-point edits to an already-drawn figure, so they run here via
// Plotly.restyle instead of a server figure rebuild: cheap enough to scrub
// epochs with, and it leaves `uirevision` alone so the reviewer's zoom (and the
// letterboxed domains equal_aspect.js wrote) survive.
//
// The XY traces carry their epochs AND their unmasked x/y in `meta`
// (plots/summary._xy_meta) because the DRAWN arrays are lossy — once a point is
// nulled we could not restore it. Reading originals from meta keeps this script
// stateless: a server rebuild ships fresh originals, so there is no cache that
// can go stale (and none to invalidate on source/model/view change).
//
// Interplay with the other graph scripts:
//   * equal_aspect.js letterboxes the same panel by writing axis DOMAINS. We
//     only write trace x/y, and restyle emits no plotly_relayout, so its
//     domain-only relayouts and our restyles cannot drive each other. Its
//     afterplot recompute sees an unchanged domain and no-ops.
//   * the active-epoch line callback writes layout SHAPES via relayout; that
//     relayout re-enters us, we recompute the same mask and skip the restyle
//     (every apply() is compared against what is already drawn — see changed()).
//   * axis ranges are set server-side over EVERY point, so hiding points never
//     re-frames the panel.
//
// The active epoch itself arrives from Dash: the clientside epoch-line
// callbacks (ui/callbacks.py, ui/compare_callbacks.py) call
// window.mojaveXY.setEpoch(graphId, decimalEpoch).

(function () {
    // Every graph that can render a build_summary_figure("Position"): both main
    // panes and both comparison-page panels.
    const GRAPH_IDS = ["summary-graph", "summary-graph-right",
                       "cmp-x-summary-graph", "cmp-c-summary-graph"];
    const EP_TOL = 5e-4;        // ~4 h — decimal-epoch float match (epochs are
                                // stored to 4 dp and are days apart at worst)
    const GUARD_MS = 1500;      // watchdog, same pattern as equal_aspect.js
    const wired = {};           // gid -> true once its events are hooked
    const applying = {};        // gid -> re-entrancy guard for our restyle
    const pending = {};         // gid -> an apply() arrived DURING our restyle
    const epochOf = {};         // gid -> active decimal epoch (null = none)

    function gdOf(gid) {
        const wrap = document.getElementById(gid);
        return wrap ? wrap.querySelector(".js-plotly-plot") : null;
    }

    // A point is "not drawn" whether it arrived as JSON null (our mask) or as
    // NaN (plotly 6 base64-encodes numeric arrays, so server-side nulls/NaNs
    // come back inside a Float64Array). Treating them alike is what makes
    // changed() idempotent for genuinely-NaN positions.
    function hidden(v) {
        return v === null || v === undefined || Number.isNaN(v);
    }

    // What is actually DRAWN for trace i. Read _fullData, not data: plotly 6
    // ships numeric arrays as base64 ({dtype, bdata}) and leaves them encoded in
    // gd.data, decoding only into _fullData. Comparing against the encoded
    // object made every trace look changed on the first pass after each server
    // rebuild, costing a full pointless restyle of the whole XY panel.
    function drawnX(gd, i) {
        const fd = gd._fullData && gd._fullData[i];
        const x = fd && fd.x;
        return (x && x.length !== undefined) ? x : gd.data[i].x;
    }

    // Would restyling to `want` change anything that is drawn? For masked
    // traces only the null pattern can differ (values never move), so compare
    // that; for the highlight trace compare the values themselves.
    function changed(cur, want, byValue) {
        if (!cur || cur.length !== want.length) return true;
        for (let j = 0; j < want.length; j++) {
            if (byValue) {
                if (Math.abs(cur[j] - want[j]) > 1e-9) return true;
            } else if (hidden(cur[j]) !== hidden(want[j])) {
                return true;
            }
        }
        return false;
    }

    // Our own restyle re-enters us (it redraws -> plotly_afterplot), so a pass
    // is skipped while one is in flight. It must be REMEMBERED, not dropped: a
    // zoom / epoch step that lands inside that window is a real state change
    // with no further event coming, and dropping it left the panel showing a
    // stale mask until the reviewer next touched the plot. So re-run once the
    // restyle settles; convergence is guaranteed because a pass with nothing to
    // change issues no restyle (so the chain ends after one no-op).
    function release(gid) {
        applying[gid] = false;
        if (pending[gid]) {
            pending[gid] = false;
            setTimeout(function () { apply(gid); }, 0);
        }
    }

    function apply(gid) {
        const gd = gdOf(gid);
        if (!gd || !gd._fullLayout || !gd.data) return;
        if (applying[gid]) { pending[gid] = true; return; }
        if (!window.Plotly) return;
        const fl = gd._fullLayout;
        const meta = fl.meta || (gd.layout && gd.layout.meta);
        if (meta !== "xy-bottom") return;        // not the Position view
        // Row 1 (xaxis) is the distance-vs-epoch panel; row 2 (xaxis2) is the
        // XY panel we are filtering.
        const xr = fl.xaxis && fl.xaxis.range;
        if (!xr || !Number.isFinite(xr[0]) || !Number.isFinite(xr[1])) return;
        const lo = Math.min(xr[0], xr[1]), hi = Math.max(xr[0], xr[1]);
        const ep = epochOf[gid];

        const idx = [], xs = [], ys = [];
        const hx = [], hy = [];
        let hlIdx = -1;
        for (let i = 0; i < gd.data.length; i++) {
            const t = gd.data[i], m = t.meta;
            if (!m || !m.xy) continue;           // distance traces, fits, core
            if (m.xy === "highlight") { hlIdx = i; continue; }
            const n = m.ep.length;
            const nx = new Array(n), ny = new Array(n);
            for (let j = 0; j < n; j++) {
                const vis = m.ep[j] >= lo && m.ep[j] <= hi;
                nx[j] = vis ? m.x0[j] : null;
                ny[j] = vis ? m.y0[j] : null;
                // Highlight only the cluster tracks (the overlay subsets would
                // stack a second square on the same point), and only where the
                // point is actually drawn.
                if (vis && m.xy === "track" && ep !== null && ep !== undefined &&
                        Math.abs(m.ep[j] - ep) < EP_TOL &&
                        !hidden(nx[j]) && !hidden(ny[j])) {
                    hx.push(nx[j]);
                    hy.push(ny[j]);
                }
            }
            if (changed(drawnX(gd, i), nx, false)) {
                idx.push(i); xs.push(nx); ys.push(ny);
            }
        }
        if (hlIdx >= 0 && changed(drawnX(gd, hlIdx), hx, true)) {
            idx.push(hlIdx); xs.push(hx); ys.push(hy);
        }
        if (!idx.length) return;                 // already drawn correctly

        applying[gid] = true;
        setTimeout(function () { if (applying[gid]) release(gid); }, GUARD_MS);
        try {
            const p = window.Plotly.restyle(gd, { x: xs, y: ys }, idx);
            if (p && p.finally) p.finally(function () { release(gid); });
            else release(gid);
        } catch (e) {
            release(gid);
        }
    }

    function wire(gid) {
        if (wired[gid]) return;
        // Absent OR not-yet-drawn -> keep polling (Dash mounts its layout after
        // this script's init runs; summary-graph-right / the compare panels
        // appear later still). Same rationale as equal_aspect.js: never treat
        // "wrapper absent" as done.
        const gd = gdOf(gid);
        if (!gd || !gd.on) return;
        wired[gid] = true;
        // afterplot: first render, view switch, server rebuild, resize.
        // relayout: fires with the SETTLED ranges after a zoom / pan / reset.
        gd.on("plotly_afterplot", function () { apply(gid); });
        gd.on("plotly_relayout", function () { apply(gid); });
        apply(gid);
    }

    // Called by the Dash clientside epoch-line callbacks.
    window.mojaveXY = {
        setEpoch: function (gid, epoch) {
            epochOf[gid] = (epoch === undefined) ? null : epoch;
            apply(gid);
        }
    };

    function init() {
        let tries = 0;
        (function attempt() {
            GRAPH_IDS.forEach(wire);
            if (tries++ > 340) return;           // ~8 s fast + ~5 min slow
            setTimeout(attempt, tries < 40 ? 200 : 1000);
        })();
        window.addEventListener("resize", function () {
            GRAPH_IDS.forEach(apply);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
