// Equal mas/pixel for the XY centroid-track panel (the BOTTOM of the "Position"
// summary view), WITHOUT locking drag-zoom to the panel aspect.
//
// Plotly's usual way to keep equal units is `scaleanchor`, but that forces the
// zoom box to a fixed aspect — you can't isolate a tall-skinny or wide-flat
// region. So the XY axes drop `scaleanchor` (free-form zoom), and this script
// re-imposes equal units by LETTERBOXING: after every draw/zoom it narrows the
// XY panel's horizontal domain (`xaxis2.domain`) so that pixels-per-mas match
// the height the top/bottom divider currently gives the panel. Circles/tracks
// stay true-scale at any zoom shape; the XY box just gets padding on its sides.
//
// Division of labour with subplot_resize.js (no conflict — disjoint props):
//   - subplot_resize.js owns the VERTICAL split: yaxis.domain / yaxis2.domain.
//   - this script owns only xaxis2.domain (horizontal letterbox of the bottom).
// Because we can only adjust width here, equal units hold whenever the XY box
// has enough width (the normal case: a wide bottom panel, a roughly-square
// source). Zoom to a very wide-flat region and the width can run out at the
// current height — nudge the top/bottom divider to shrink the XY panel and it
// snaps back to equal. (Full 2-D auto-fit would require also owning
// yaxis2.domain, which fights the divider — deliberately not done.)
//
// Identification: the Position figure is tagged `layout.meta = "xy-bottom"`.
// Titles can't be used — Kinematics' bottom shares "X/Y [mas]" axes but keeps
// scaleanchor. Scope: the two summary graphs (Position can show on either pane).
//
// Mechanism mirrors subplot_resize.js / the beam callback: hook the graph div's
// plotly_afterplot, call Plotly.relayout directly, and guard re-entrancy so our
// own domain write doesn't recurse (it no-ops once already equalized).

(function () {
    const GRAPH_IDS = ["summary-graph", "summary-graph-right"];
    const MIN_DOM = 0.03;          // floor so an extreme zoom can't collapse the
                                   // XY box to zero width (unusable)
    const wired = {};              // gid -> true once its afterplot is hooked
    const applying = {};           // gid -> re-entrancy guard for our relayout

    function gdOf(gid) {
        const wrap = document.getElementById(gid);
        return wrap ? wrap.querySelector(".js-plotly-plot") : null;
    }

    function near(a, b) {
        return a && b &&
            Math.abs(a[0] - b[0]) < 1e-3 && Math.abs(a[1] - b[1]) < 1e-3;
    }

    function equalize(gid) {
        const gd = gdOf(gid);
        if (!gd || !gd._fullLayout) return;
        const fl = gd._fullLayout;
        // Only the Position figure's XY bottom panel (flagged from Python).
        const meta = fl.meta || (gd.layout && gd.layout.meta);
        if (meta !== "xy-bottom" || !fl.xaxis2 || !fl.yaxis2) return;
        // Skip empty / hidden graphs (no data, or zero-size when display:none).
        if (!gd.data || gd.data.length === 0) return;
        const sz = fl._size;
        if (!sz || !(sz.w > 0) || !(sz.h > 0)) return;
        const xr = fl.xaxis2.range, yr = fl.yaxis2.range;
        const ydom = fl.yaxis2.domain;
        if (!xr || !yr || !ydom) return;
        const dx = Math.abs(xr[1] - xr[0]);
        const dy = Math.abs(yr[1] - yr[0]);
        if (!(dx > 0) || !(dy > 0)) return;

        // Bottom-panel height in px (its vertical domain slot, owned by
        // subplot_resize) and the full available width in px.
        const hPix = sz.h * (ydom[1] - ydom[0]);
        const wPix = sz.w;
        if (!(hPix > 0) || !(wPix > 0)) return;

        // Width fraction that makes px/mas on x match px/mas on y at this height.
        let xSpan = (hPix / dy) * dx / wPix;
        xSpan = Math.max(MIN_DOM, Math.min(1.0, xSpan));
        const xdom = [0.5 - xSpan / 2, 0.5 + xSpan / 2];   // centered letterbox

        if (near(fl.xaxis2.domain, xdom)) return;
        applying[gid] = true;
        window.Plotly
            .relayout(gd, { "xaxis2.domain": xdom })
            .then(() => { applying[gid] = false; })
            .catch(() => { applying[gid] = false; });
    }

    function wire(gid) {
        const gd = gdOf(gid);
        if (!gd || !gd.on) return false;
        if (wired[gid]) return true;
        wired[gid] = true;
        // Fires after every draw / relayout / react (incl. zoom, view switch,
        // and the divider's Plotly.Plots.resize / subplot_resize relayout).
        gd.on("plotly_afterplot", () => { if (!applying[gid]) equalize(gid); });
        equalize(gid);
        return true;
    }

    function init() {
        let tries = 0;
        (function attempt() {
            // #summary-graph-right only appears once its callback first renders;
            // keep retrying a bounded while for whichever graph is still absent.
            const done = GRAPH_IDS.map(wire);
            if (done.every(Boolean) || tries++ > 40) return;
            setTimeout(attempt, 200);
        })();
        window.addEventListener("resize", () => GRAPH_IDS.forEach(equalize));
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
