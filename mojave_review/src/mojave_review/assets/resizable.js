// Draggable vertical splitter between the summary-plots panel and the
// FITS-overlay panel. Adjusts the two panels' `flex` widths on mouse
// drag and calls Plotly.Plots.resize() on every chart inside them so
// the figures reflow live.
//
// Dash auto-loads any .js file under the configured `assets_folder`
// (see app.py — points at the installed package's assets/ dir).

(function () {
    function init() {
        const handle = document.getElementById("split-handle");
        const left = document.getElementById("summary-panel");
        const right = document.getElementById("overlay-panel");

        // Dash renders asynchronously — the body may not be wired up yet
        // on the first DOMContentLoaded fire. Retry until the elements
        // exist (a few hundred ms is enough on a cold load).
        if (!handle || !left || !right) {
            setTimeout(init, 150);
            return;
        }

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
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", init);
    } else {
        init();
    }
})();
