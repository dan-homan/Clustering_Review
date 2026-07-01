// Draggable HORIZONTAL divider between the two stacked subplots of the
// summary graph (#summary-graph), letting the reviewer re-balance the top vs
// bottom plot heights.
//
// Mechanism: a drag adjusts the two subplots' yaxis.domain / yaxis2.domain via
// Plotly.relayout. Domains are independent of axis *ranges*, so this preserves
// the user's zoom (uirevision is untouched) and never touches the shared,
// figure-level legend. Same "call Plotly directly from an assets/ script"
// approach as resizable.js (the panel splitter).
//
// The split fraction persists in a module var and is re-applied after every
// redraw (view change, data refresh, the active-epoch line relayout, etc.) via
// the graph div's plotly_afterplot event — the analogue of the beam callback's
// "re-apply on figure change" discipline.
//
// Single-plot views (Position Angle) have no yaxis2; the handle hides itself
// and the relayout is skipped.

(function () {
    const GAP = 0.10;   // inter-panel gap; matches make_subplots vertical_spacing
    const MIN = 0.18;   // smallest domain height allowed for either panel
    // split == gap-center fraction (0 = bottom, 1 = top). 0.50 reproduces the
    // default vertical_spacing=0.10 layout exactly, so the initial look is
    // unchanged until the user drags.
    let split = 0.50;
    let handle = null;
    let applying = false;   // re-entrancy guard around our own relayout

    function gdOf() {
        const wrap = document.getElementById("summary-graph");
        return wrap ? wrap.querySelector(".js-plotly-plot") : null;
    }

    function desired() {
        return { top: [split + GAP / 2, 1.0], bot: [0.0, split - GAP / 2] };
    }

    function applyDomains(gd) {
        const fl = gd && gd._fullLayout;
        if (!fl || !fl.yaxis2) return false;          // single-plot view
        const { top, bot } = desired();
        const c1 = fl.yaxis.domain, c2 = fl.yaxis2.domain;
        const same = (a, b) =>
            Math.abs(a[0] - b[0]) < 1e-4 && Math.abs(a[1] - b[1]) < 1e-4;
        if (same(c1, top) && same(c2, bot)) return true;   // already there
        applying = true;
        window.Plotly.relayout(gd, { "yaxis.domain": top, "yaxis2.domain": bot })
            .then(() => { applying = false; })
            .catch(() => { applying = false; });
        return true;
    }

    function positionHandle(gd) {
        if (!handle) return;
        const fl = gd && gd._fullLayout;
        if (!fl || !fl.yaxis2) { handle.style.display = "none"; return; }
        const sz = fl._size;                       // plot-area geometry, px
        const yPix = sz.t + sz.h * (1 - split);    // gap center, px from gd top
        handle.style.display = "block";
        handle.style.top = (yPix - 4) + "px";
        // Far-left, in the otherwise-empty left margin at the gap height (the
        // y-axis titles are centered on each panel, not here). Keeps the grab
        // bar off the top plot's centered "Epoch" x-axis label.
        handle.style.left = "4px";
        handle.style.width = "48px";
    }

    function refresh() {
        const gd = gdOf();
        if (!gd) return;
        applyDomains(gd);
        positionHandle(gd);
    }

    function ensureHandle() {
        const wrap = document.getElementById("summary-graph");
        if (!wrap) return false;
        if (handle && handle.isConnected) return true;
        wrap.style.position = "relative";
        handle = document.createElement("div");
        handle.className = "hsplit-handle";
        handle.title = "Drag to resize the top / bottom plot";
        wrap.appendChild(handle);

        let dragging = false, startY = 0, startSplit = 0, gdH = 1;
        handle.addEventListener("mousedown", (e) => {
            const gd = gdOf();
            if (!gd || !gd._fullLayout || !gd._fullLayout.yaxis2) return;
            dragging = true;
            startY = e.clientY;
            startSplit = split;
            gdH = gd._fullLayout._size.h || 1;
            document.body.style.cursor = "row-resize";
            document.body.style.userSelect = "none";
            handle.classList.add("hsplit-handle-active");
            e.preventDefault();
            e.stopPropagation();
        });
        document.addEventListener("mousemove", (e) => {
            if (!dragging) return;
            const dy = e.clientY - startY;          // down = lower split fraction
            let s = startSplit - dy / gdH;
            s = Math.max(MIN + GAP / 2, Math.min(1 - MIN - GAP / 2, s));
            split = s;
            refresh();
        });
        document.addEventListener("mouseup", () => {
            if (!dragging) return;
            dragging = false;
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
            handle.classList.remove("hsplit-handle-active");
        });
        return true;
    }

    function wireAfterplot() {
        const gd = gdOf();
        if (!gd || !gd.on || gd.dataset.hsplitWired) return !!(gd && gd.dataset.hsplitWired);
        gd.dataset.hsplitWired = "1";
        // Fires after every draw/relayout/react. Guarded so our own relayout
        // doesn't recurse, and applyDomains no-ops when already at the target.
        gd.on("plotly_afterplot", () => { if (!applying) refresh(); });
        return true;
    }

    function init() {
        let tries = 0;
        (function attempt() {
            const ok = ensureHandle() && wireAfterplot();
            if (ok) { refresh(); return; }
            if (tries++ > 40) return;               // ~8s; Dash renders async
            setTimeout(attempt, 200);
        })();
        window.addEventListener("resize", refresh);
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
