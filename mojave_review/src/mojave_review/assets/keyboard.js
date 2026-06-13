// Keyboard shortcuts for the overlay panel.
//
// Left  arrow → click #epoch-prev (previous epoch)
// Right arrow → click #epoch-next (next epoch)
//
// When the admin Window-N review panel (#nwin-details) is OPEN, the arrows
// route there instead — matplotlib N_win_edit parity:
//   Left/Right → #nwin-win-prev / #nwin-win-next  (previous/next time window)
//   Up/Down    → #nwin-n-up   / #nwin-n-down      (cluster count N ± 1)
//   r          → #nwin-record-btn                 (record N for this window)
// Close the panel to give Left/Right back to the main epoch overlay.
// (Up/Down and r are only claimed while the panel is open, so normal page
// scrolling / typing is unaffected the rest of the time.)
//
// Skipped when focus is in a text input / textarea / contenteditable element
// so the arrows don't fight with typing in the recommendations panel.
//
// IMPORTANT — capture phase + stopImmediatePropagation:
// The epoch control is a dcc.Slider whose focusable handle ALSO handles arrow
// keys natively (rc-slider moves it one step). If this listener ran in the
// normal (bubble) phase, then whenever the slider handle had focus BOTH the
// slider's native handler AND this handler would fire for one keypress, so the
// epoch advanced TWO steps per press. (After clicking a button / Reset view,
// focus moved off the handle, so only this handler fired and it behaved — the
// intermittent "skips epochs" symptom.) Worse, the slider uses
// updatemode="mouseup", so the native keyboard move doesn't reliably push its
// value to Dash, leaving the handle position out of sync with the displayed
// epoch/date ("dates underneath the timeline not always correct").
//
// Running in the CAPTURE phase and calling stopImmediatePropagation()
// intercepts the arrow key before it reaches the slider's handler, so EVERY
// arrow press routes through exactly one path — the prev/next button →
// _step_epoch — which is the single source of truth for the epoch value. The
// handle then tracks the Dash value, staying in sync.

(function () {
    function inEditableTarget(el) {
        if (!el) return false;
        const tag = (el.tagName || "").toLowerCase();
        if (tag === "input" || tag === "textarea" || tag === "select") return true;
        if (el.isContentEditable) return true;
        return false;
    }

    document.addEventListener("keydown", (e) => {
        if (e.altKey || e.ctrlKey || e.metaKey || e.shiftKey) return;
        // Let arrows do their normal job inside text fields (cursor movement,
        // editing recommendation comments, etc.).
        if (inEditableTarget(e.target)) return;

        const nwin = document.getElementById("nwin-details");
        const nwinOpen = !!(nwin && nwin.open);

        let btnId = null;
        if (e.key === "ArrowLeft") btnId = nwinOpen ? "nwin-win-prev" : "epoch-prev";
        else if (e.key === "ArrowRight") btnId = nwinOpen ? "nwin-win-next" : "epoch-next";
        else if (e.key === "ArrowUp" && nwinOpen) btnId = "nwin-n-up";
        else if (e.key === "ArrowDown" && nwinOpen) btnId = "nwin-n-down";
        else if (e.key === "r" && nwinOpen) btnId = "nwin-record-btn";
        else return;

        const btn = document.getElementById(btnId);
        if (!btn) return;

        // Block the slider's native arrow handling (and any other listener) so
        // ONLY the button path advances the epoch — exactly one step per press.
        e.stopImmediatePropagation();
        e.preventDefault();
        btn.click();
    }, true);  // <-- capture phase: fires before the slider's own handler
})();
