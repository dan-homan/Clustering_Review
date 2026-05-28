"""Fetch and cache MOJAVE FITS images.

URL pattern (confirmed against `grab_mojave_image` in ``cluster_code.py``)::

    http://www.cv.nrao.edu/2cmVLBA/data/<source>/<epoch_name>/<source>.<band>.<epoch_name>.<stokes>cn.fits.gz

`<source>` is the bare B1950 name (``0003-066``) — without the trailing
band code that appears in the per-source folder name (``0003-066u``).
"""

from __future__ import annotations

import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path

import requests
from astropy.io.fits import HDUList, open as fits_open

log = logging.getLogger(__name__)

MOJAVE_BASE = "http://www.cv.nrao.edu/2cmVLBA/data"
DOWNLOAD_TIMEOUT_S = 60


class FitsFetchError(RuntimeError):
    """Raised when a FITS file can't be obtained from cache or network."""


def mojave_fits_url(source_no_band: str, band: str, epoch_name: str,
                    stokes: str = "i") -> str:
    return (f"{MOJAVE_BASE}/{source_no_band}/{epoch_name}/"
            f"{source_no_band}.{band}.{epoch_name}.{stokes}cn.fits.gz")


def split_source_band(source_with_band: str) -> tuple[str, str]:
    """Split ``"0003-066u"`` -> ``("0003-066", "u")``.

    Falls back to the input unchanged + band="u" if the trailing character
    isn't a known band code (so we don't silently corrupt unexpected names).
    """
    if len(source_with_band) >= 2 and source_with_band[-1] in {"u", "x", "c", "s", "k"}:
        return source_with_band[:-1], source_with_band[-1]
    return source_with_band, "u"


def local_fits_path(cache_dir: Path, source_no_band: str, band: str,
                    epoch_name: str, stokes: str = "i") -> Path:
    return (cache_dir / source_no_band / epoch_name
            / f"{source_no_band}.{band}.{epoch_name}.{stokes}cn.fits.gz")


@dataclass(frozen=True)
class FitsRef:
    """All we need to identify one FITS file at the archive."""
    source_no_band: str
    band: str
    epoch_name: str
    stokes: str = "i"

    @property
    def url(self) -> str:
        return mojave_fits_url(self.source_no_band, self.band, self.epoch_name, self.stokes)

    def cache_path(self, cache_dir: Path) -> Path:
        return local_fits_path(cache_dir, self.source_no_band, self.band,
                                self.epoch_name, self.stokes)


def fetch_fits(ref: FitsRef, cache_dir: Path) -> Path:
    """Return a path to the FITS file, downloading from MOJAVE if not cached.

    Raises FitsFetchError if the file can't be obtained.
    """
    target = ref.cache_path(cache_dir)
    if target.is_file() and target.stat().st_size > 0:
        return target

    target.parent.mkdir(parents=True, exist_ok=True)
    log.info("Fetching %s", ref.url)
    try:
        with requests.get(ref.url, stream=True, timeout=DOWNLOAD_TIMEOUT_S) as resp:
            if resp.status_code == 404:
                raise FitsFetchError(f"Not found on MOJAVE: {ref.url}")
            resp.raise_for_status()
            # Write atomically so a partial download never satisfies a future
            # cache hit.
            tmp_fd, tmp_path = tempfile.mkstemp(
                prefix=target.name + ".", dir=str(target.parent), suffix=".part"
            )
            try:
                with os.fdopen(tmp_fd, "wb") as f:
                    for chunk in resp.iter_content(chunk_size=1 << 16):
                        if chunk:
                            f.write(chunk)
                os.replace(tmp_path, target)
            except BaseException:
                try:
                    os.unlink(tmp_path)
                except FileNotFoundError:
                    pass
                raise
    except requests.RequestException as e:
        raise FitsFetchError(f"Network error fetching {ref.url}: {e}") from e

    if not target.is_file() or target.stat().st_size == 0:
        raise FitsFetchError(f"Downloaded file empty: {target}")
    return target


def open_fits(path: Path) -> HDUList:
    """Thin wrapper to keep astropy import behind one call site."""
    return fits_open(str(path))
