// Draggable vertical splitters between side-by-side plotting panels.
// Adjusts the two panels' `flex` widths on mouse drag and calls
// Plotly.Plots.resize() on every chart inside them so the figures reflow
// live.
//
// Two splitters use this:
//   #split-handle      — main summary-panel | overlay-panel
//   #nwin-split-handle — Window-N panel's BIC*/strip | overlay (admin only)
//
// Dash auto-loads any .js file under the configured `assets_folder`
// (see app.py — points at the installed package's assets/ dir).

(function () {
    // Each splitter: the drag handle plus the panels on its left and right.
    // The Window-N one only exists in --admin mode, so a missing handle is
    // simply skipped (not an error).
    const SPLITTERS = [
        { handle: "split-handle", left: "summary-panel", right: "overlay-panel" },
        { handle: "nwin-split-handle", left: "nwin-left-panel", right: "nwin-right-panel" },
    ];

    function wire(cfg) {
        const handle = document.getElementById(cfg.handle);
        const left = document.getElementById(cfg.left);
        const right = document.getElementById(cfg.right);
        if (!handle || !left || !right) return false;
        if (handle.dataset.splitWired) return true;   // idempotent
        handle.dataset.splitWired = "1";

        let dragging = false;
        let startX = 0;
        let startLeftWidth = 0;
        let totalWidth = 0;
        let rafScheduled = false;

        function resizePlots() {
            if (!window.Plotly) return;
            [left, right].forEach((panel) => {
                const plots = panel.querySelectorAll(".js-plotly-plot");
                plots.forEach((pd) => {
                    try { window.Plotly.Plots.resize(pd); } catch (_) {}
                });
            });
        }

        handle.addEventListener("mousedown", (e) => {
            dragging = true;
            startX = e.clientX;
            startLeftWidth = left.offsetWidth;
            totalWidth = left.offsetWidth + right.offsetWidth;
            document.body.style.cursor = "col-resize";
            document.body.style.userSelect = "none";
            handle.classList.add("split-handle-active");
            e.preventDefault();
        });

        document.addEventListener("mousemove", (e) => {
            if (!dragging) return;
            const dx = e.clientX - startX;
            const minWidth = 120;
            const newLeft = Math.max(
                minWidth,
                Math.min(totalWidth - minWidth, startLeftWidth + dx)
            );
            const newRight = totalWidth - newLeft;
            // 0 0 <px> = don't grow, don't shrink, fixed basis
            left.style.flex = "0 0 " + newLeft + "px";
            right.style.flex = "0 0 " + newRight + "px";

            // Throttle expensive Plotly resizes to one per animation frame.
            if (!rafScheduled) {
                rafScheduled = true;
                requestAnimationFrame(() => {
                    rafScheduled = false;
                    resizePlots();
                });
            }
        });

        document.addEventListener("mouseup", () => {
            if (!dragging) return;
            dragging = false;
            document.body.style.cursor = "";
            document.body.style.userSelect = "";
            handle.classList.remove("split-handle-active");
            resizePlots();
        });

        // Also reflow on window resize so the proportions stay sane when
        // the viewport itself changes size.
        window.addEventListener("resize", resizePlots);
        return true;
    }

    function init() {
        // Dash renders asynchronously — panels may not be wired up yet on
        // the first fire. Wire whatever's present; retry a bounded number of
        // times for any still-missing splitter (the Window-N one is absent
        // entirely in non-admin mode, so we stop after a few tries rather
        // than spin forever).
        let attempts = 0;
        function attempt() {
            const allWired = SPLITTERS.every((cfg) => wire(cfg));
            if (allWired || attempts > 20) return;
            attempts += 1;
            setTimeout(attempt, 150);
        }
        attempt();
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
