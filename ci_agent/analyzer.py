"""Turn raw diffs into intelligence: classify changes, update reports, write the digest."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .config import SIGNIFICANCE, Config, Competitor
from .llm import LLM
from .store import SourceDiff, Store

BUILTIN_FRAMEWORKS = Path(__file__).parent / "frameworks"
MAX_SNAPSHOT_CHARS = 60_000  # per source, to keep prompts bounded

CATEGORIES = ["pricing", "product", "positioning", "go_to_market", "hiring", "corporate", "customers", "other"]

CHANGE_SCHEMA = {
    "type": "object",
    "properties": {
        "changes": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "source": {"type": "string", "description": "source id the change was seen in"},
                    "category": {"type": "string", "enum": CATEGORIES},
                    "significance": {"type": "string", "enum": SIGNIFICANCE},
                    "summary": {"type": "string", "description": "one sentence: what changed"},
                    "evidence": {"type": "string", "description": "short quote or detail from the diff"},
                    "implication": {"type": "string", "description": "one sentence: why it matters for us"},
                },
                "required": ["source", "category", "significance", "summary", "evidence", "implication"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["changes"],
    "additionalProperties": False,
}

ANALYST_SYSTEM = (
    "You are a senior product marketing and competitive intelligence analyst. "
    "You are precise, evidence-based and commercially minded. You never invent facts: "
    "if the sources don't say something, you say it's unknown. Write in {language}."
)


class Analyzer:
    def __init__(self, cfg: Config, llm: LLM, store: Store):
        self.cfg = cfg
        self.llm = llm
        self.store = store
        self.system = ANALYST_SYSTEM.format(language=cfg.language)

    # ── 1. classify what changed ────────────────────────────────

    def classify(self, competitor: Competitor, diffs: list[SourceDiff]) -> list[dict]:
        changed = [d for d in diffs if d.changed]
        if not changed:
            return []
        blocks = "\n\n".join(
            f"### Source: {d.source_id} (type: {d.source_type}{', ' + d.url if d.url else ''})\n```diff\n{d.diff}\n```"
            for d in changed
        )
        prompt = f"""{self.cfg.company_context()}

Below are the changes detected this week on {competitor.name}'s public sources.
In page diffs, lines starting with '-' were removed and '+' were added. In feed/search diffs, every line is a new item.

{blocks}

List every MEANINGFUL change. Ignore noise: dates, counters, cookie banners, reordered content,
rotating testimonials, minor copy edits, A/B test variations, typo fixes.
Merge related lines into one change. Significance guide:
- high: pricing changes, major launches, new market entry, funding/M&A, repositioning
- medium: notable features, new integrations, key hires or hiring waves, messaging tweaks
- low: minor features, blog posts, small updates
Return an empty list if nothing meaningful changed."""
        result = self.llm.json(self.system, prompt, CHANGE_SCHEMA)
        today = date.today().isoformat()
        return [{"date": today, "competitor": competitor.name, **c} for c in result["changes"]]

    # ── 2. keep the per-competitor reports up to date ───────────

    def update_reports(self, competitor: Competitor, changes: list[dict], force: bool = False) -> list[Path]:
        """Regenerate each framework report if something changed or it doesn't exist yet."""
        written = []
        snapshots = self._snapshots_block(competitor)
        for framework in self.cfg.frameworks:
            previous = self.store.read_report(competitor.slug, framework)
            if previous and not changes and not force:
                continue
            instructions = self._framework_template(framework)
            instructions = instructions.replace("{competitor}", competitor.name).replace(
                "{today}", date.today().isoformat()
            )
            prompt = f"""{self.cfg.company_context()}

## Current public sources for {competitor.name}
{snapshots}

## Changes detected this run
{_changes_block(changes) or "None (baseline run or no meaningful changes)."}

## Previous version of this report
{previous or "None — this is the first version."}

## Your task
{instructions}

Update the previous version rather than starting over: keep what is still true, revise what changed, and
reflect the new changes. Output only the markdown report."""
            written.append(self.store.write_report(competitor.slug, framework, self.llm.text(self.system, prompt)))
        return written

    # ── 3. the digest across all competitors ────────────────────

    def digest(self, changes: list[dict], baseline: list[str], errors: list[str]) -> str:
        today = date.today().isoformat()
        if not changes:
            body = "No meaningful competitor changes were detected this run."
            if baseline:
                body += f"\n\nBaseline snapshots captured for: {', '.join(baseline)}. Changes will be tracked from the next run."
        else:
            prompt = f"""{self.cfg.company_context()}

These are all the meaningful competitor changes detected this run ({today}):

{_changes_block(changes)}

Write the weekly competitive intelligence digest in markdown with these sections:
## Executive summary  (3-5 bullets, most important first)
## Changes by competitor  (one subsection per competitor, most significant first; mark high-significance items with 🔴, medium with 🟠)
## What this means for us  (implications for positioning, messaging, sales and product)
## Recommended actions  (3-5 concrete actions, each with an owner function, e.g. PMM / Sales / Product)
Be concise. Do not repeat the same point across sections. Output only the markdown, without a top-level title."""
            body = self.llm.text(self.system, prompt)
        footer = []
        if baseline and changes:
            footer.append(f"Baseline captured for: {', '.join(baseline)}.")
        if errors:
            footer.append("Sources that could not be collected:\n" + "\n".join(f"- {e}" for e in errors))
        return f"# Competitive intelligence digest — {today}\n\n{body}\n" + (
            "\n---\n" + "\n\n".join(footer) + "\n" if footer else ""
        )

    # ── helpers ─────────────────────────────────────────────────

    def _snapshots_block(self, competitor: Competitor) -> str:
        parts = []
        for source_id, text in self.store.all_snapshots(competitor.slug).items():
            if len(text) > MAX_SNAPSHOT_CHARS:
                text = text[:MAX_SNAPSHOT_CHARS] + "\n… (truncated)"
            parts.append(f"### {source_id}\n{text}")
        return "\n\n".join(parts) or "No snapshots available."

    def _framework_template(self, name: str) -> str:
        candidates = []
        if self.cfg.frameworks_dir:
            candidates.append(self.cfg.base_dir / self.cfg.frameworks_dir / f"{name}.md")
        candidates.append(BUILTIN_FRAMEWORKS / f"{name}.md")
        for path in candidates:
            if path.exists():
                return path.read_text()
        raise FileNotFoundError(f"framework '{name}' not found in {', '.join(str(c.parent) for c in candidates)}")


def _changes_block(changes: list[dict]) -> str:
    return "\n".join(
        f"- [{c['competitor']}] [{c['significance']}] [{c['category']}] {c['summary']} "
        f"(evidence: {c['evidence']}; implication: {c['implication']})"
        for c in changes
    )


def significant(changes: list[dict], minimum: str) -> list[dict]:
    threshold = SIGNIFICANCE.index(minimum)
    return [c for c in changes if SIGNIFICANCE.index(c["significance"]) >= threshold]
