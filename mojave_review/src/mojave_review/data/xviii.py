"""MOJAVE Paper XVIII Gaussian-fit table (Lister et al.).

Parses the machine-readable table ``MOJAVE_XVIII_apjac230ft4_mrt.txt``
(shipped as package data) and adapts one source's fitted Gaussian
features into the *same* cluster DataFrame schema the pipeline produces,
so ``plots.summary.build_summary_figure`` and ``plots.overlay`` can render
the old Gaussian fits unchanged for the side-by-side comparison page.

Column mapping (XVIII → our schema), verified against 0003+380 @ 2006 Mar 9:

    ID (B1950, no band)  -> source        (band appended to match Results/)
    F (feature number)   -> clusterID     (0 = core, stable across epochs)
    f_F == 'a'           -> use_in_fit=False (else True)
    Obs.Y/M/D            -> epoch, ep_name (snapped to the matching MOJAVE obs)
    I  [mJy]             -> iflux [Jy]     (/1000)
    r [mas] + PA [deg]   -> avg_x/avg_y    (x=r·sin PA, y=r·cos PA, + core)
    MajAxis [mas]        -> fwhm_maj
    Ratio                -> fwhm_min       (MajAxis·Ratio)
    MajPA [deg]          -> cpa / sizePA   (Gaussian major-axis PA)
    Robust? (Y/N)        -> robust         (per-feature, earliest non-blank)

The features are positioned in the *clustering* core frame (each epoch's
XVIII feature offsets are added to that epoch's fitted clustering core)
so the Gaussian ellipses overlay cleanly on the shared clean-component
image built from our npz. XVIII has no polarization -> pflux/evpa = NaN.
"""

from __future__ import annotations

import calendar
from functools import lru_cache
from importlib.resources import files
from pathlib import Path

import numpy as np
import pandas as pd

# Fixed-width byte columns (0-based, end-exclusive) from the MRT
# byte-by-byte description. See the header of the shipped .txt.
_SLICES = {
    "id":      (0, 8),
    "F":       (9, 11),
    "flag":    (12, 13),
    "year":    (14, 18),
    "month":   (19, 22),
    "day":     (23, 25),
    "I":       (26, 33),
    "r":       (34, 39),
    "PA":      (40, 45),
    "MajAxis": (46, 51),
    "Ratio":   (52, 56),
    "MajPA":   (57, 60),
    "Robust":  (61, 62),
}

_MONTHS = {m: i for i, m in enumerate(calendar.month_abbr) if m}

# Tolerance for matching an XVIII observation date to a MOJAVE epoch in the
# current npz. XVIII epochs are a subset of the current observation epochs,
# aligned to well under a day in practice; ~25 days is a generous guard
# against the odd decimal-year rounding mismatch without ever pulling in a
# neighbouring session.
_EPOCH_MATCH_TOL_YR = 25.0 / 365.25

# Default bundled table.
_DEFAULT_TABLE = files("mojave_review.data") / "MOJAVE_XVIII_apjac230ft4_mrt.txt"


def default_table_path() -> str:
    """Absolute path of the bundled XVIII table."""
    return str(_DEFAULT_TABLE)


def _f(text: str) -> float:
    """Parse a possibly-blank fixed-width float field -> value or NaN."""
    t = text.strip()
    if not t:
        return float("nan")
    try:
        return float(t)
    except ValueError:
        return float("nan")


def _decimal_year(year: int, month: int, day: int) -> float:
    """Fractional year for an observation date (matches the pipeline's
    epoch_val to well under a day, which is all the epoch matcher needs)."""
    doy = (pd.Timestamp(year=year, month=month, day=day).dayofyear - 1)
    days = 366 if calendar.isleap(year) else 365
    return year + doy / days


@lru_cache(maxsize=4)
def load_xviii_table(path: str | None = None) -> pd.DataFrame:
    """Parse the MRT into a flat DataFrame (one row per feature-epoch).

    Cached by path. Data rows are detected by a 4-digit year in the fixed
    Obs.Y field, which cleanly skips the descriptive header block.
    """
    p = Path(path) if path else Path(default_table_path())
    records: list[dict] = []
    for raw in p.read_text().splitlines():
        if len(raw) < 45:
            continue
        year_txt = raw[_SLICES["year"][0]:_SLICES["year"][1]].strip()
        if not year_txt.isdigit():
            continue  # header / separator line
        src = raw[_SLICES["id"][0]:_SLICES["id"][1]].strip()
        f_txt = raw[_SLICES["F"][0]:_SLICES["F"][1]].strip()
        if not src or not f_txt.lstrip("-").isdigit():
            continue
        mon = _MONTHS.get(raw[_SLICES["month"][0]:_SLICES["month"][1]].strip(), 0)
        day = raw[_SLICES["day"][0]:_SLICES["day"][1]].strip()
        if mon == 0 or not day.isdigit():
            continue
        year = int(year_txt)
        records.append({
            "source": src,
            "F": int(f_txt),
            "flag": raw[_SLICES["flag"][0]:_SLICES["flag"][1]].strip(),
            "epoch": _decimal_year(year, mon, int(day)),
            "I_mJy": _f(raw[_SLICES["I"][0]:_SLICES["I"][1]]),
            "r": _f(raw[_SLICES["r"][0]:_SLICES["r"][1]]),
            "PA": _f(raw[_SLICES["PA"][0]:_SLICES["PA"][1]]),
            "MajAxis": _f(raw[_SLICES["MajAxis"][0]:_SLICES["MajAxis"][1]]),
            "Ratio": _f(raw[_SLICES["Ratio"][0]:_SLICES["Ratio"][1]]),
            "MajPA": _f(raw[_SLICES["MajPA"][0]:_SLICES["MajPA"][1]]),
            "Robust": raw[_SLICES["Robust"][0]:_SLICES["Robust"][1]].strip(),
        })
    return pd.DataFrame.from_records(records)


@lru_cache(maxsize=4)
def xviii_sources(path: str | None = None) -> frozenset[str]:
    """Set of B1950 source identifiers present in the XVIII table."""
    tbl = load_xviii_table(path)
    if tbl.empty:
        return frozenset()
    return frozenset(tbl["source"].unique().tolist())


def _robust_by_feature(sub: pd.DataFrame) -> dict[int, bool]:
    """Per-feature robust flag (XVIII marks it per row but leaves it blank on
    'a'-flagged rows). Canonical = earliest-epoch non-blank Y/N, mirroring the
    pipeline's per-cluster rule; all-blank -> False."""
    out: dict[int, bool] = {}
    for feat, rows in sub.sort_values("epoch").groupby("F"):
        vals = [r for r in rows["Robust"] if r in ("Y", "N")]
        out[int(feat)] = (vals[0] == "Y") if vals else False
    return out


def build_xviii_cluster_df(
    source_no_band: str,
    band: str,
    bundle,
    *,
    path: str | None = None,
) -> pd.DataFrame:
    """Adapt one source's XVIII features into our cluster-DataFrame schema.

    Registration follows MRT Note (3): the **core** feature's (r, PA) is
    measured from the *map center* — the same reference as our clustering
    ``core_x``/``core_y`` — while **non-core** features' (r, PA) are measured
    from the *core*. So each epoch's absolute (map-center-relative) positions
    are::

        X0        = (r0·sin PA0, r0·cos PA0)      # XVIII core, from map center
        feature k = X0 + (rk·sin PAk, rk·cos PAk) # core + core-relative offset

    ``avg_x``/``avg_y`` hold those absolute map positions. ``core_x``/``core_y``
    are set to **X0** (the XVIII core), so this ``avg − core`` frame is the
    *summary* frame: distances/PA are relative to XVIII's own core (core at 0),
    directly comparable to the clustering summary. The **overlay** re-registers
    to the clustering core (``compare_overlay`` overrides ``core_x``/``core_y``
    with the fitted clustering core) so the Gaussians sit on the shared,
    clustering-core-centered image with the true X0−core registration offset.

    The epoch is snapped to the nearest MOJAVE observation's ``epoch_val`` (for
    the shared image + slider). Returns an empty frame with the right columns
    when the source is absent or has no epoch matches.
    """
    cols = ["source", "band", "clusterID", "epoch", "ep_name",
            "avg_x", "avg_y", "core_x", "core_y",
            "fwhm_maj", "fwhm_min", "cpa", "sizePA", "sizeMaj", "bmaj",
            "iflux", "pflux", "evpa", "robust", "use_in_fit", "select"]
    tbl = load_xviii_table(path)
    sub = tbl[tbl["source"] == source_no_band]
    if sub.empty or bundle is None or bundle.plotdata is None:
        return pd.DataFrame(columns=cols)

    info = bundle.plotdata.epoch_info
    ep_vals = np.asarray(info["epoch_val"], dtype=float)
    ep_names = np.asarray(info["epoch_name"]).astype(str)
    ep_bmaj = np.asarray(info["bmaj"], dtype=float)

    # Per-epoch clustering core (nearest fitted-core epoch) — only a fallback
    # for the rare epoch that has no XVIII core row to anchor X0.
    cdf = bundle.cluster_df
    fitted = cdf[cdf["clusterID"] >= 0]
    core_by_epoch = (fitted.groupby("epoch")[["core_x", "core_y"]].first())
    core_ep = core_by_epoch.index.to_numpy(dtype=float)

    def _clustering_core(ev: float) -> tuple[float, float]:
        if not len(core_ep):
            return 0.0, 0.0
        k = int(np.argmin(np.abs(core_ep - ev)))
        return (float(core_by_epoch.iloc[k]["core_x"]),
                float(core_by_epoch.iloc[k]["core_y"]))

    def _xy(r_val, pa_val) -> tuple[float, float]:
        if not (np.isfinite(r_val) and np.isfinite(pa_val)):
            return 0.0, 0.0
        pr = np.radians(pa_val)
        return float(r_val) * np.sin(pr), float(r_val) * np.cos(pr)

    # Snap every row to the nearest MOJAVE observation; drop unmatched.
    src_ep = sub["epoch"].to_numpy(dtype=float)
    j_idx = np.argmin(np.abs(ep_vals[None, :] - src_ep[:, None]), axis=1)
    matched = ep_vals[j_idx]
    keep = np.abs(matched - src_ep) <= _EPOCH_MATCH_TOL_YR
    sub = sub.assign(_ev=matched, _ename=ep_names[j_idx]).loc[keep]
    if sub.empty:
        return pd.DataFrame(columns=cols)

    robust_map = _robust_by_feature(sub)
    rows: list[dict] = []
    for ev, grp in sub.groupby("_ev"):
        ev = float(ev)
        # XVIII core position on the map (Note 3). Fall back to the clustering
        # core if this epoch has no XVIII core feature to anchor to.
        core_rows = grp[grp["F"] == 0]
        if len(core_rows):
            x0, y0 = _xy(core_rows.iloc[0]["r"], core_rows.iloc[0]["PA"])
        else:
            x0, y0 = _clustering_core(ev)
        ename = str(grp.iloc[0]["_ename"])
        bmaj = float(ep_bmaj[int(np.argmin(np.abs(ep_vals - ev)))])
        for _, r in grp.iterrows():
            feat = int(r["F"])
            if feat == 0:
                ax, ay = x0, y0                       # core: r,PA from map center
            else:
                ox, oy = _xy(r["r"], r["PA"])         # core-relative offset
                ax, ay = x0 + ox, y0 + oy
            maj = r["MajAxis"]
            rat = r["Ratio"] if np.isfinite(r["Ratio"]) else 1.0
            rows.append({
                "source": source_no_band,
                "band": band,
                "clusterID": feat,
                "epoch": ev,
                "ep_name": ename,
                "avg_x": ax,
                "avg_y": ay,
                "core_x": x0,          # XVIII core (summary frame; avg−core=Rk)
                "core_y": y0,
                "fwhm_maj": maj,
                "fwhm_min": maj * rat if np.isfinite(maj) else float("nan"),
                "cpa": r["MajPA"],
                "sizePA": r["MajPA"],
                "sizeMaj": maj,
                "bmaj": bmaj,
                "iflux": r["I_mJy"] / 1000.0,
                "pflux": float("nan"),
                "evpa": float("nan"),
                "robust": robust_map.get(feat, False),
                "use_in_fit": r["flag"] != "a",
                "select": False,
            })
    if not rows:
        return pd.DataFrame(columns=cols)
    return pd.DataFrame.from_records(rows)[cols]


def xviii_epoch_options(xviii_df: pd.DataFrame) -> list[tuple[float, str]]:
    """Sorted unique (epoch_val, ep_name) for the XVIII overlay slider."""
    if xviii_df.empty:
        return []
    uniq = (xviii_df[["epoch", "ep_name"]]
            .drop_duplicates()
            .sort_values("epoch"))
    return list(zip(uniq["epoch"].astype(float), uniq["ep_name"].astype(str)))
