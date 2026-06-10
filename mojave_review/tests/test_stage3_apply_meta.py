"""mojave-apply --stage3-meta bookkeeping: archive considered submissions,
append the ledger entry (with {{BACKUP_REF}} resolved), and set Status."""

from __future__ import annotations

import json

from mojave_review.cli.apply import _apply_stage3_meta
from mojave_review.notes import notes_dir_for, read_note, get_section, get_status


def test_stage3_meta_archives_and_writes_ledger(tmp_path):
    recs = tmp_path / "recommendations"
    source = "0003-066u_1994.00-2026.00"
    sub = recs / source / "submitted"
    sub.mkdir(parents=True)
    (sub / "alice.json").write_text("{}")
    (sub / "bob.json").write_text("{}")

    meta = recs / source / "stage3" / "aggregated.stage3.json"
    meta.parent.mkdir(parents=True)
    meta.write_text(json.dumps({
        "considered_slugs": ["alice", "bob"],
        "ledger_entry": "### 2026-06-10 — Stage 3 reconciliation (run 2, "
                        "applied by Dan) — {{BACKUP_REF}}\nConsidered: alice, bob",
        "status": "Stage 3 done · applied 2026-06-10",
        "date": "2026-06-10",
    }))

    _apply_stage3_meta(meta, recs, source,
                       backup_ref="backups/backup_007",
                       fallback_date="2026-06-10")

    # considered submissions moved out of submitted/
    assert not (sub / "alice.json").exists()
    assert (recs / source / "considered" / "2026-06-10" / "alice.json").is_file()
    assert (recs / source / "considered" / "2026-06-10" / "bob.json").is_file()

    # ledger entry appended with the placeholder resolved; status set
    md = read_note(notes_dir_for(recs), source)
    ledger = get_section(md, "ledger")
    assert "Stage 3 reconciliation (run 2" in ledger
    assert "backups/backup_007" in ledger
    assert "{{BACKUP_REF}}" not in ledger
    assert get_status(md) == "Stage 3 done · applied 2026-06-10"
