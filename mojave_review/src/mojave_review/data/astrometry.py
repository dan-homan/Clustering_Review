"""Absolute astrometric positions for the core (cluster 0).

The data lives in an ``astrometry_mojave.dat`` the user keeps alongside the
production data (NOT tracked in git), one fixed-layout line per (source,
astrometric observation)::

    0003-066   20211023 h  4  EPOCH: 2021.81167 \
        R: 00 06 13.89288712 -+ 0.0422  D: -06 23 35.3355558 -+ 0.0941 \
        Corr: -0.0210  Obs: 151 / 157  Sca: 0 / 0

Fields we use: the band-less source name, the observation date (``YYYYMMDD``,
which matches our ``ep_name`` once underscored), the sexagesimal RA/Dec, and
their 1-sigma errors.

Why it matters
--------------
Every position in the app is plotted **relative to the core**, i.e. the origin
rides along with whatever the core did in that epoch's map. Where an absolute
astrometric position exists we can do better: put the origin at a point that is
FIXED on the sky — the mean astrometric position of the core across the epochs
that have astrometry — so the core itself is free to wander and the features
carry absolute-frame motion.

With ``corr(e) = astro(e) - mean(astro)`` (the per-epoch astrometric
correction), the reference position in epoch *e*'s map frame is::

    reference = core(e) - corr(e)

so a plotted offset becomes ``avg - core + corr(e)`` and the core itself plots
at ``+corr(e)`` instead of exactly ``(0, 0)``. :func:`apply_astrometric_reference`
implements that by rewriting ``core_x`` / ``core_y``, which is all it takes:
every consumer (summary views, uncertainties, the overlay image + CC scatter,
the synthesized/stacked contours, the zoom extent) derives positions from
``avg - core``.

Epochs with no astrometry keep the plain core reference, so a source with
partial coverage is plotted in two frames — unavoidable, and the UI says so.

Units / sign conventions
------------------------
* ``dx`` is +east (= +RA), matching the app's ``+x`` (the plots then reverse the
  axis so east shows on the left); ``dy`` is +north.
* The file's RA offsets and RA errors are **coordinate** angles, not true
  angles: they must be multiplied by ``cos(dec)``. (Confirmed empirically — at
  dec +81.6 the RA error column runs ~6.5x the Dec one, and the ratio collapses
  to ~1 after the ``cos(dec)`` scaling.)
* Corrections are small: ~0.1 mas rms, up to ~0.7 mas — sub-beam, but well
  above the CC centroid errors the 1-sigma bars show.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .fits_cache import split_source_band

__all__ = [
    "AstroEpoch", "SourceAstrometry", "AstroApplied",
    "find_astrometry_file", "load_astrometry", "astrometry_for",
    "apply_astrometric_reference",
]

_DAT_NAME = "astrometry_mojave.dat"

# mas per degree
_MAS_PER_DEG = 3_600_000.0


# ---------------------------------------------------------------------------
# Records
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class AstroEpoch:
    """One epoch's astrometric correction, relative to the source mean."""
    ep_name: str      # "YYYY_MM_DD" — matches cluster_df.ep_name
    epoch: float      # decimal year as given in the file (informational)
    dx: float         # mas, +east (+RA*cos dec), relative to the mean position
    dy: float         # mas, +north
    sig_x: float      # mas, 1-sigma on dx (file RA error * cos dec)
    sig_y: float      # mas, 1-sigma on dy


@dataclass(frozen=True)
class SourceAstrometry:
    """Every astrometric epoch for one (band-less) source + their mean."""
    source: str
    ra_mean_deg: float
    dec_mean_deg: float
    epochs: dict[str, AstroEpoch]   # keyed by ep_name

    @property
    def n_epochs(self) -> int:
        return len(self.epochs)


@dataclass(frozen=True)
class AstroApplied:
    """What :func:`apply_astrometric_reference` actually did."""
    available: bool          # the source has astrometry at all
    n_astro: int             # astrometric epochs known for the source
    n_matched: int           # of those, how many matched an epoch in the df
    n_epochs: int            # epochs in the df


# ---------------------------------------------------------------------------
# Discovery + parsing
# ---------------------------------------------------------------------------


def find_astrometry_file(
    results_dir: Path, explicit: Path | str | None = None,
) -> Path | None:
    """Locate ``astrometry_mojave.dat`` — the same search as
    ``source_run_param.csv`` (see
    :func:`data.source_params.find_source_params` for why cwd is last).

    ``explicit`` (the ``astrometry_file`` config key / ``--astrometry-file``)
    wins outright when given. Otherwise: the parent of ``results_dir``, then
    ``results_dir``, then the current working directory.

    Returns None when nothing is found — a perfectly normal state for a
    project with no astrometry yet, which disables the header option rather
    than erroring.
    """
    if explicit is not None:
        return Path(explicit).expanduser()
    candidates = [
        Path(results_dir).parent / _DAT_NAME,
        Path(results_dir) / _DAT_NAME,
        Path.cwd() / _DAT_NAME,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def _ep_name(date_token: str) -> str:
    """``"20211023"`` -> ``"2021_10_23"`` (our ``ep_name`` form)."""
    d = "".join(ch for ch in date_token if ch.isdigit())
    if len(d) != 8:
        raise ValueError(f"bad date token {date_token!r}")
    return f"{d[0:4]}_{d[4:6]}_{d[6:8]}"


def _parse_line(line: str) -> tuple[str, str, float, float, float, float, float] | None:
    """Parse one data line -> ``(source, ep_name, epoch, ra_deg, dec_deg,
    ra_err_mas, dec_err_mas)``, or None when the line isn't parseable.

    ``ra_err_mas`` is the file's RA error as given (a coordinate angle); the
    ``cos(dec)`` scaling happens once the source mean declination is known.
    """
    f = line.split()
    # source date band nbands EPOCH: <val> R: h m s -+ err D: d m s -+ err ...
    if len(f) < 18 or f[4] != "EPOCH:" or f[6] != "R:" or f[12] != "D:":
        return None
    try:
        source = f[0]
        ep_name = _ep_name(f[1])
        epoch = float(f[5])
        ra_deg = (float(f[7]) + float(f[8]) / 60.0 + float(f[9]) / 3600.0) * 15.0
        ra_err = float(f[11])
        # Dec sign lives on the degrees token ("-06"), and float("-00") loses
        # it for a source inside the first degree south — read the string.
        dd_tok = f[13].strip()
        sign = -1.0 if dd_tok.startswith("-") else 1.0
        dec_deg = sign * (abs(float(dd_tok)) + float(f[14]) / 60.0
                          + float(f[15]) / 3600.0)
        dec_err = float(f[17])
    except (ValueError, IndexError):
        return None
    return source, ep_name, epoch, ra_deg, dec_deg, ra_err, dec_err


def load_astrometry(path: Path | None) -> dict[str, SourceAstrometry]:
    """Map band-less source name -> :class:`SourceAstrometry`.

    The mean position is taken over **every** epoch the file carries for that
    source (reviewer decision 2026-10-29): a source-level constant, so the
    reference frame never shifts when a model gains epochs or the loaded epoch
    range changes. A handful of astrometric dates have no matching MOJAVE epoch
    in our CSVs; they still count toward the mean.

    Never raises on a malformed file — unparseable lines are skipped and a
    missing / unreadable file yields an empty map, so every caller simply falls
    back to the plain core reference.
    """
    if path is None:
        return {}
    raw: dict[str, list[tuple]] = {}
    try:
        with open(path, "r", errors="replace") as fh:
            for line in fh:
                if not line.strip() or line.lstrip().startswith("#"):
                    continue
                rec = _parse_line(line)
                if rec is not None:
                    raw.setdefault(rec[0].strip(), []).append(rec[1:])
    except OSError:
        return {}

    out: dict[str, SourceAstrometry] = {}
    for source, recs in raw.items():
        # Duplicate dates (same source observed twice in a day) collapse to the
        # last one — ep_name is our join key and must be unique.
        by_name = {r[0]: r for r in recs}
        names = sorted(by_name)
        ra = np.array([by_name[n][2] for n in names], dtype=float)
        dec = np.array([by_name[n][3] for n in names], dtype=float)
        if not len(names):
            continue
        ra_mean = float(ra.mean())
        dec_mean = float(dec.mean())
        cos_dec = float(np.cos(np.deg2rad(dec_mean)))
        epochs: dict[str, AstroEpoch] = {}
        for n in names:
            _nm, epoch, ra_deg, dec_deg, ra_err, dec_err = by_name[n]
            epochs[n] = AstroEpoch(
                ep_name=n,
                epoch=float(epoch),
                dx=(ra_deg - ra_mean) * _MAS_PER_DEG * cos_dec,
                dy=(dec_deg - dec_mean) * _MAS_PER_DEG,
                # File RA errors are coordinate angles -> true angle needs cos(dec).
                sig_x=float(ra_err) * cos_dec,
                sig_y=float(dec_err),
            )
        out[source] = SourceAstrometry(
            source=source, ra_mean_deg=ra_mean, dec_mean_deg=dec_mean,
            epochs=epochs,
        )
    return out


def astrometry_for(table: dict[str, SourceAstrometry],
                   source_with_band: str) -> SourceAstrometry | None:
    """Look up astrometry for an app source name like ``"0003-066u"`` (band
    stripped to match the file's source column). None when we have none."""
    base, _band = split_source_band(source_with_band)
    return table.get(base)


# ---------------------------------------------------------------------------
# Applying the reference
# ---------------------------------------------------------------------------

# Columns the astrometric reference adds to cluster_df. ``astro_sig_x/y`` are
# the 1-sigma errors of the correction used on that row (NaN where the epoch
# has no astrometry); plots/uncertainty.py folds them into the position bars.
ASTRO_COLS = ("astro_sig_x", "astro_sig_y")


def apply_astrometric_reference(
    cluster_df: pd.DataFrame, astro: SourceAstrometry | None,
) -> tuple[pd.DataFrame, AstroApplied]:
    """Re-reference ``cluster_df`` to the mean astrometric core position.

    Returns ``(df, info)``. ``df`` is a copy with ``core_x`` / ``core_y``
    shifted by ``-corr(e)`` on every row of an epoch that has astrometry (so
    plotted offsets gain ``+corr(e)``), plus the ``astro_sig_x`` /
    ``astro_sig_y`` error columns. Epochs with no astrometric position are
    untouched and keep the plain core reference.

    A no-op (same values, info.available False / n_matched 0) when the source
    has no astrometry or the frame can't be applied, so callers can hand the
    result straight to the plot builders.
    """
    df = cluster_df.copy()
    n_epochs = int(df["epoch"].nunique()) if "epoch" in df.columns and len(df) else 0
    if astro is None or not astro.epochs:
        return df, AstroApplied(False, 0, 0, n_epochs)
    if "ep_name" not in df.columns or not len(df):
        # Frames without epoch names (window fits, the XVIII table) can't join.
        return df, AstroApplied(True, astro.n_epochs, 0, n_epochs)

    names = df["ep_name"].astype(str)
    dx = names.map(lambda n: astro.epochs[n].dx if n in astro.epochs else np.nan)
    dy = names.map(lambda n: astro.epochs[n].dy if n in astro.epochs else np.nan)
    sx = names.map(lambda n: astro.epochs[n].sig_x if n in astro.epochs else np.nan)
    sy = names.map(lambda n: astro.epochs[n].sig_y if n in astro.epochs else np.nan)

    hit = dx.notna()
    # reference = core - corr  =>  plotted offset (avg - reference) = avg - core + corr
    df.loc[hit, "core_x"] = df.loc[hit, "core_x"] - dx[hit]
    df.loc[hit, "core_y"] = df.loc[hit, "core_y"] - dy[hit]
    df["astro_sig_x"] = sx.to_numpy(dtype=float)
    df["astro_sig_y"] = sy.to_numpy(dtype=float)

    matched = int(names[hit].nunique())
    return df, AstroApplied(True, astro.n_epochs, matched, n_epochs)
