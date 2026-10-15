"""Load and validate config.yaml into typed objects.

Everything company-specific lives in the config file, so the rest of the
code never needs to know which company or industry it is analysing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

PAGE_TYPES = {"homepage", "pricing", "changelog", "careers", "blog", "docs", "page"}
SEARCH_TYPES = {"news", "reviews", "search"}
FEED_TYPES = {"rss"}
SOURCE_TYPES = PAGE_TYPES | SEARCH_TYPES | FEED_TYPES

OUTPUT_TYPES = {"github_issue", "slack", "email"}
SIGNIFICANCE = ["low", "medium", "high"]
EFFORTS = {"low", "medium", "high", "xhigh", "max"}


class ConfigError(ValueError):
    """Raised when config.yaml is missing something or has a wrong value."""


def slugify(text: str) -> str:
    """'Competitor A, Inc.' -> 'competitor-a-inc' (safe for folder names)."""
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "unnamed"


@dataclass
class Source:
    type: str
    url: str | None = None
    query: str | None = None
    name: str | None = None
    render: bool = False

    @property
    def id(self) -> str:
        """Unique id within a competitor, used as the snapshot file name."""
        return slugify(self.name or self.type)

    @property
    def is_list(self) -> bool:
        """List-like sources (feeds, searches) are diffed by new items, not by line changes."""
        return self.type in SEARCH_TYPES | FEED_TYPES


@dataclass
class Competitor:
    name: str
    sources: list[Source]
    website: str | None = None

    @property
    def slug(self) -> str:
        return slugify(self.name)


@dataclass
class Company:
    name: str = ""
    website: str = ""
    description: str = ""
    positioning_file: str | None = None


@dataclass
class LLMConfig:
    model: str = "claude-sonnet-5-5"
    effort: str = "medium"
    fallbacks: bool = True


@dataclass
class Config:
    competitors: list[Competitor]
    company: Company = field(default_factory=Company)
    llm: LLMConfig = field(default_factory=LLMConfig)
    frameworks: list[str] = field(default_factory=lambda: ["battlecard", "positioning", "swot"])
    frameworks_dir: str | None = None
    data_dir: str = "data"
    outputs: list[str] = field(default_factory=list)
    alert_min_significance: str = "high"
    alert_outputs: list[str] = field(default_factory=list)
    github_repo: str = ""
    github_labels: list[str] = field(default_factory=lambda: ["competitive-intel"])
    email_to: list[str] = field(default_factory=list)
    notify_when_no_changes: bool = False
    language: str = "English"
    base_dir: Path = Path(".")  # folder containing config.yaml; relative paths resolve from here

    @property
    def data_path(self) -> Path:
        return self.base_dir / self.data_dir

    def company_context(self) -> str:
        """A text block describing 'us', fed to Claude so analysis is relative to our company."""
        if not self.company.name:
            return "No information about our own company was provided; analyse competitors neutrally."
        parts = [f"Our company: {self.company.name}"]
        if self.company.website:
            parts.append(f"Website: {self.company.website}")
        if self.company.description:
            parts.append(f"Description: {self.company.description}")
        if self.company.positioning_file:
            path = self.base_dir / self.company.positioning_file
            if path.exists():
                parts.append(f"Our positioning and messaging:\n{path.read_text()}")
        return "\n".join(parts)


def _check_choice(value: str, allowed, where: str) -> str:
    if value not in allowed:
        raise ConfigError(f"{where}: '{value}' is not valid. Use one of: {', '.join(sorted(allowed))}")
    return value


def _parse_source(raw: dict, competitor: str) -> Source:
    where = f"competitor '{competitor}' source {raw}"
    if not isinstance(raw, dict) or "type" not in raw:
        raise ConfigError(f"{where}: every source needs a 'type'")
    src = Source(
        type=_check_choice(raw["type"], SOURCE_TYPES, where),
        url=raw.get("url"),
        query=raw.get("query"),
        name=raw.get("name"),
        render=bool(raw.get("render", False)),
    )
    if (src.type in PAGE_TYPES or src.type in FEED_TYPES) and not src.url:
        raise ConfigError(f"{where}: type '{src.type}' needs a 'url'")
    if src.type in {"page", "search"} and not src.name:
        raise ConfigError(f"{where}: type '{src.type}' needs a unique 'name'")
    if src.type == "search" and not src.query:
        raise ConfigError(f"{where}: type 'search' needs a 'query'")
    return src


def parse_config(data: dict, base_dir: Path = Path(".")) -> Config:
    if not isinstance(data, dict):
        raise ConfigError("config must be a YAML mapping")
    raw_competitors = data.get("competitors") or []
    if not raw_competitors:
        raise ConfigError("add at least one competitor under 'competitors:'")

    competitors = []
    for raw in raw_competitors:
        if not raw.get("name"):
            raise ConfigError(f"competitor {raw} needs a 'name'")
        sources = [_parse_source(s, raw["name"]) for s in raw.get("sources") or []]
        if not sources:
            raise ConfigError(f"competitor '{raw['name']}' has no sources")
        ids = [s.id for s in sources]
        dupes = {i for i in ids if ids.count(i) > 1}
        if dupes:
            raise ConfigError(
                f"competitor '{raw['name']}' has several sources with id {dupes}; give them distinct 'name's"
            )
        competitors.append(Competitor(name=raw["name"], website=raw.get("website"), sources=sources))

    slugs = [c.slug for c in competitors]
    if len(set(slugs)) != len(slugs):
        raise ConfigError("two competitors have the same (or too similar) name")

    company = Company(**{k: v for k, v in (data.get("company") or {}).items() if k in Company.__dataclass_fields__})

    llm_raw = data.get("llm") or {}
    llm = LLMConfig(
        model=llm_raw.get("model", LLMConfig.model),
        effort=_check_choice(llm_raw.get("effort", LLMConfig.effort), EFFORTS, "llm.effort"),
        fallbacks=bool(llm_raw.get("fallbacks", True)),
    )

    outputs = data.get("outputs") or []
    alerts = data.get("alerts") or {}
    alert_outputs = alerts.get("outputs") or []
    for o in outputs + alert_outputs:
        _check_choice(o, OUTPUT_TYPES, "outputs")

    return Config(
        competitors=competitors,
        company=company,
        llm=llm,
        frameworks=data.get("frameworks") or ["battlecard", "positioning", "swot"],
        frameworks_dir=data.get("frameworks_dir"),
        data_dir=data.get("data_dir", "data"),
        outputs=outputs,
        alert_min_significance=_check_choice(
            alerts.get("min_significance", "high"), set(SIGNIFICANCE), "alerts.min_significance"
        ),
        alert_outputs=alert_outputs,
        github_repo=(data.get("github") or {}).get("repo", ""),
        github_labels=(data.get("github") or {}).get("labels", ["competitive-intel"]),
        email_to=(data.get("email") or {}).get("to", []),
        notify_when_no_changes=bool(data.get("notify_when_no_changes", False)),
        language=data.get("language", "English"),
        base_dir=base_dir,
    )


def load_config(path: str | Path) -> Config:
    path = Path(path)
    if not path.exists():
        raise ConfigError(f"{path} not found. Create it with `ci-agent init` or copy config.example.yaml")
    return parse_config(yaml.safe_load(path.read_text()), base_dir=path.resolve().parent)
