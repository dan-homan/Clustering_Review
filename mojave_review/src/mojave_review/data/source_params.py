"""Per-source run parameters — currently just the redshift (``z``).

The data lives in a ``source_run_param.csv`` the user keeps alongside the
production data (NOT tracked in git). Only two columns matter here:

    Source     e.g. "0003+380"   (band-less, matches split_source_band output)
    redshift   float, or BLANK when unknown

``z`` is used in two places (see plots/summary.py):

  * brightness temperature is multiplied by ``(1 + z)`` to put it in the host
    galaxy frame (the Tb formula already carries the ``(1+z)`` factor; feeding a
    real z is all that's needed — z=0 leaves it as the observed value);
  * apparent jet speed in units of c, ``beta_app``, on the Kinematics hovers:
    ``beta_app = (1 + z) * mu * D_A / c`` with mu the angular speed (mas/yr) and
    D_A the angular-diameter distance at z.

Cosmology is the MOJAVE standard flat ΛCDM (H0=71, Ωm=0.27, ΩΛ=0.73; e.g.
Lister et al. kinematics papers). Change ``_H0`` / ``_OM0`` to adjust.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import numpy as np
import pandas as pd

from .fits_cache import split_source_band

# --- cosmology (MOJAVE standard) -------------------------------------------
_H0 = 71.0     # km / s / Mpc
_OM0 = 0.27    # Omega_matter (flat -> Omega_Lambda = 0.73)

_CSV_NAME = "source_run_param.csv"


# ---------------------------------------------------------------------------
# Redshift table
# ---------------------------------------------------------------------------


def find_source_params(results_dir: Path) -> Path | None:
    """Locate ``source_run_param.csv``. Checks, in order: the current working
    directory (where the user runs the app from), the parent of ``results_dir``,
    then ``results_dir`` itself. Returns None if not found."""
    candidates = [
        Path.cwd() / _CSV_NAME,
        Path(results_dir).parent / _CSV_NAME,
        Path(results_dir) / _CSV_NAME,
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def load_redshifts(path: Path | None) -> dict[str, float]:
    """Map band-less source name -> redshift, for sources with a known z.

    Blank / non-numeric redshifts are skipped (left out of the map), so a
    missing key means "z unknown". Never raises on a malformed file — returns
    an empty map and lets the caller fall back to z=0 behaviour."""
    if path is None:
        return {}
    try:
        df = pd.read_csv(path)
    except Exception:
        return {}
    if "Source" not in df.columns or "redshift" not in df.columns:
        return {}
    out: dict[str, float] = {}
    z = pd.to_numeric(df["redshift"], errors="coerce")
    for src, zv in zip(df["Source"].astype(str), z):
        if pd.notna(zv) and float(zv) > 0:
            out[src.strip()] = float(zv)
    return out


def redshift_for(redshift_map: dict[str, float], source_with_band: str) -> float | None:
    """Look up z for an app source name like ``"0003+380u"`` (band stripped to
    match the CSV's ``Source`` column). None when unknown."""
    base, _band = split_source_band(source_with_band)
    return redshift_map.get(base)


# ---------------------------------------------------------------------------
# Apparent speed in units of c
# ---------------------------------------------------------------------------


@lru_cache(maxsize=1)
def _cosmo():
    from astropy.cosmology import FlatLambdaCDM
    return FlatLambdaCDM(H0=_H0, Om0=_OM0)


def beta_app(speed_mas_per_yr: float, z: float | None) -> float | None:
    """Apparent transverse speed in units of c from angular speed mu (mas/yr)
    and redshift z: ``beta_app = (1 + z) * mu * D_A / c``.

    Returns None when z is unknown / non-positive or the speed is invalid, so
    callers can simply omit it from the hover."""
    if (z is None or not np.isfinite(z) or z <= 0
            or speed_mas_per_yr is None or not np.isfinite(speed_mas_per_yr)):
        return None
    import astropy.units as u
    from astropy.constants import c
    mu = speed_mas_per_yr * u.mas / u.yr
    d_a = _cosmo().angular_diameter_distance(z)
    beta = ((1.0 + z) * mu * d_a / c).to(
        u.dimensionless_unscaled, equivalencies=u.dimensionless_angles())
    return float(beta)
