"""Per-source human-readable lab-notebook (notes/<source>.md).

See docs/review_workflow.md. The notes file holds only *durable* content —
Stage 1/2 prose and an append-only decisions ledger. Volatile in-flight
suggestions are NOT stored here; the app renders those live from the submitted
recommendation JSONs.
"""

from .store import (
    notes_dir_for, note_path, scaffold, read_note, write_note,
    get_section, set_section, append_ledger, get_status, set_status, SECTIONS,
)
from .render import (
    notes_markdown, open_suggestions_markdown, combined_markdown,
)

__all__ = [
    "notes_dir_for", "note_path", "scaffold", "read_note", "write_note",
    "get_section", "set_section", "append_ledger", "get_status", "set_status",
    "SECTIONS",
    "notes_markdown", "open_suggestions_markdown", "combined_markdown",
]
