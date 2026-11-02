"""Switching the model must NOT reset the plots' zoom.

Switching `current` ⇄ `backup_NNN` ⇄ `alt_model_NNN` ⇄ `Rec: <slug>` is how a
reviewer compares models, and that comparison is only meaningful at a fixed
zoom — every model under one source covers the same source over the same
epochs, so the axes mean the same thing. `model_key` used to be part of both
figures' `uirevision`, so every switch handed Plotly a fresh key and the view
snapped back to the default extent (reviewer request 2026-11-02).

The key must still change on source and view (different data domain / axis
identities) and on the Reset-view counters (the explicit flush).
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from mojave_review.app import create_app


SOURCE_FOLDER = "0003-066u_1994.00-2026.00"
PREFIX = "0003-066u.1994.00-2026.00"


def _write_model(csv_path):
    """Six epochs × (core + one cluster) — enough for a Position figure."""
    rows = []
    for i in range(6):
        ep = 2000.0 + i
        for cid in (0, 1):
            rows.append(dict(
                source="0003-066u", band="u", ep_name=f"{2000 + i}_01_01",
                epoch=ep, clusterID=cid, origID=cid,
                avg_x=1.0 * cid + 0.1 * i, avg_y=0.5 * cid,
                core_x=0.0, core_y=0.0,
                fwhm_maj=0.5, fwhm_min=0.4, iflux=1.0, pflux=0.1, evpa=10.0,
                robust=True, use_in_fit=True, select=False,
                bmaj=1.0, bmin=0.8, bpa=0.0, inoise=1e-4, pnoise=1e-4,
            ))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(csv_path, index=False)


@pytest.fixture(scope="module")
def client(tmp_path_factory):
    root = tmp_path_factory.mktemp("uirev")
    results = root / "Results"
    folder = results / SOURCE_FOLDER
    _write_model(folder / f"{PREFIX}.merged_win_results.csv")
    # A backup gives us a second model to switch to. No npz anywhere, so the
    # overlay renders its placeholder — which is itself part of the contract:
    # a figure with nothing to draw must still carry the key.
    _write_model(folder / "backups" / "backup_001_merged_win_results.csv")
    app = create_app(
        results_dir=results,
        recommendations_dir=root / "recommendations",
        cache_dir=root / "cache",
        reviewer="Test Reviewer",
    )
    return app, app.server.test_client(), str(folder.resolve())


def _uirevision(client, output_key, values):
    """POST the callback that owns ``output_key``, building its input payload
    from the registered spec so this test survives new inputs being added."""
    app, http, _folder = client
    # The registered map key is what the endpoint dispatches on — for a
    # multi-output callback that's "..a.prop...b.prop..", not our search string.
    map_key, spec = next((key, cb) for key, cb in app.callback_map.items()
                         if output_key in key)
    inputs = []
    for i in spec["inputs"]:
        cid = i["id"]
        assert cid in values, f"test needs a value for input {cid}.{i['property']}"
        inputs.append({"id": cid, "property": i["property"],
                       "value": values[cid]})
    # Dash stores a single Output bare, a multi-output one as a list.
    spec_out = spec["output"]
    if not isinstance(spec_out, (list, tuple)):
        spec_out = [spec_out]
    outputs = [{"id": o.component_id, "property": o.component_property}
               for o in spec_out]
    r = http.post("/_dash-update-component", json={
        "output": map_key,
        "outputs": outputs if len(outputs) > 1 else outputs[0],
        "inputs": inputs,
        "changedPropIds": ["model-picker.value"],
    })
    assert r.status_code == 200, r.data[:400]
    body = r.get_json()["response"]
    graph = output_key.strip(".").split(".")[0]
    return body[graph]["figure"]["layout"].get("uirevision")


def _base(folder, **over):
    """Every input any of the three figure callbacks takes, at a resting value."""
    v = {
        "source-picker": folder,
        "model-picker": "current",
        "view-picker": "Position",
        "right-pane-mode": "Position",
        "epoch-slider": 0,
        "vector-scale": 1.0,
        "selection-store": None,
        "visualize-checkbox": [],
        "cluster-feedback-table": None,
        "edits-store": None,
        "no-changes-checkbox": [],
        "hide-non-robust-checkbox": [],
        "only-3sigma-checkbox": [],
        "use-fits-checkbox": [],
        "stack-image-checkbox": [],
        "astrometry-checkbox": [],
        "reload-counter": 0,
        "agg-preview-rec": None,
        "summary-reset-counter": 0,
        "overlay-reset-counter": 0,
    }
    v.update(over)
    return v


_GRAPHS = ["summary-graph.figure", "summary-graph-right.figure",
           "overlay-graph.figure"]


@pytest.mark.parametrize("output_key", _GRAPHS)
def test_model_switch_keeps_the_key(client, output_key):
    _app, _http, folder = client
    keys = {m: _uirevision(client, output_key, _base(folder, **{"model-picker": m}))
            for m in ("current", "backup_001")}
    assert keys["current"] is not None
    assert keys["current"] == keys["backup_001"], keys
    # And the model name must not be hiding in there some other way.
    assert "backup_001" not in keys["backup_001"]


@pytest.mark.parametrize("output_key", _GRAPHS[:2])
def test_view_change_still_resets(client, output_key):
    """Position and Kinematics don't share axis meanings — a fresh key there is
    correct, and the summary panes are the two that have a view selector."""
    _app, _http, folder = client
    picker = ("view-picker" if output_key.startswith("summary-graph.")
              else "right-pane-mode")
    a = _uirevision(client, output_key, _base(folder, **{picker: "Position"}))
    b = _uirevision(client, output_key, _base(folder, **{picker: "Kinematics"}))
    assert a != b


@pytest.mark.parametrize("output_key,counter", [
    ("summary-graph.figure", "summary-reset-counter"),
    ("summary-graph-right.figure", "summary-reset-counter"),
    ("overlay-graph.figure", "overlay-reset-counter"),
])
def test_reset_view_still_flushes(client, output_key, counter):
    _app, _http, folder = client
    a = _uirevision(client, output_key, _base(folder, **{counter: 0}))
    b = _uirevision(client, output_key, _base(folder, **{counter: 1}))
    assert a != b


def test_left_and_right_panes_keep_separate_keys(client):
    _app, _http, folder = client
    left = _uirevision(client, "summary-graph.figure", _base(folder))
    right = _uirevision(client, "summary-graph-right.figure", _base(folder))
    assert left != right
