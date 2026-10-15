"""The weekly run: collect -> snapshot & diff -> classify -> update reports -> digest -> notify."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

from .analyzer import Analyzer, significant
from .collectors import collect
from .config import Config
from .llm import LLM
from .outputs import send
from .store import Store


@dataclass
class RunResult:
    changes: list[dict] = field(default_factory=list)
    baseline: list[str] = field(default_factory=list)  # competitors seen for the first time
    errors: list[str] = field(default_factory=list)
    reports: list[Path] = field(default_factory=list)
    digest_path: Path | None = None
    notifications: list[str] = field(default_factory=list)


def run(
    cfg: Config,
    llm: LLM | None,
    only: str | None = None,
    notify: bool = True,
    refresh_reports: bool = False,
    log=print,
) -> RunResult:
    """llm=None means 'collect and diff only' (no Claude calls; search sources are skipped)."""
    store = Store(cfg.data_path)
    analyzer = Analyzer(cfg, llm, store) if llm else None
    result = RunResult()

    competitors = [c for c in cfg.competitors if not only or c.name.lower() == only.lower()]
    if not competitors:
        raise ValueError(f"no competitor named '{only}' in the config")

    for competitor in competitors:
        log(f"\n▶ {competitor.name}")
        diffs = []
        for source in competitor.sources:
            if llm is None and source.is_list and source.type != "rss":
                log(f"  · {source.id}: skipped (needs Claude)")
                continue
            try:
                text = collect(source, competitor, llm)
            except Exception as e:  # one broken source must not stop the whole run
                result.errors.append(f"{competitor.name} / {source.id}: {e}")
                log(f"  ✗ {source.id}: {e}")
                continue
            d = store.update_snapshot(competitor.slug, source, text)
            diffs.append(d)
            status = "baseline" if d.is_new else ("changed" if d.changed else "no change")
            log(f"  · {source.id}: {status}")

        if diffs and all(d.is_new for d in diffs):
            result.baseline.append(competitor.name)
        if analyzer is None:
            continue

        changes = analyzer.classify(competitor, diffs)
        store.append_changes(changes)
        result.changes.extend(changes)
        log(f"  → {len(changes)} meaningful change(s)")
        result.reports.extend(analyzer.update_reports(competitor, changes, force=refresh_reports))

    if analyzer is None:
        return result

    digest = analyzer.digest(result.changes, result.baseline, result.errors)
    result.digest_path = store.write_digest(date.today().isoformat(), digest)
    log(f"\n📄 Digest saved to {result.digest_path}")

    if notify:
        alerts = significant(result.changes, cfg.alert_min_significance)
        if alerts and cfg.alert_outputs:
            body = "\n".join(
                f"- **{c['competitor']}** ({c['category']}): {c['summary']}\n  _Why it matters:_ {c['implication']}"
                for c in alerts
            )
            title = f"🚨 {len(alerts)} important competitor change(s) — {date.today().isoformat()}"
            result.notifications += send(cfg, cfg.alert_outputs, title, body)
        if cfg.outputs and (result.changes or cfg.notify_when_no_changes):
            title = f"Competitive intelligence digest — {date.today().isoformat()}"
            result.notifications += send(cfg, cfg.outputs, title, digest)
        for n in result.notifications:
            log(f"  {n}")
    return result
