"""Everything the agent remembers lives in plain files under data/:

data/
├── snapshots/<competitor>/<source>.md   latest text of each source (git keeps the history)
├── reports/<competitor>/<framework>.md  battle cards and other analyses, always up to date
├── digests/<YYYY-MM-DD>.md              one digest per run
└── changes.jsonl                        every classified change ever detected
"""

from __future__ import annotations

import difflib
import json
import re
from dataclasses import dataclass
from pathlib import Path

MAX_DIFF_LINES = 400  # keep prompts bounded when a page is completely redesigned


@dataclass
class SourceDiff:
    source_id: str
    source_type: str
    url: str | None
    is_new: bool          # first time we see this source (baseline, no comparison possible)
    diff: str             # human-readable diff; empty when nothing changed

    @property
    def changed(self) -> bool:
        return bool(self.diff)


def normalize(text: str) -> str:
    """Remove differences that don't mean anything (whitespace, blank lines, copyright years)."""
    lines = []
    for line in text.splitlines():
        line = re.sub(r"\s+", " ", line).strip()
        if not line or re.match(r"^(©|copyright)\s", line, re.I):
            continue
        lines.append(line)
    return "\n".join(lines) + "\n"


def page_diff(old: str, new: str) -> str:
    """Unified diff of a page: lines starting with '-' were removed, '+' were added."""
    diff = list(difflib.unified_diff(old.splitlines(), new.splitlines(), "last run", "this run", n=2, lineterm=""))
    if len(diff) > MAX_DIFF_LINES:
        diff = diff[:MAX_DIFF_LINES] + [f"... ({len(diff) - MAX_DIFF_LINES} more diff lines truncated)"]
    return "\n".join(diff)


def list_diff(old: str, new: str) -> str:
    """For feeds and searches only NEW items matter; reordering or items dropping off are noise."""
    seen = {_item_key(line) for line in old.splitlines()}
    added = [line for line in new.splitlines() if line.strip() and _item_key(line) not in seen]
    return "\n".join(f"+ {line}" for line in added)


def _item_key(line: str) -> str:
    # Items are '- date | title | url'; the URL is the most stable identity
    parts = [p.strip() for p in line.split("|")]
    return (parts[-1] if len(parts) >= 3 and parts[-1] else line).lower()


class Store:
    def __init__(self, data_path: Path):
        self.root = data_path

    # ── snapshots ───────────────────────────────────────────────

    def snapshot_path(self, competitor_slug: str, source_id: str) -> Path:
        return self.root / "snapshots" / competitor_slug / f"{source_id}.md"

    def read_snapshot(self, competitor_slug: str, source_id: str) -> str | None:
        path = self.snapshot_path(competitor_slug, source_id)
        return path.read_text() if path.exists() else None

    def update_snapshot(self, competitor_slug, source, text: str) -> SourceDiff:
        """Save the new snapshot and return what changed compared to the previous one."""
        new = normalize(text)
        old = self.read_snapshot(competitor_slug, source.id)
        if source.is_list and old is not None:
            # Keep remembering old items so they aren't reported as "new" again later
            new_items = [line for line in new.splitlines() if line.strip()]
            old_items = [line for line in old.splitlines() if line.strip()]
            keys = {_item_key(line) for line in new_items}
            merged = new_items + [line for line in old_items if _item_key(line) not in keys]
            to_save = "\n".join(merged[:200]) + "\n"
        else:
            to_save = new
        path = self.snapshot_path(competitor_slug, source.id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(to_save)

        if old is None:
            return SourceDiff(source.id, source.type, source.url, is_new=True, diff="")
        diff = list_diff(old, new) if source.is_list else page_diff(old, new)
        return SourceDiff(source.id, source.type, source.url, is_new=False, diff=diff)

    def all_snapshots(self, competitor_slug: str) -> dict[str, str]:
        folder = self.root / "snapshots" / competitor_slug
        if not folder.exists():
            return {}
        return {p.stem: p.read_text() for p in sorted(folder.glob("*.md"))}

    # ── reports, digests, change log ────────────────────────────

    def report_path(self, competitor_slug: str, framework: str) -> Path:
        return self.root / "reports" / competitor_slug / f"{framework}.md"

    def read_report(self, competitor_slug: str, framework: str) -> str | None:
        path = self.report_path(competitor_slug, framework)
        return path.read_text() if path.exists() else None

    def write_report(self, competitor_slug: str, framework: str, text: str) -> Path:
        path = self.report_path(competitor_slug, framework)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n")
        return path

    def all_reports(self) -> dict[str, str]:
        folder = self.root / "reports"
        return {str(p.relative_to(folder)): p.read_text() for p in sorted(folder.glob("*/*.md"))} if folder.exists() else {}

    def write_digest(self, day: str, text: str) -> Path:
        path = self.root / "digests" / f"{day}.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text.rstrip() + "\n")
        return path

    def recent_digests(self, n: int = 4) -> dict[str, str]:
        folder = self.root / "digests"
        if not folder.exists():
            return {}
        return {p.stem: p.read_text() for p in sorted(folder.glob("*.md"))[-n:]}

    def append_changes(self, changes: list[dict]) -> None:
        path = self.root / "changes.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a") as f:
            for change in changes:
                f.write(json.dumps(change, ensure_ascii=False) + "\n")

    def read_changes(self, limit: int = 300) -> list[dict]:
        path = self.root / "changes.jsonl"
        if not path.exists():
            return []
        lines = path.read_text().splitlines()[-limit:]
        return [json.loads(line) for line in lines if line.strip()]
