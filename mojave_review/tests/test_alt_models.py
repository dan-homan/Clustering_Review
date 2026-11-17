"""Alternate-model discovery is name-agnostic.

``alt_models/`` used to be scanned with a hard-coded ``alt_model_NNN``
pattern. The pipeline now also writes descriptive tags
(``alt_model_recalcIDs_*``), which that pattern dropped silently — the
model simply never appeared in the dropdown. Discovery is now by file
stem, seeded from the per-model ``_config.json`` and
``_merged_win_results.csv``.
"""

from __future__ import annotations

from pathlib import Path

from mojave_review.data.loader import SourceRef, list_models


def _make_source(tmp_path: Path) -> SourceRef:
    folder = tmp_path / "0003-066u_1994.00-2026.00"
    folder.mkdir()
    src = SourceRef(source="0003-066u", epoch_min=1994.0, epoch_max=2026.0,
                    folder=folder)
    (folder / f"{src.file_prefix}.merged_win_results.csv").write_text("x\n")
    (folder / f"{src.file_prefix}.merged_win_results.plotdata.npz").write_bytes(b"")
    return src


def _write_alt(src: SourceRef, stem: str, *, csv: bool = True,
               npz: bool = True, config: bool = True) -> None:
    alt = src.folder / "alt_models"
    alt.mkdir(exist_ok=True)
    if csv:
        (alt / f"{stem}_merged_win_results.csv").write_text("x\n")
    if npz:
        (alt / f"{stem}_plotdata.npz").write_bytes(b"")
    if config:
        (alt / f"{stem}_config.json").write_text("{}\n")


def test_numbered_and_named_alt_models_are_both_found(tmp_path):
    src = _make_source(tmp_path)
    _write_alt(src, "alt_model_001")
    _write_alt(src, "alt_model_recalcIDs")
    _write_alt(src, "alt_model_recalc_IDs_v2")      # tag with underscores

    by_key = {m.key: m for m in list_models(src)}
    assert "alt_model_001" in by_key
    assert "alt_model_recalcIDs" in by_key
    assert "alt_model_recalc_IDs_v2" in by_key
    # Each ships its own npz (unlike a backup).
    for key in ("alt_model_001", "alt_model_recalcIDs", "alt_model_recalc_IDs_v2"):
        assert by_key[key].npz_path is not None
        assert by_key[key].npz_path.name == f"{key}_plotdata.npz"
        assert by_key[key].csv_path.name == f"{key}_merged_win_results.csv"


def test_alt_model_without_npz_still_loads_csv_only(tmp_path):
    src = _make_source(tmp_path)
    _write_alt(src, "alt_model_bare", npz=False)
    model = next(m for m in list_models(src) if m.key == "alt_model_bare")
    assert model.npz_path is None


def test_config_without_csv_is_skipped(tmp_path):
    """A config left behind by an aborted run isn't a loadable model."""
    src = _make_source(tmp_path)
    _write_alt(src, "alt_model_aborted", csv=False, npz=False)
    assert not [m for m in list_models(src) if m.key == "alt_model_aborted"]


def test_csv_without_config_is_still_found(tmp_path):
    """Older alt models predate the per-model config.json."""
    src = _make_source(tmp_path)
    _write_alt(src, "alt_model_002", config=False)
    assert "alt_model_002" in {m.key for m in list_models(src)}


def test_keys_are_namespaced_and_unique(tmp_path):
    """A stray stem can't shadow `current` or a backup."""
    src = _make_source(tmp_path)
    _write_alt(src, "current")
    keys = [m.key for m in list_models(src)]
    assert keys.count("current") == 1
    assert "alt_model_current" in keys


def test_no_alt_models_dir(tmp_path):
    src = _make_source(tmp_path)
    assert [m.key for m in list_models(src)] == ["current"]
