"""Snippet: offline sync contract used by docs (offline notes) + escape hatches."""

SOURCE = """<ul data-pw-id="notes-list" pw-bind="notes" pw-sync="localStorage pw-outbox">
</ul>
# push: POST /__pyweb/rpc/sync_notes {notes: [...]}
# conflict: last-write-wins
"""

SYNC_PATH = "/__pyweb/rpc/sync_notes"
CONFLICT = "last-write-wins"
OUTBOX_KEY = "pw-outbox"


def queue_push(outbox: list, note: dict) -> list:
    """Queue a note locally (mirrors the documented offline contract)."""
    return outbox + [note]


def merge_notes(server: list, outbox: list) -> list:
    """Last-write-wins merge of queued notes onto server state."""
    merged = {n["id"]: n for n in server}
    merged.update({n["id"]: n for n in outbox})
    return [merged[k] for k in sorted(merged)]


# Escape hatches (docs/07): raw HTML / raw SQL / custom headers stay possible.
ESCAPE_HATCHES = ("raw-html", "raw-sql", "custom-headers")


if __name__ == "__main__":
    server = [{"id": "note-1", "text": "a"}]
    print(merge_notes(server, queue_push([], {"id": "note-2", "text": "b"})))
    print(ESCAPE_HATCHES)
