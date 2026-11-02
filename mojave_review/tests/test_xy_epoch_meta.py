"""The Position view's XY panel is epoch-linked in the BROWSER
(assets/xy_epoch.js): squares on the active epoch's points, and points outside
the epoch range visible in the panel above are hidden. The script is stateless
and reads everything it needs off each trace's ``meta``, so this test pins the
server-side half of that contract — the payload shape and the highlight
placeholder. Break it and the linkage silently stops working (no error, the
script just finds no traces to act on).
"""

from __future__ import annotations

import json

import numpy as np
import pandas as pd
import pytest

from mojave_review.plots.summary import XY_EPOCH_COLOR, build_summary_figure


def _df() -> pd.DataFrame:
    """Two clusters over four epochs; cluster 2 has one excluded point and one
    selected point, so the slash / halo overlay traces exist too."""
    rows = []
    epochs = [2000.0, 2001.0, 2002.0, 2003.0]
    for cid in (1, 2):
        for i, ep in enumerate(epochs):
            rows.append(dict(
                clusterID=cid, epoch=ep, ep_name=f"{int(ep)}_01_01",
                avg_x=0.5 * cid + 0.1 * i, avg_y=0.2 * cid + 0.05 * i,
                core_x=0.0, core_y=0.0, fwhm_maj=0.5, fwhm_min=0.4,
                iflux=1.0, pflux=0.1, evpa=10.0, robust=True,
                use_in_fit=not (cid == 2 and i == 1),
                select=(cid == 2 and i == 2),
                sig_dx=0.01, sig_dy=0.01, sig_dist=0.01, sig_pa=1.0,
            ))
    return pd.DataFrame(rows)


def _xy_traces(fig):
    return [t for t in fig.data if isinstance(getattr(t, "meta", None), dict)
            and "xy" in t.meta]


def test_xy_traces_carry_epochs_and_unmasked_positions():
    df = _df()
    fig = build_summary_figure(df, view="Position")
    traces = _xy_traces(fig)
    kinds = [t.meta["xy"] for t in traces]

    # one track per cluster, the slash + halo overlays, one highlight
    assert kinds.count("track") == 2
    assert kinds.count("overlay") == 2
    assert kinds.count("highlight") == 1

    for t in traces:
        if t.meta["xy"] == "highlight":
            continue
        n = len(t.x)
        assert len(t.meta["ep"]) == n            # parallel to the drawn arrays
        assert len(t.meta["x0"]) == n
        assert len(t.meta["y0"]) == n
        assert np.allclose(t.meta["x0"], t.x)    # originals == what's drawn now
        assert np.allclose(t.meta["y0"], t.y)

    track = next(t for t in traces if t.meta["xy"] == "track")
    assert track.meta["ep"] == [2000.0, 2001.0, 2002.0, 2003.0]


def test_highlight_trace_is_an_empty_cyan_square_placeholder():
    fig = build_summary_figure(_df(), view="Position")
    hl = next(t for t in _xy_traces(fig) if t.meta["xy"] == "highlight")
    assert len(hl.x) == 0 and len(hl.y) == 0     # the script fills it in
    assert hl.marker.symbol == "square-open"
    assert hl.marker.color == XY_EPOCH_COLOR
    assert hl.showlegend is False                # never a legend entry


def test_meta_survives_json_as_plain_numbers():
    # plotly 6 base64-encodes numpy arrays; meta must be plain JSON the script
    # can index (the same hazard as customdata), and NaN positions must come
    # through as null rather than a NaN literal.
    df = _df()
    df.loc[df.index[-1], ["avg_x", "avg_y"]] = np.nan
    payload = json.loads(build_summary_figure(df, view="Position").to_json())
    metas = [t["meta"] for t in payload["data"]
             if isinstance(t.get("meta"), dict) and "xy" in t["meta"]]
    assert metas
    for m in metas:
        if m["xy"] == "highlight":
            continue
        assert all(isinstance(v, float) for v in m["ep"])
        assert all(v is None or isinstance(v, float) for v in m["x0"])
    assert any(None in m["x0"] for m in metas if m["xy"] == "track")


def test_other_views_have_no_xy_payload():
    # Only the XY panel is epoch-linked; the script keys on these traces, and
    # Kinematics' bottom panel is also an X/Y mas plot (must NOT be filtered).
    for view in ("Position Angle", "Flux", "Polarization", "Kinematics"):
        fig = build_summary_figure(_df(), view=view)
        assert not _xy_traces(fig), view


# ---------------------------------------------------------------------------
# The core in the XY panel
# ---------------------------------------------------------------------------


def _df_with_core(core_offsets) -> pd.DataFrame:
    """``_df()`` plus cluster 0, whose plotted offset per epoch is taken from
    ``core_offsets`` (a list of (dx, dy)). The offset comes from moving
    ``core_x``/``core_y`` away from ``avg_x``/``avg_y``, which is exactly what
    a reference shift does — the core's own avg is always its fitted position.
    """
    rows = list(_df().to_dict("records"))
    for i, (dx, dy) in enumerate(core_offsets):
        ep = 2000.0 + i
        rows.append(dict(
            clusterID=0, epoch=ep, ep_name=f"{int(ep)}_01_01",
            avg_x=0.0, avg_y=0.0, core_x=-dx, core_y=-dy,
            fwhm_maj=0.5, fwhm_min=0.4, iflux=2.0, pflux=0.1, evpa=10.0,
            robust=True, use_in_fit=True, select=False,
            sig_dx=0.01, sig_dy=0.01, sig_dist=0.01, sig_pa=1.0,
        ))
    return pd.DataFrame(rows)


def _track_cids(fig):
    """clusterIDs with a track trace in the XY panel, read off customdata."""
    out = []
    for t in _xy_traces(fig):
        if t.meta["xy"] == "track" and getattr(t, "customdata", None):
            out.append(int(t.customdata[0][0]))
    return sorted(out)


def test_core_omitted_from_xy_when_it_sits_on_the_origin():
    """With the plain core reference the core's track IS the origin, so drawing
    it would pile one marker per epoch under the origin ×."""
    fig = build_summary_figure(_df_with_core([(0.0, 0.0)] * 4), view="Position")
    assert _track_cids(fig) == [1, 2]
    # It stays in the distance panel above (a flat zero line) as before.
    assert any(t.yaxis in (None, "y") and getattr(t, "customdata", None)
               and int(t.customdata[0][0]) == 0 for t in fig.data)


def test_core_drawn_in_xy_once_it_moves_off_the_origin():
    """The astrometric reference makes the origin a fixed sky position, so the
    core acquires a real track — which is worth plotting."""
    offs = [(0.0, 0.0), (0.0, 0.0), (0.1, -0.2), (0.05, 0.3)]
    fig = build_summary_figure(_df_with_core(offs), view="Position")
    assert _track_cids(fig) == [0, 1, 2]
    core = next(t for t in _xy_traces(fig)
                if t.meta["xy"] == "track" and getattr(t, "customdata", None)
                and int(t.customdata[0][0]) == 0)
    # Every epoch is kept, zero-offset ones included: in those the reference
    # genuinely IS the core, and a gap would misjoin the dotted connector.
    assert len(core.x) == 4
    assert core.x[2] == pytest.approx(0.1)
    assert core.y[2] == pytest.approx(-0.2)
    # And it rides the browser-side epoch linkage like any other track.
    assert core.meta["ep"] == [2000.0, 2001.0, 2002.0, 2003.0]


def test_core_not_drawn_for_a_sub_micro_mas_wobble():
    """Guard against float noise promoting a zero track (avg_x IS core_x for
    the core, equal to ~1e-16, not exactly)."""
    fig = build_summary_figure(
        _df_with_core([(1e-16, -1e-16)] * 4), view="Position")
    assert _track_cids(fig) == [1, 2]
