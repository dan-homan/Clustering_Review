// Equal mas/pixel WITHOUT locking drag-zoom to the panel aspect, for the
// spatial plots that want it: the "Position" view's XY centroid track (bottom
// subplot), the epoch overlay (single panel), and the admin Window-N review
// overlay (same single-panel overlay figure).
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
//     .domain), so we own ONLY xaxis2.domain (horizontal letterbox).
//   meta == "overlay-equal"  (epoch overlay): single panel, so we own BOTH
//     xaxis.domain and yaxis.domain (full-2D letterbox).
//
// SELF-HEALING (why a stuck view can't survive) — the domains we write are
// preserved by the figure's constant `uirevision` across epoch/data updates.
// That is intended for zoom persistence, but it means a bad domain, once
// written, would stick — and the modebar "home" / double-click reset the axis
// RANGES but not DOMAINS and don't change uirevision, so they can't clear it
// (only a uirevision bump — the "Reset view" buttons — or a reload could).
// To guarantee a bad domain never persists we: (1) recompute on BOTH
// plotly_afterplot AND plotly_relayout (the latter fires once with the SETTLED
// ranges after any interaction, incl. a reset), (2) reject non-finite /
// transient inputs so a mid-animation frame can't produce a collapsed domain,
// and (3) bulletproof the re-entrancy guard (try/catch + finally + watchdog) so
// it can never latch on and kill the listener. Result: the letterbox always
// re-converges to the correct domain, so uirevision only ever preserves a good
// one.
//
// Safe alongside the overlay's beam callback: that uses Plotly.restyle (no
// relayout event) and ignores domain-only relayouts (keys on
// xaxis.range[*] / autorange), so our domain writes never drive it.

(function () {
    // "overlay-graph" is the standard-view epoch overlay; "nwin-overlay-graph"
    // is the admin Window-N review overlay — same overlay_figure_for_epoch
    // (meta == "overlay-equal"), so it wants the identical full-2D letterbox.
    // Both nwin beam callbacks ignore domain-only relayouts, so our domain
    // writes never disturb them.
    const GRAPH_IDS = ["summary-graph", "summary-graph-right",
                       "overlay-graph", "nwin-overlay-graph"];
    const MIN_DOM = 0.03;          // floor so an extreme zoom can't collapse a
                                   // domain to zero (unusable)
    const GUARD_MS = 1500;         // watchdog: force-clear the guard if a
                                   // relayout promise never settles
    const wired = {};              // gid -> true once its events are hooked
    const applying = {};           // gid -> re-entrancy guard for our relayout

    function gdOf(gid) {
        const wrap = document.getElementById(gid);
        return wrap ? wrap.querySelector(".js-plotly-plot") : null;
    }

    function finite2(a) {
        return a && Number.isFinite(a[0]) && Number.isFinite(a[1]);
    }

    function near(a, b) {
        return a && b &&
            Math.abs(a[0] - b[0]) < 1e-3 && Math.abs(a[1] - b[1]) < 1e-3;
    }

    function centered(span) {
        return [0.5 - span / 2, 0.5 + span / 2];
    }

    // Write domains under a bulletproof re-entrancy guard: cleared by the
    // promise (finally) AND by a watchdog, so it can never latch true and
    // silently disable the letterbox (the "stuck until reload" failure).
    function applyDomains(gid, gd, patch) {
        applying[gid] = true;
        setTimeout(function () { applying[gid] = false; }, GUARD_MS);
        try {
            var p = window.Plotly.relayout(gd, patch);
            if (p && p.finally) p.finally(function () { applying[gid] = false; });
            else applying[gid] = false;
        } catch (e) {
            applying[gid] = false;
        }
    }

    // meta == "xy-bottom": narrow xaxis2.domain to match the bottom panel's
    // current height (owned by subplot_resize) — horizontal-only letterbox.
    function equalizeXYBottom(gid, gd, fl) {
        if (!fl.xaxis2 || !fl.yaxis2) return;
        const sz = fl._size;
        if (!sz || !(sz.w > 0) || !(sz.h > 0)) return;
        const xr = fl.xaxis2.range, yr = fl.yaxis2.range, ydom = fl.yaxis2.domain;
        if (!finite2(xr) || !finite2(yr) || !ydom) return;
        const dx = Math.abs(xr[1] - xr[0]), dy = Math.abs(yr[1] - yr[0]);
        if (!(dx > 0) || !(dy > 0)) return;
        const hPix = sz.h * (ydom[1] - ydom[0]);   // bottom-panel height, px
        if (!(hPix > 0)) return;
        let xSpan = (hPix / dy) * dx / sz.w;
        if (!Number.isFinite(xSpan)) return;
        xSpan = Math.max(MIN_DOM, Math.min(1.0, xSpan));
        const xdom = centered(xSpan);
        if (near(fl.xaxis2.domain, xdom)) return;
        applyDomains(gid, gd, { "xaxis2.domain": xdom });
    }

    // meta == "overlay-equal": single panel — shrink whichever of x/y domain is
    // roomier so px/mas match (full-2D letterbox).
    function equalizeSinglePanel(gid, gd, fl) {
        if (!fl.xaxis || !fl.yaxis || fl.yaxis2) return;
        const sz = fl._size;
        if (!sz || !(sz.w > 0) || !(sz.h > 0)) return;
        const xr = fl.xaxis.range, yr = fl.yaxis.range;
        if (!finite2(xr) || !finite2(yr)) return;
        const dx = Math.abs(xr[1] - xr[0]), dy = Math.abs(yr[1] - yr[0]);
        if (!(dx > 0) || !(dy > 0)) return;
        const sx = sz.w / dx, sy = sz.h / dy;      // px per mas at full domain
        if (!Number.isFinite(sx) || !Number.isFinite(sy)) return;
        let domX = 1.0, domY = 1.0;
        if (sx <= sy) domY = Math.max(MIN_DOM, sx / sy);
        else domX = Math.max(MIN_DOM, sy / sx);
        const xdom = centered(domX), ydom = centered(domY);
        if (near(fl.xaxis.domain, xdom) && near(fl.yaxis.domain, ydom)) return;
        applyDomains(gid, gd, { "xaxis.domain": xdom, "yaxis.domain": ydom });
    }

    function equalize(gid) {
        const gd = gdOf(gid);
        if (!gd || !gd._fullLayout || applying[gid]) return;
        const fl = gd._fullLayout;
        if (!gd.data || gd.data.length === 0) return;   // empty / hidden
        const meta = fl.meta || (gd.layout && gd.layout.meta);
        if (meta === "xy-bottom") equalizeXYBottom(gid, gd, fl);
        else if (meta === "overlay-equal") equalizeSinglePanel(gid, gd, fl);
    }

    function wire(gid) {
        if (wired[gid]) return true;
        // Wrapper absent from the DOM = this graph isn't on the current page
        // (e.g. nwin-overlay-graph without --admin). Treat as "done" so the
        // init poll can still settle; a present-but-not-yet-rendered wrapper
        // (Plotly hasn't drawn the figure) returns false and keeps polling.
        if (!document.getElementById(gid)) return true;
        const gd = gdOf(gid);
        if (!gd || !gd.on) return false;
        wired[gid] = true;
        // afterplot: initial render, view switch, server react, resize.
        // relayout: fires once with the SETTLED ranges after any interaction
        //   (zoom, pan, double-click / home reset) — the self-heal path.
        gd.on("plotly_afterplot", function () { equalize(gid); });
        gd.on("plotly_relayout", function () { equalize(gid); });
        equalize(gid);
        return true;
    }

    function init() {
        let tries = 0;
        (function attempt() {
            // A graph div only exists once its callback first renders; keep
            // retrying for whichever is still absent. The admin Window-N
            // overlay lives in a collapsed <details> and can render a beat
            // later, so poll fast at first, then back off to 1 Hz for a
            // bounded while rather than giving up at 8 s.
            const done = GRAPH_IDS.map(wire);
            if (done.every(Boolean)) return;
            tries++;
            if (tries > 340) return;                 // ~8 s fast + ~5 min slow
            setTimeout(attempt, tries < 40 ? 200 : 1000);
        })();
        window.addEventListener("resize", function () {
            GRAPH_IDS.forEach(equalize);
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
