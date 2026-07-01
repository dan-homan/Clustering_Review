// Equal mas/pixel WITHOUT locking drag-zoom to the panel aspect, for the
// spatial plots that want it: the "Position" view's XY centroid track (bottom
// subplot) and the epoch overlay (single panel).
//
// Plotly's usual way to keep equal units is `scaleanchor`, but that forces the
// zoom box to a fixed aspect — you can't isolate a tall-skinny or wide-flat
// region. So those axes drop `scaleanchor` (free-form zoom), and this script
// re-imposes equal units by LETTERBOXING: after every draw/zoom it narrows an
// axis *domain* (the fraction of the panel an axis fills) so pixels-per-mas
// match the current ranges. Circles / beam / FWHM ellipses stay true-scale at
// any zoom shape; the plot box just gets padding on its short side(s).
//
// Two modes, chosen by the figure's `layout.meta` (titles can't disambiguate —
// Kinematics' bottom shares "X/Y [mas]" axes but keeps scaleanchor):
//
//   meta == "xy-bottom"      (Position summary): the XY panel is the BOTTOM of a
//     2-row figure. subplot_resize.js owns the vertical split (yaxis/yaxis2
//     .domain), so we own ONLY xaxis2.domain (horizontal letterbox) to avoid a
//     conflict. Equal units hold while the box has enough width; a wide-flat
//     zoom may need a top/bottom divider nudge to stay equal.
//   meta == "overlay-equal"  (epoch overlay): single panel, so we own BOTH
//     xaxis.domain and yaxis.domain (full-2D letterbox).
//
// Mechanism mirrors subplot_resize.js / the beam callback: hook the graph div's
// plotly_afterplot, call Plotly.relayout directly, guard re-entrancy so our own
// domain write doesn't recurse (no-ops once equalized). Safe alongside the
// overlay's beam callback: that uses Plotly.restyle (no relayout event) and
// ignores domain-only relayouts (it keys on xaxis.range[*] / autorange).

(function () {
    const GRAPH_IDS = ["summary-graph", "summary-graph-right", "overlay-graph"];
    const MIN_DOM = 0.03;          // floor so an extreme zoom can't collapse a
                                   // domain to zero (unusable)
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

    function centered(span) {
        return [0.5 - span / 2, 0.5 + span / 2];
    }

    // meta == "xy-bottom": narrow xaxis2.domain to match the bottom panel's
    // current height (owned by subplot_resize) — horizontal-only letterbox.
    function equalizeXYBottom(gid, gd, fl) {
        if (!fl.xaxis2 || !fl.yaxis2) return;
        const sz = fl._size;
        if (!sz || !(sz.w > 0) || !(sz.h > 0)) return;
        const xr = fl.xaxis2.range, yr = fl.yaxis2.range, ydom = fl.yaxis2.domain;
        if (!xr || !yr || !ydom) return;
        const dx = Math.abs(xr[1] - xr[0]), dy = Math.abs(yr[1] - yr[0]);
        if (!(dx > 0) || !(dy > 0)) return;
        const hPix = sz.h * (ydom[1] - ydom[0]);   // bottom-panel height, px
        if (!(hPix > 0)) return;
        let xSpan = (hPix / dy) * dx / sz.w;
        xSpan = Math.max(MIN_DOM, Math.min(1.0, xSpan));
        const xdom = centered(xSpan);
        if (near(fl.xaxis2.domain, xdom)) return;
        applying[gid] = true;
        window.Plotly.relayout(gd, { "xaxis2.domain": xdom })
            .then(() => { applying[gid] = false; })
            .catch(() => { applying[gid] = false; });
    }

    // meta == "overlay-equal": single panel — shrink whichever of x/y domain is
    // roomier so px/mas match (full-2D letterbox).
    function equalizeSinglePanel(gid, gd, fl) {
        if (!fl.xaxis || !fl.yaxis || fl.yaxis2) return;
        const sz = fl._size;
        if (!sz || !(sz.w > 0) || !(sz.h > 0)) return;
        const xr = fl.xaxis.range, yr = fl.yaxis.range;
        if (!xr || !yr) return;
        const dx = Math.abs(xr[1] - xr[0]), dy = Math.abs(yr[1] - yr[0]);
        if (!(dx > 0) || !(dy > 0)) return;
        const sx = sz.w / dx, sy = sz.h / dy;      // px per mas at full domain
        let domX = 1.0, domY = 1.0;
        if (sx <= sy) domY = Math.max(MIN_DOM, sx / sy);
        else domX = Math.max(MIN_DOM, sy / sx);
        const xdom = centered(domX), ydom = centered(domY);
        if (near(fl.xaxis.domain, xdom) && near(fl.yaxis.domain, ydom)) return;
        applying[gid] = true;
        window.Plotly.relayout(gd, { "xaxis.domain": xdom, "yaxis.domain": ydom })
            .then(() => { applying[gid] = false; })
            .catch(() => { applying[gid] = false; });
    }

    function equalize(gid) {
        const gd = gdOf(gid);
        if (!gd || !gd._fullLayout) return;
        const fl = gd._fullLayout;
        if (!gd.data || gd.data.length === 0) return;   // empty / hidden
        const meta = fl.meta || (gd.layout && gd.layout.meta);
        if (meta === "xy-bottom") equalizeXYBottom(gid, gd, fl);
        else if (meta === "overlay-equal") equalizeSinglePanel(gid, gd, fl);
    }

    function wire(gid) {
        const gd = gdOf(gid);
        if (!gd || !gd.on) return false;
        if (wired[gid]) return true;
        wired[gid] = true;
        // Fires after every draw / relayout / react (incl. zoom, view switch,
        // epoch step, and the divider's Plotly.Plots.resize). Guarded so our
        // own domain write doesn't recurse.
        gd.on("plotly_afterplot", () => { if (!applying[gid]) equalize(gid); });
        equalize(gid);
        return true;
    }

    function init() {
        let tries = 0;
        (function attempt() {
            // A graph div only exists once its callback first renders; keep
            // retrying a bounded while for whichever is still absent.
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
