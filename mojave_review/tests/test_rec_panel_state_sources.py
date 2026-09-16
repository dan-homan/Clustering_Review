"""The Robustness/Epoch feedback stores are written by *separate* bridge
callbacks, and Dash's ready-callback ordering only respects Input edges — a
callback's State is never waited on. So any callback that needs the reviewer's
current feedback must read the LIVE components, not the stores.

Regression: `_resync_table_with_effective` merged against
`cluster-feedback-table.data`, which on a source load still held the previous
source's rows (nothing at all on a fresh page load). Every saved
robust/non-robust pick was dropped from the rebuilt radios, and `_autosave`
then wrote that wiped panel back over `current/<slug>.json` — the reviewer's
robustness recommendations silently reset to "everything eligible is robust".
"""

from __future__ import annotations

import dash
import pytest

from mojave_review.ui import recommendations_callbacks as rc


_RADIO_VALUE = "{\"cid\":[\"ALL\"],\"cur\":[\"ALL\"],\"type\":\"robust-radio\"}.value"
_CLUSTER_COMMENT = "{\"cid\":[\"ALL\"],\"type\":\"cluster-comment\"}.value"
_EPOCH_COMMENT = "{\"epoch\":[\"ALL\"],\"type\":\"epoch-comment\"}.value"


@pytest.fixture(scope="module")
def callback_map(tmp_path_factory):
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    root = tmp_path_factory.mktemp("recs")
    rc.register(
        app,
        results_dir=root / "Results",
        recommendations_dir=root / "recommendations",
        reviewer="Test Reviewer",
    )
    return app.callback_map


def _spec(callback_map, output_id, output_prop):
    """The callback whose FIRST output is (output_id, output_prop)."""
    want = f"{output_id}.{output_prop}"
    hits = [cb for key, cb in callback_map.items()
            if key.split("...")[0].startswith(want)]
    assert hits, f"no callback outputs {want}"
    return hits


def _state_ids(spec):
    return {s["id"] if isinstance(s["id"], str) else str(s["id"])
            for s in spec["state"]}


def _state_keys(spec):
    out = set()
    for s in spec["state"]:
        if isinstance(s["id"], str):
            out.add(f"{s['id']}.{s['property']}")
        else:
            # Pattern-matching id: dash stringifies it with sorted keys.
            import json
            key = json.dumps(s["id"], sort_keys=True, separators=(",", ":"))
            out.add(f"{key}.{s['property']}")
    return out


def test_resync_reads_live_radios_not_the_store(callback_map):
    specs = _spec(callback_map, "cluster-feedback-rows", "children")
    # The resync callback is the one triggered by the visualize checkbox.
    resync = [s for s in specs
              if any(i["id"] == "visualize-checkbox" for i in s["inputs"])]
    assert len(resync) == 1
    keys = _state_keys(resync[0])
    assert _RADIO_VALUE in keys
    assert _CLUSTER_COMMENT in keys
    # The lagging store must NOT be the baseline any more.
    assert "cluster-feedback-table.data" not in keys


def test_autosave_reads_live_components_not_the_stores(callback_map):
    specs = _spec(callback_map, "save-indicator", "children")
    autosave = [s for s in specs
                if any(i["id"] == "cluster-feedback-table" for i in s["inputs"])]
    assert len(autosave) == 1
    keys = _state_keys(autosave[0])
    assert _RADIO_VALUE in keys
    assert _CLUSTER_COMMENT in keys
    assert _EPOCH_COMMENT in keys
    # The stores stay as Inputs (they are the save trigger) but must not be
    # read as State — their payload is what went stale.
    assert "cluster-feedback-table.data" not in keys
    assert "epoch-feedback-table.data" not in keys
