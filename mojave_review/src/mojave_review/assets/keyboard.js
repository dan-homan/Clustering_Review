// Keyboard shortcuts for the overlay panel.
//
// Left  arrow → click #epoch-prev (previous epoch)
// Right arrow → click #epoch-next (next epoch)
//
// Skipped when focus is in a text input / textarea / contenteditable element
// so the arrows don't fight with typing in the recommendations panel.

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
        if (inEditableTarget(e.target)) return;

        let btnId = null;
        if (e.key === "ArrowLeft") btnId = "epoch-prev";
        else if (e.key === "ArrowRight") btnId = "epoch-next";
        else return;

        const btn = document.getElementById(btnId);
        if (!btn) return;
        // Don't act if the overlay panel isn't visible (e.g. on initial load
        // before a source has been picked); button still exists but a click
        // would be a no-op anyway.
        btn.click();
        e.preventDefault();
    });
})();
