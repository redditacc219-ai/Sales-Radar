#!/usr/bin/env python3
"""
Sales Radar — hiring signals module (Phase 1: MyCareersFuture).

For every account in accounts.yaml this module:
  1. Searches the public MCF API for the account's postings (name + aliases).
  2. Classifies each posting into a function bucket (IT, Transformation,
     Finance, Ops ...) via title keywords.
  3. Diffs against data/seen_jobs.json (the repo is the database).
  4. Emits three kinds of signal lines for the LLM:
       [JOBS-HEADCOUNT]  N+ new roles in a function within the rolling window
       [JOBS-KEYROLE]    a single buyer-seat / builder role posted
       [JOBS-STACK]      a posting title naming competitor tech

The MCF API is public but undocumented; endpoint patterns are isolated in
MCF_STRATEGIES below so a future change is a one-block patch.
"""

import json
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

ROOT = Path(__file__).parent
STATE_FILE = ROOT / "data" / "seen_jobs.json"
UA = {"User-Agent": "sales-radar/1.0 (personal daily account monitor)"}

# Known public endpoint patterns, tried in order. Each returns (url, method,
# params/json builder). Patch here if MCF changes.
MCF_STRATEGIES = [
    {
        "name": "gov-v2-search-post",
        "method": "POST",
        "url": "https://api.mycareersfuture.gov.sg/v2/search",
        "params": {"limit": 100, "page": 0},
        "json": lambda q: {"sessionId": "", "search": q,
                           "sortBy": ["new_posting_date"]},
        "results_key": "results",
    },
    {
        "name": "api1-v2-jobs-get",
        "method": "GET",
        "url": "https://api1.mycareersfuture.sg/v2/jobs",
        "params": lambda q: {"limit": 100, "offset": 0, "search": q},
        "json": None,
        "results_key": "results",
    },
]


# ------------------------------------------------------------------ helpers

def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9 ]", "", (s or "").lower()).strip()


def _company_matches(posted_name: str, aliases: list[str]) -> bool:
    pn = _norm(posted_name)
    if not pn:
        return False
    return any(_norm(a) in pn or pn in _norm(a) for a in aliases if a)


def _kw_hit(title_norm: str, keyword: str) -> bool:
    """Whole-word/phrase match so 'IT' can't hit 'recruitment'
    and 'head of AI' can't hit 'head of air cargo'."""
    kw = _norm(keyword)
    if not kw:
        return False
    return re.search(rf"\b{re.escape(kw)}\b", title_norm) is not None


def _classify(title: str, functions: dict[str, list[str]]) -> str | None:
    t = _norm(title)
    for func, keywords in functions.items():
        if any(_kw_hit(t, k) for k in keywords):
            return func
    return None


def _matches_any(title: str, keywords: list[str]) -> str | None:
    t = _norm(title)
    for k in keywords:
        if _kw_hit(t, k):
            return k
    return None


def _parse_date(value) -> datetime | None:
    if not value:
        return None
    for fmt in ("%Y-%m-%d", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f"):
        try:
            return datetime.strptime(str(value)[:26], fmt).replace(
                tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


# ---------------------------------------------------------------- MCF fetch

def mcf_search(query: str) -> list[dict]:
    """Search MCF; returns raw job dicts. Tries endpoint strategies in order."""
    for strat in MCF_STRATEGIES:
        try:
            if strat["method"] == "POST":
                resp = requests.post(strat["url"], params=strat["params"],
                                     json=strat["json"](query), headers=UA,
                                     timeout=30)
            else:
                params = strat["params"](query) if callable(strat["params"]) \
                    else strat["params"]
                resp = requests.get(strat["url"], params=params, headers=UA,
                                    timeout=30)
            if resp.status_code != 200:
                continue
            data = resp.json()
            results = data.get(strat["results_key"]) or data.get("jobs") or []
            if isinstance(results, list):
                return results
        except Exception as exc:
            print(f"    ! MCF strategy {strat['name']} failed: {exc}",
                  file=sys.stderr)
    return []


def extract_job(raw: dict) -> dict | None:
    """Normalise one MCF job record defensively across schema variants."""
    job_id = raw.get("uuid") or raw.get("id") or raw.get("jobPostId")
    title = raw.get("title") or raw.get("jobTitle") or ""
    company = ""
    pc = raw.get("postedCompany") or raw.get("hiringCompany") or {}
    if isinstance(pc, dict):
        company = pc.get("name", "")
    meta = raw.get("metadata") or {}
    posted = (_parse_date(meta.get("newPostingDate"))
              or _parse_date(meta.get("originalPostingDate"))
              or _parse_date(raw.get("postedDate")))
    link = ""
    links = raw.get("_links") or {}
    if isinstance(links, dict):
        link = (links.get("self") or {}).get("href", "")
    if not link and job_id:
        link = f"https://www.mycareersfuture.gov.sg/job/{job_id}"
    if not job_id or not title:
        return None
    return {"id": str(job_id), "title": title.strip(), "company": company,
            "posted": posted, "link": link}


# ------------------------------------------------------------------- state

def load_state() -> dict:
    if STATE_FILE.exists():
        try:
            return json.loads(STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"jobs": {}}


def save_state(state: dict, retention_days: int) -> None:
    cutoff = (datetime.now(timezone.utc)
              - timedelta(days=retention_days)).date().isoformat()
    state["jobs"] = {k: v for k, v in state["jobs"].items()
                     if v.get("first_seen", "9999") >= cutoff}
    STATE_FILE.parent.mkdir(exist_ok=True)
    STATE_FILE.write_text(json.dumps(state, indent=1, sort_keys=True),
                          encoding="utf-8")


# -------------------------------------------------------------------- main

def collect_job_signals(config: dict) -> list[str]:
    jc = config.get("jobs", {})
    if not jc.get("enabled", True):
        return []

    functions: dict = jc.get("functions", {})
    key_roles: list = jc.get("key_roles", [])
    stack: list = jc.get("competitor_stack", [])
    window_days = int(jc.get("window_days", 14))
    threshold = int(jc.get("headcount_threshold", 3))
    retention = int(jc.get("retention_days", 90))
    alias_map: dict = jc.get("aliases", {})
    delay = float(jc.get("request_delay_seconds", 0.8))

    state = load_state()
    today = datetime.now(timezone.utc).date()
    window_start = (today - timedelta(days=window_days)).isoformat()
    signals: list[str] = []
    new_today: dict[str, list[dict]] = {}   # account -> new postings this run

    for acct in config.get("accounts", []):
        name = acct["name"]
        aliases = [name] + alias_map.get(name, [])
        found: dict[str, dict] = {}
        for alias in aliases:
            for raw in mcf_search(alias):
                job = extract_job(raw)
                if job and _company_matches(job["company"], aliases):
                    found[job["id"]] = job
            time.sleep(delay)
        print(f"  jobs [{acct.get('tier','B')}] {name}: "
              f"{len(found)} matching postings")

        for jid, job in found.items():
            if jid in state["jobs"]:
                continue
            first_seen = (job["posted"].date().isoformat()
                          if job["posted"] else today.isoformat())
            func = _classify(job["title"], functions)
            state["jobs"][jid] = {
                "account": name, "title": job["title"],
                "function": func or "Other", "first_seen": first_seen,
                "link": job["link"],
            }
            if first_seen >= window_start:
                new_today.setdefault(name, []).append(
                    {**job, "function": func, "first_seen": first_seen})

    # ---- build signal lines ----
    for name, jobs in new_today.items():
        # single-posting exceptions
        for job in jobs:
            hit = _matches_any(job["title"], key_roles)
            if hit:
                signals.append(
                    f"[JOBS-KEYROLE] {name} | {job['title']} | "
                    f"posted {job['first_seen']} | {job['link']}")
            tech = _matches_any(job["title"], stack)
            if tech:
                signals.append(
                    f"[JOBS-STACK] {name} | '{job['title']}' names "
                    f"{tech} | posted {job['first_seen']} | {job['link']}")

        # headcount triggers: rolling window count per function,
        # surfaced only on days with fresh postings in that function
        fresh_funcs = {j["function"] for j in jobs if j["function"]}
        for func in fresh_funcs:
            rolling = [v for v in state["jobs"].values()
                       if v["account"] == name and v["function"] == func
                       and v["first_seen"] >= window_start]
            if len(rolling) >= threshold:
                titles = "; ".join(sorted({v["title"] for v in rolling})[:6])
                signals.append(
                    f"[JOBS-HEADCOUNT] {name} | {func} | "
                    f"{len(rolling)} new roles in last {window_days}d | "
                    f"titles: {titles}")

    save_state(state, retention)
    return signals


if __name__ == "__main__":
    import yaml
    cfg = yaml.safe_load((ROOT / "accounts.yaml").read_text(encoding="utf-8"))
    for line in collect_job_signals(cfg):
        print(line)
