"""Tests for the absolute astrometric reference (data/astrometry.py).

Covers the parser (including the sign traps in the fixed-layout file), the
mean-position / correction arithmetic, the sign convention of the applied
reference, and the propagation into the 1-sigma position bars.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from mojave_review.data.astrometry import (
    apply_astrometric_reference, astrometry_for, find_astrometry_file,
    load_astrometry,
)
from mojave_review.plots.uncertainty import compute_position_uncertainties


# One line of the real file layout, parameterised so a test can move the
# source around the sky. Column positions don't matter (we split on
# whitespace), but the token ORDER does.
def _line(src, date, epoch, rh, rm, rs, ra_err, dsign, dd, dm, ds, dec_err):
    return (f"{src:<20s}  {date} h    4   EPOCH:  {epoch:.5f}  "
            f"R:  {rh:02d} {rm:02d} {rs:.8f} -+     {ra_err:.4f}  "
            f"D:  {dsign}{dd:02d} {dm:02d} {ds:.7f} -+     {dec_err:.4f} "
            f"Corr: -0.0210 Obs:   151 /   157 Sca:   0 /   0\n")


@pytest.fixture()
def dat(tmp_path):
    """Two sources: one north, one just south of the celestial equator (the
    ``-00`` degrees case, where float() alone loses the sign)."""
    p = tmp_path / "astrometry_mojave.dat"
    p.write_text(
        # 0003+380: 3 epochs. Dec 38 deg -> cos(dec) ~ 0.788.
        _line("0003+380", "20200101", 2020.00273, 0, 5, 57.000000, 0.05,
              "+", 38, 0, 0.0000000, 0.06)
        + _line("0003+380", "20210101", 2021.00274, 0, 5, 57.000100, 0.05,
                "+", 38, 0, 0.3000000, 0.06)
        + _line("0003+380", "20220101", 2022.00274, 0, 5, 57.000200, 0.05,
                "+", 38, 0, 0.6000000, 0.06)
        # 0010-002: 2 epochs, dec -0 deg 30' -> the negative-zero trap.
        + _line("0010-002", "20200101", 2020.00273, 0, 10, 0.000000, 0.10,
                "-", 0, 30, 0.0000000, 0.12)
        + _line("0010-002", "20200701", 2020.50137, 0, 10, 0.000000, 0.10,
                "-", 0, 30, 0.2000000, 0.12)
    )
    return p


def test_parse_and_mean(dat):
    table = load_astrometry(dat)
    assert set(table) == {"0003+380", "0010-002"}

    sa = table["0003+380"]
    assert sa.n_epochs == 3
    assert sorted(sa.epochs) == ["2020_01_01", "2021_01_01", "2022_01_01"]
    # Mean of three evenly spaced positions = the middle one, so its
    # correction is zero and the outer two are symmetric about it.
    mid = sa.epochs["2021_01_01"]
    assert mid.dx == pytest.approx(0.0, abs=1e-6)
    assert mid.dy == pytest.approx(0.0, abs=1e-6)
    first, last = sa.epochs["2020_01_01"], sa.epochs["2022_01_01"]
    assert first.dy == pytest.approx(-last.dy)
    assert first.dx == pytest.approx(-last.dx)

    # Dec: 0.3" of arc from the mean -> 300 mas, no cos(dec) factor.
    assert last.dy == pytest.approx(300.0, rel=1e-6)
    # RA: 1e-4 s of TIME from the mean -> 1e-4 * 15 * 1000 mas of coordinate
    # angle, times cos(dec) for the true angle.
    expect_dx = 1e-4 * 15.0 * 1000.0 * np.cos(np.deg2rad(38.0))
    assert last.dx == pytest.approx(expect_dx, rel=1e-4)


def test_dec_sign_survives_negative_zero_degrees(dat):
    """``D: -00 30 00`` is half a degree SOUTH; float("-00") == -0.0 drops the
    sign, which would put the source in the northern hemisphere."""
    sa = load_astrometry(dat)["0010-002"]
    assert sa.dec_mean_deg < 0
    assert sa.dec_mean_deg == pytest.approx(-(30.0 + 0.1 / 60.0) / 60.0, rel=1e-12)


def test_ra_errors_get_the_cos_dec_factor(dat):
    """The file's RA error is a coordinate angle (confirmed empirically: at
    dec +81.6 it runs ~6.5x the Dec error, and the ratio collapses to ~1 once
    scaled), so it must be multiplied by cos(dec) to become a true angle."""
    e = load_astrometry(dat)["0003+380"].epochs["2021_01_01"]
    # cos() is taken at the MEAN declination, a hair off 38 deg exactly.
    assert e.sig_x == pytest.approx(0.05 * np.cos(np.deg2rad(38.0)), rel=1e-4)
    assert e.sig_y == pytest.approx(0.06, rel=1e-12)


def test_band_stripped_lookup(dat):
    table = load_astrometry(dat)
    assert astrometry_for(table, "0003+380u") is table["0003+380"]
    assert astrometry_for(table, "9999+999u") is None


def test_malformed_and_missing_are_tolerated(tmp_path):
    assert load_astrometry(None) == {}
    assert load_astrometry(tmp_path / "nope.dat") == {}
    p = tmp_path / "junk.dat"
    p.write_text("# a comment\n\nnot a data line at all\n"
                 + _line("0003+380", "20200101", 2020.0, 0, 5, 57.0, 0.05,
                         "+", 38, 0, 0.0, 0.06))
    assert list(load_astrometry(p)) == ["0003+380"]


def test_find_file_search_order(tmp_path, monkeypatch):
    results = tmp_path / "data" / "Results"
    results.mkdir(parents=True)
    monkeypatch.chdir(tmp_path)
    assert find_astrometry_file(results) is None
    # parent of results_dir
    (results.parent / "astrometry_mojave.dat").write_text("")
    assert find_astrometry_file(results) == results.parent / "astrometry_mojave.dat"
    # cwd wins
    (tmp_path / "astrometry_mojave.dat").write_text("")
    assert find_astrometry_file(results) == tmp_path / "astrometry_mojave.dat"


# ---------------------------------------------------------------------------
# Applying the reference
# ---------------------------------------------------------------------------


def _cluster_df():
    """Two epochs x (core + one cluster). Only the second epoch will have an
    astrometric position. The core sits off map-centre so a wrong sign on the
    correction can't hide."""
    rows = []
    for ep_name, epoch, core in (("2019_01_01", 2019.0027, (1.0, -2.0)),
                                 ("2021_01_01", 2021.0027, (1.5, -2.5))):
        for cid, avg in ((0, core), (1, (core[0] + 5.0, core[1] + 1.0))):
            rows.append({
                "ep_name": ep_name, "epoch": epoch, "clusterID": cid,
                "avg_x": avg[0], "avg_y": avg[1],
                "core_x": core[0], "core_y": core[1],
                "robust": True, "use_in_fit": True, "iflux": 1.0,
                "origID": cid, "fwhm_maj": 1.0, "fwhm_min": 1.0,
            })
    return pd.DataFrame(rows)


def test_reference_sign_and_partial_coverage(dat):
    """Origin = the MEAN astrometric position (fixed on the sky), so the core
    plots at +corr(e) and every feature's offset shifts by the same +corr(e).
    Epochs with no astrometric position keep the plain core reference."""
    sa = load_astrometry(dat)["0003+380"]
    corr = sa.epochs["2021_01_01"]
    # Make the 2021 correction non-zero so the test has something to see
    # (the fixture's middle epoch IS the mean). Shift it by hand.
    from dataclasses import replace
    sa = replace(sa, epochs={**sa.epochs,
                             "2021_01_01": replace(corr, dx=0.3, dy=-0.4)})

    df = _cluster_df()
    out, info = apply_astrometric_reference(df, sa)
    assert (info.available, info.n_astro, info.n_matched, info.n_epochs) \
        == (True, 3, 1, 2)

    off = out["avg_x"] - out["core_x"], out["avg_y"] - out["core_y"]
    # 2019: no astrometry -> unchanged (core exactly at the origin).
    e19 = out["ep_name"] == "2019_01_01"
    assert off[0][e19].tolist() == [0.0, 5.0]
    assert off[1][e19].tolist() == [0.0, 1.0]
    # 2021: every offset gains +corr; the core itself lands ON +corr.
    e21 = out["ep_name"] == "2021_01_01"
    assert off[0][e21].to_numpy() == pytest.approx([0.3, 5.3])
    assert off[1][e21].to_numpy() == pytest.approx([-0.4, 0.6])
    # The input frame is never mutated in place.
    assert df["core_x"].tolist() == [1.0, 1.0, 1.5, 1.5]


def test_error_columns_only_where_covered(dat):
    sa = load_astrometry(dat)["0003+380"]
    out, _ = apply_astrometric_reference(_cluster_df(), sa)
    e19 = out["ep_name"] == "2019_01_01"
    assert out.loc[e19, "astro_sig_x"].isna().all()
    e21 = out["ep_name"] == "2021_01_01"
    assert (out.loc[e21, "astro_sig_x"] > 0).all()
    assert out.loc[e21, "astro_sig_y"].to_numpy() == pytest.approx(0.06)


def test_noop_without_astrometry_or_epoch_names(dat):
    df = _cluster_df()
    out, info = apply_astrometric_reference(df, None)
    assert not info.available and info.n_matched == 0
    pd.testing.assert_frame_equal(out, df)

    # A frame with no ep_name (window fits, the XVIII table) can't join.
    sa = load_astrometry(dat)["0003+380"]
    out, info = apply_astrometric_reference(df.drop(columns=["ep_name"]), sa)
    assert info.available and info.n_matched == 0
    assert "astro_sig_x" not in out.columns


# ---------------------------------------------------------------------------
# Error-bar propagation
# ---------------------------------------------------------------------------


def _cc_inputs(df):
    """Two Stokes-I CCs per (epoch, cluster), spread so the weighted SE of the
    centroid is finite and identical in both epochs."""
    cc_dtype = np.dtype([("epoch", "f8"), ("x", "f8"), ("y", "f8"),
                         ("stokes", "U1"), ("flux", "f8")])
    recs, labels = [], []
    for _, r in df.iterrows():
        for sx, sy in ((-0.1, -0.1), (0.1, 0.1)):
            recs.append((r["epoch"], r["avg_x"] + sx, r["avg_y"] + sy, "i", 1.0))
            labels.append(int(r["origID"]))
    return np.array(recs, dtype=cc_dtype), np.array(labels, dtype=np.int32)


def test_astrometric_error_adds_in_quadrature(dat):
    sa = load_astrometry(dat)["0003+380"]
    df = _cluster_df()
    cc, labels = _cc_inputs(df)
    base = compute_position_uncertainties(df, cc, labels)

    out, _ = apply_astrometric_reference(df, sa)
    withastro = compute_position_uncertainties(out, cc, labels)

    e = sa.epochs["2021_01_01"]
    # Covered epoch: sig grows to hypot(CC-only, astrometric).
    key = (round(2021.0027, 4), 1)
    assert withastro[key][0] == pytest.approx(np.hypot(base[key][0], e.sig_x))
    assert withastro[key][1] == pytest.approx(np.hypot(base[key][1], e.sig_y))
    # Uncovered epoch: unchanged — a NaN astrometric error must not poison the
    # bars (np.hypot would propagate it).
    key19 = (round(2019.0027, 4), 1)
    assert withastro[key19][0] == pytest.approx(base[key19][0])
    assert np.isfinite(withastro[key19][0])


# ---------------------------------------------------------------------------
# The stacked contour background must follow the rendered reference frame
# ---------------------------------------------------------------------------


def test_core_reference_key_tracks_the_frame(dat):
    """``_stacked_axes_for_bundle`` is an lru_cache keyed partly on this: the
    stacked image shifts every epoch's CCs to that epoch's core, so a stack
    built in the core frame must never be served for the astrometric one (that
    left the contour background behind while the axes and CCs moved)."""
    from mojave_review.plots.overlay import core_reference_key

    df = _cluster_df()
    sa = load_astrometry(dat)["0003+380"]
    shifted, info = apply_astrometric_reference(df, sa)
    assert info.n_matched == 1

    k_core = core_reference_key(df)
    k_astro = core_reference_key(shifted)
    assert len(k_core) == len(k_astro) == 2       # one entry per epoch
    assert k_core != k_astro
    # Only the covered epoch moved; the uncovered one is byte-identical.
    assert k_core[0] == k_astro[0]                # 2019 — no astrometry
    assert k_core[1] != k_astro[1]                # 2021 — re-referenced
    # Idempotent (so the cache actually hits).
    assert core_reference_key(shifted) == k_astro


# ---------------------------------------------------------------------------
# Callback wiring: every figure that shows positions must react to the toggle
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def callback_map(tmp_path_factory):
    import dash
    from mojave_review.ui.callbacks import register_callbacks

    root = tmp_path_factory.mktemp("astro-app")
    (root / "Results").mkdir()
    app = dash.Dash(__name__, suppress_callback_exceptions=True)
    register_callbacks(
        app,
        results_dir=root / "Results",
        recommendations_dir=root / "recommendations",
        cache_dir=root / "cache",
        reviewer="Test Reviewer",
        admin=True,
    )
    return app.callback_map


@pytest.mark.parametrize("output", [
    "summary-graph.figure",
    "summary-graph-right.figure",
    "overlay-graph.figure",
])
def test_toggle_is_an_input_on_every_position_figure(callback_map, output):
    # Multi-output callbacks are keyed "..a.prop...b.prop..", single ones
    # just "a.prop" — a containment test covers both.
    specs = [cb for key, cb in callback_map.items() if output in key]
    assert specs, f"no callback outputs {output}"
    inputs = {f"{i['id']}.{i['property']}" for s in specs for i in s["inputs"]
              if isinstance(i["id"], str)}
    # Input, not State: the figure has to rebuild the moment the box is ticked.
    assert "astrometry-checkbox.value" in inputs


def test_status_callback_reports_coverage(callback_map):
    keys = [k for k in callback_map if "astrometry-checkbox.options" in k]
    assert keys, "no callback drives the checkbox options"
    spec = callback_map[keys[0]]
    outs = {f"{o.component_id}.{o.component_property}" for o in spec["output"]}
    assert outs == {"astrometry-checkbox.options",
                    "astrometry-status.children"}
