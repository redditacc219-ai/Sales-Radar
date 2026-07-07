#!/usr/bin/env python3
"""
Sales Radar — daily account-news brief generator.

Pipeline:
  1. Read accounts.yaml (tiers, queries, sectors).
  2. Pull Google News RSS for every account + sector feed.
  3. Keep only items inside the lookback window, dedupe across feeds.
  4. Send candidates + prompt.md to Claude:
       - via `claude -p` if CLAUDE_CODE_OAUTH_TOKEN is set (subscription /
         Agent SDK credit — the $0-extra path), else
       - via the Anthropic API if ANTHROPIC_API_KEY is set.
  5. Render docs/index.html from template.html and archive a dated copy.

Environment variables:
  WINDOW_HOURS   lookback window (default 24; use 168 for the first run)
  MODEL          model to use (default claude-sonnet-4-6)
  GITHUB_REPOSITORY  set automatically inside GitHub Actions
"""

import html
import os
import re
import subprocess
import sys
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import feedparser
import requests
import yaml
import markdown as md

ROOT = Path(__file__).parent
DOCS = ROOT / "docs"
ARCHIVE = DOCS / "archive"
SGT = ZoneInfo("Asia/Singapore")

WINDOW_HOURS = int(os.environ.get("WINDOW_HOURS", "24"))
MODEL = os.environ.get("MODEL", "claude-sonnet-4-6")
RSS_BASE = "https://news.google.com/rss/search?q={q}&hl=en-SG&gl=SG&ceid=SG:en"


# ----------------------------------------------------------------- fetching

def fetch_feed(query: str, cap: int, cutoff_utc: datetime) -> list[dict]:
    """Fetch one Google News RSS feed, keep items newer than cutoff."""
    url = RSS_BASE.format(q=urllib.parse.quote(query))
    items = []
    try:
        parsed = feedparser.parse(url)
    except Exception as exc:  # network hiccup on one feed shouldn't kill the run
        print(f"  ! feed error for '{query}': {exc}", file=sys.stderr)
        return items
    for entry in parsed.entries:
        ts = entry.get("published_parsed") or entry.get("updated_parsed")
        if not ts:
            continue
        published = datetime.fromtimestamp(time.mktime(ts), tz=timezone.utc)
        if published < cutoff_utc:
            continue
        title = (entry.get("title") or "").strip()
        if not title:
            continue
        source = ""
        if entry.get("source") and entry.source.get("title"):
            source = entry.source.title
        elif " - " in title:  # Google News suffixes titles with " - Source"
            source = title.rsplit(" - ", 1)[-1]
        clean_title = title.rsplit(" - ", 1)[0] if " - " in title else title
        items.append({
            "title": clean_title,
            "source": source,
            "link": entry.get("link", ""),
            "published": published,
        })
        if len(items) >= cap:
            break
    return items


def norm_title(title: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", title.lower()).strip()


def collect_candidates(config: dict) -> list[str]:
    cutoff = datetime.now(timezone.utc) - timedelta(hours=WINDOW_HOURS)
    settings = config.get("settings", {})
    cap_a = int(settings.get("max_headlines_per_feed_tier_a", 10))
    cap_b = int(settings.get("max_headlines_per_feed_tier_b", 6))
    cap_s = int(settings.get("max_headlines_per_sector_feed", 6))

    seen: set[str] = set()
    lines: list[str] = []

    def add(tag: str, label: str, sector: str, items: list[dict], note: str = ""):
        for it in items:
            key = norm_title(it["title"])
            if not key or key in seen:
                continue
            seen.add(key)
            when = it["published"].astimezone(SGT).strftime("%d %b %H:%M")
            note_part = f" | note: {note}" if note else ""
            lines.append(
                f"[{tag}] {label} ({sector}) | {when} | {it['title']} | "
                f"{it['source']} | {it['link']}{note_part}"
            )

    accounts = sorted(config.get("accounts", []),
                      key=lambda a: 0 if a.get("tier") == "A" else 1)
    for acct in accounts:
        tier = acct.get("tier", "B").upper()
        cap = cap_a if tier == "A" else cap_b
        print(f"  fetching [{tier}] {acct['name']} ...")
        items = fetch_feed(acct["query"], cap, cutoff)
        add(tier, acct["name"], acct.get("sector", ""), items,
            acct.get("note", ""))

    for feed in config.get("sector_feeds", []):
        print(f"  fetching [SECTOR] {feed['sector']} ...")
        items = fetch_feed(feed["query"], cap_s, cutoff)
        add("SECTOR", feed["sector"], feed["sector"], items)

    return lines


# ------------------------------------------------------------------- claude

def run_claude(prompt: str) -> str:
    oauth = os.environ.get("CLAUDE_CODE_OAUTH_TOKEN", "").strip()
    api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()

    if oauth:
        print("  brain: Claude subscription (claude -p, Agent SDK credit)")
        result = subprocess.run(
            ["claude", "-p", "--model", MODEL, "--output-format", "text",
             "You will receive a full task specification on stdin. "
             "Execute it exactly and output only the final markdown brief."],
            input=prompt, capture_output=True, text=True, timeout=600,
        )
        if result.returncode != 0:
            raise RuntimeError(f"claude -p failed: {result.stderr[:2000]}")
        return result.stdout.strip()

    if api_key:
        print("  brain: Anthropic API (pay-as-you-go)")
        resp = requests.post(
            "https://api.anthropic.com/v1/messages",
            headers={
                "x-api-key": api_key,
                "anthropic-version": "2023-06-01",
                "content-type": "application/json",
            },
            json={
                "model": MODEL,
                "max_tokens": 6000,
                "messages": [{"role": "user", "content": prompt}],
            },
            timeout=600,
        )
        resp.raise_for_status()
        blocks = resp.json().get("content", [])
        return "\n".join(b.get("text", "") for b in blocks
                         if b.get("type") == "text").strip()

    raise RuntimeError(
        "No credentials found. Set the CLAUDE_CODE_OAUTH_TOKEN secret "
        "(subscription path) or ANTHROPIC_API_KEY (pay-as-you-go path)."
    )


# ------------------------------------------------------------------ render

def strip_code_fence(text: str) -> str:
    text = text.strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\n", "", text)
        text = re.sub(r"\n```\s*$", "", text)
    return text.strip()


def archive_links() -> str:
    pages = sorted(ARCHIVE.glob("*.html"), reverse=True)[:14]
    if not pages:
        return ""
    links = " · ".join(
        f'<a href="archive/{p.name}">{p.stem}</a>' for p in pages
    )
    return f"Past briefs: {links}"


def render(content_md: str, date_human: str) -> None:
    template = (ROOT / "template.html").read_text(encoding="utf-8")
    body = md.markdown(content_md, extensions=["tables", "sane_lists"])
    repo = os.environ.get("GITHUB_REPOSITORY", "")
    actions_url = f"https://github.com/{repo}/actions" if repo else "#"
    generated = datetime.now(SGT).strftime("%H:%M SGT")

    page = (template
            .replace("{{DATE_HUMAN}}", html.escape(date_human))
            .replace("{{GENERATED_AT}}", generated)
            .replace("{{WINDOW_HOURS}}", str(WINDOW_HOURS))
            .replace("{{ACTIONS_URL}}", actions_url)
            .replace("{{ARCHIVE_LINKS}}", archive_links())
            .replace("{{CONTENT}}", body))

    DOCS.mkdir(exist_ok=True)
    ARCHIVE.mkdir(exist_ok=True)
    (DOCS / "index.html").write_text(page, encoding="utf-8")
    stamp = datetime.now(SGT).strftime("%Y-%m-%d")
    (ARCHIVE / f"{stamp}.html").write_text(page, encoding="utf-8")
    print(f"  wrote docs/index.html and docs/archive/{stamp}.html")


# -------------------------------------------------------------------- main

def main() -> None:
    config = yaml.safe_load((ROOT / "accounts.yaml").read_text(encoding="utf-8"))
    now_sgt = datetime.now(SGT)
    date_human = now_sgt.strftime("%a %d %b %Y")

    print(f"Sales Radar · {date_human} · window {WINDOW_HOURS}h")
    candidates = collect_candidates(config)
    print(f"  {len(candidates)} candidate headlines after dedupe")

    job_signals: list[str] = []
    try:
        from jobs_radar import collect_job_signals
        print("  scanning MyCareersFuture for hiring signals ...")
        job_signals = collect_job_signals(config)
        print(f"  {len(job_signals)} hiring signals")
    except Exception as exc:
        print(f"  ! hiring-signals scan failed (news brief continues): {exc}",
              file=sys.stderr)

    if not candidates and not job_signals:
        content = (f"# 📡 Sales Radar — {date_human}\n\n"
                   f"⚪ **Completely quiet** — no headlines across any account "
                   f"or sector in the last {WINDOW_HOURS}h. Rare, but it "
                   f"happens. Nothing to act on today.")
        render(content, date_human)
        return

    prompt = (ROOT / "prompt.md").read_text(encoding="utf-8")
    prompt = (prompt
              .replace("{SELLER_CONTEXT}",
                       config.get("seller_context", "").strip())
              .replace("{WINDOW_HOURS}", str(WINDOW_HOURS))
              .replace("{DATE}", date_human)
              .replace("{CANDIDATES}", "\n".join(candidates) or "(none)")
              .replace("{JOB_SIGNALS}",
                       "\n".join(job_signals) or "(none today)"))

    brief = strip_code_fence(run_claude(prompt))
    if not brief:
        raise RuntimeError("Model returned an empty brief.")
    render(brief, date_human)
    print("Done.")


if __name__ == "__main__":
    main()
