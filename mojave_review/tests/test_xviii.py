"""Tests for the MOJAVE Paper XVIII table adapter (data/xviii.py)."""

from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from mojave_review.data import xviii


def test_load_table_parses_bundled_mrt():
    tbl = xviii.load_xviii_table()
    assert not tbl.empty
    # A known core row: 0003+380, feature 0, 2006 Mar 9, 489 mJy.
    core = tbl[(tbl["source"] == "0003+380") & (tbl["F"] == 0)
               & (tbl["epoch"].astype(int) == 2006)].iloc[0]
    assert core["I_mJy"] == 489.0
    assert core["MajAxis"] == 0.23
    assert core["Robust"] == "Y"


def test_sources_include_known_ids():
    srcs = xviii.xviii_sources()
    assert "0003+380" in srcs
    assert "0003-066" in srcs


def _fake_bundle(epochs, core_xy=(0.0, 0.0)):
    """Minimal bundle stub: epoch_info structured array + a core cluster_df."""
    ei = np.zeros(len(epochs), dtype=[("epoch_val", "f8"),
                                      ("epoch_name", "U16"), ("bmaj", "f8")])
    rows = []
    for i, (ev, name) in enumerate(epochs):
        ei["epoch_val"][i] = ev
        ei["epoch_name"][i] = name
        ei["bmaj"][i] = 0.5
        rows.append({"clusterID": 0, "epoch": ev,
                     "core_x": core_xy[0], "core_y": core_xy[1]})
    cdf = pd.DataFrame(rows)
    return SimpleNamespace(plotdata=SimpleNamespace(epoch_info=ei),
                           cluster_df=cdf)


def test_build_df_reconstructs_position_and_flux():
    # Clustering core is only a fallback here — 0003+380 has a real XVIII core
    # row, so the summary frame references the XVIII core (X0), not (1, -2).
    bundle = _fake_bundle([(2006.1834, "2006_03_09")], core_xy=(1.0, -2.0))
    df = xviii.build_xviii_cluster_df("0003+380", "u", bundle)
    assert not df.empty
    # Feature 2 @ 2006 Mar 9: r=1.25 mas, PA=110.5 deg, I=42.1 mJy — the
    # summary frame (avg − core) reproduces XVIII's core-relative r, PA exactly.
    row = df[df["clusterID"] == 2].iloc[0]
    dx = row["avg_x"] - row["core_x"]
    dy = row["avg_y"] - row["core_y"]
    assert np.isclose(np.hypot(dx, dy), 1.25, atol=1e-6)
    assert np.isclose(np.degrees(np.arctan2(dx, dy)) % 360, 110.5, atol=1e-3)
    assert np.isclose(row["iflux"], 0.0421, atol=1e-9)   # mJy -> Jy
    assert bool(row["use_in_fit"]) is True


def test_core_referenced_to_map_center():
    # MRT Note 3: the core feature's r, PA is measured from the MAP CENTER, so
    # core_x/core_y (the summary reference = XVIII core X0) must equal
    # (r0·sin PA0, r0·cos PA0), NOT the fallback clustering core.
    bundle = _fake_bundle([(2006.1834, "2006_03_09")], core_xy=(1.0, -2.0))
    df = xviii.build_xviii_cluster_df("0003+380", "u", bundle)
    core = df[df["clusterID"] == 0].iloc[0]
    r0, pa0 = 0.04, 290.7                 # 0003+380 core @ 2006 Mar 9
    x0 = r0 * np.sin(np.radians(pa0))
    y0 = r0 * np.cos(np.radians(pa0))
    assert np.isclose(core["core_x"], x0, atol=1e-4)
    assert np.isclose(core["core_y"], y0, atol=1e-4)
    assert core["core_x"] != 1.0          # not the fallback clustering core
    # Core avg == X0 (its own map-center position); avg − core == 0.
    assert np.isclose(core["avg_x"], x0, atol=1e-4)
    assert np.isclose(core["avg_x"] - core["core_x"], 0.0, atol=1e-9)
    # A non-core feature's absolute avg is X0 + core-relative offset.
    f2 = df[df["clusterID"] == 2].iloc[0]
    assert np.isclose(f2["avg_x"], x0 + 1.25 * np.sin(np.radians(110.5)),
                      atol=1e-4)


def _mrt_line(source, F, year, month, day, I, r, PA, flag=" ", robust="Y"):
    """Format one fixed-width MRT data row at the documented byte columns."""
    s = [" "] * 63
    def put(a, b, text):
        s[a:b] = list(f"{text:>{b - a}}")
    put(0, 8, source); put(9, 11, F); s[12] = flag
    put(14, 18, year); put(19, 22, month); put(23, 25, day)
    put(26, 33, f"{I:.1f}"); put(34, 39, f"{r:.2f}"); put(40, 45, f"{PA:.1f}")
    s[61] = robust
    return "".join(s)


def test_core_fallback_to_clustering_core_when_absent(tmp_path):
    # A source with NO core row (only F=1) must anchor X0 to the clustering
    # core, so the feature is placed at clustering_core + its (r, PA) offset.
    header = ("Byte-by-byte\n"
              "--------------------------------------------------------\n")
    line = _mrt_line("9999+999", 1, 2006, "Mar", 9, 10.0, 2.0, 90.0)
    p = tmp_path / "mini.txt"
    p.write_text(header + line + "\n")

    bundle = _fake_bundle([(2006.1834, "2006_03_09")], core_xy=(0.5, -0.5))
    df = xviii.build_xviii_cluster_df("9999+999", "u", bundle, path=str(p))
    assert len(df) == 1
    row = df.iloc[0]
    # Fallback: core reference == clustering core (0.5, -0.5).
    assert np.isclose(row["core_x"], 0.5) and np.isclose(row["core_y"], -0.5)
    # avg = clustering_core + (r·sin90, r·cos90) = (0.5 + 2.0, -0.5 + 0).
    assert np.isclose(row["avg_x"], 2.5, atol=1e-6)
    assert np.isclose(row["avg_y"], -0.5, atol=1e-6)


def test_build_df_maps_use_in_fit_flag():
    # 2008 May 1 rows carry the 'a' flag (not used in kinematic fits) for the
    # non-core features -> use_in_fit False.
    bundle = _fake_bundle([(2008.3299, "2008_05_01")])
    df = xviii.build_xviii_cluster_df("0003+380", "u", bundle)
    non_core = df[df["clusterID"] == 1].iloc[0]
    assert bool(non_core["use_in_fit"]) is False


def test_build_df_empty_for_unknown_source():
    bundle = _fake_bundle([(2006.1834, "2006_03_09")])
    df = xviii.build_xviii_cluster_df("9999+999", "u", bundle)
    assert df.empty
    # still has the expected columns for downstream consumers
    assert "avg_x" in df.columns and "clusterID" in df.columns
