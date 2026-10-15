"""Send digests and alerts. Secrets come from environment variables, never from config.yaml.

To add a new destination, write `send_x(cfg, title, body)` and register it in SENDERS.
"""

from __future__ import annotations

import os
import smtplib
from email.message import EmailMessage

import httpx

from .config import Config

GITHUB_BODY_LIMIT = 65_000  # GitHub's issue body limit is 65,536 characters
SLACK_TEXT_LIMIT = 3_500


class OutputError(RuntimeError):
    pass


def _env(name: str) -> str:
    value = os.environ.get(name, "")
    if not value:
        raise OutputError(f"environment variable {name} is not set")
    return value


def send_github_issue(cfg: Config, title: str, body: str) -> str:
    repo = cfg.github_repo or _env("GITHUB_REPOSITORY")
    if len(body) > GITHUB_BODY_LIMIT:
        body = body[:GITHUB_BODY_LIMIT] + "\n\n… (truncated — full text in the data/ folder)"
    resp = httpx.post(
        f"https://api.github.com/repos/{repo}/issues",
        headers={"Authorization": f"Bearer {_env('GITHUB_TOKEN')}", "Accept": "application/vnd.github+json"},
        json={"title": title, "body": body, "labels": cfg.github_labels},
        timeout=30,
    )
    if resp.status_code >= 300:
        raise OutputError(f"GitHub issue creation failed ({resp.status_code}): {resp.text[:300]}")
    return resp.json()["html_url"]


def send_slack(cfg: Config, title: str, body: str) -> str:
    text = f"*{title}*\n{body}"
    if len(text) > SLACK_TEXT_LIMIT:
        text = text[:SLACK_TEXT_LIMIT] + "\n… (truncated)"
    resp = httpx.post(_env("SLACK_WEBHOOK_URL"), json={"text": text}, timeout=30)
    if resp.status_code >= 300:
        raise OutputError(f"Slack webhook failed ({resp.status_code}): {resp.text[:300]}")
    return "slack"


def send_email(cfg: Config, title: str, body: str) -> str:
    if not cfg.email_to:
        raise OutputError("email output enabled but `email.to` is empty in config")
    msg = EmailMessage()
    msg["Subject"] = title
    msg["From"] = os.environ.get("SMTP_FROM") or _env("SMTP_USER")
    msg["To"] = ", ".join(cfg.email_to)
    msg.set_content(body)
    with smtplib.SMTP(_env("SMTP_HOST"), int(os.environ.get("SMTP_PORT", "587"))) as smtp:
        smtp.starttls()
        smtp.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
        smtp.send_message(msg)
    return f"email to {msg['To']}"


SENDERS = {"github_issue": send_github_issue, "slack": send_slack, "email": send_email}


def send(cfg: Config, outputs: list[str], title: str, body: str) -> list[str]:
    """Send to every configured output. One failing output doesn't stop the others."""
    results = []
    for name in outputs:
        try:
            results.append(f"{name}: sent ({SENDERS[name](cfg, title, body)})")
        except (OutputError, httpx.HTTPError, smtplib.SMTPException, OSError) as e:
            results.append(f"{name}: FAILED — {e}")
    return results
