#!/usr/bin/env python3
"""Claude tokens spent by each listing-writer agent, read from local Claude Code transcripts.

Best effort: Claude Code keeps subagent transcripts under
~/.claude/projects/<project dir>/<session>/subagents/agent-*.jsonl with a sibling .meta.json
naming the agent type. The format is Claude Code's own and may change, so every failure
returns nothing instead of raising. One API response can span several transcript lines
that repeat its usage, so usage is counted once per message id.
"""
from __future__ import annotations

import json
import re
from pathlib import Path


def project_dir(cwd: Path, home: Path | None = None) -> Path:
    """Claude Code names a project's folder after its path with every non-alphanumeric
    character replaced by '-' (/Users/a_b/My Repo -> -Users-a-b-My-Repo)."""
    return (home or Path.home()) / ".claude/projects" / re.sub(r"[^A-Za-z0-9]", "-", str(cwd))


def _first_prompt(rows: list[dict]) -> str:
    for r in rows:
        if r.get("type") == "user":
            c = (r.get("message") or {}).get("content")
            if isinstance(c, str):
                return c
            if isinstance(c, list):
                return " ".join(b.get("text", "") for b in c if isinstance(b, dict))
    return ""


def agent_usage(path: Path) -> dict | None:
    try:
        rows = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    except (OSError, ValueError):
        return None
    usage, turns = {}, []
    for r in rows:
        msg = r.get("message") or {}
        if r.get("type") == "assistant" and isinstance(msg, dict) and msg.get("id"):
            if msg["id"] not in usage:
                turns.append(msg["id"])
            if isinstance(msg.get("usage"), dict):
                usage[msg["id"]] = msg["usage"]
    total = peak = 0
    for u in usage.values():
        n = sum(int(u.get(k) or 0) for k in ("input_tokens", "cache_creation_input_tokens",
                                             "cache_read_input_tokens", "output_tokens"))
        total, peak = total + n, max(peak, n)
    return {"prompt": _first_prompt(rows), "turns": len(turns), "total_tokens": total, "peak_context": peak}


def writer_usage(slugs: list[str], date: str, cwd: Path, home: Path | None = None) -> dict[str, dict]:
    """{slug: usage} for listing-writer agents whose prompt names output/<date>/<slug>.
    Several agents for one listing (a retry) are summed."""
    out: dict[str, dict] = {}
    root = project_dir(cwd, home)
    for meta in root.glob("*/subagents/agent-*.meta.json"):
        try:
            if json.loads(meta.read_text(encoding="utf-8-sig")).get("agentType") != "listing-writer":
                continue
        except (OSError, ValueError):
            continue
        u = agent_usage(meta.with_name(meta.name.replace(".meta.json", ".jsonl")))
        if not u:
            continue
        for slug in slugs:
            if f"output/{date}/{slug}" in u["prompt"] or re.search(rf"\bslug:?\s*{re.escape(slug)}\b", u["prompt"]):
                if date not in u["prompt"]:
                    continue
                agg = out.setdefault(slug, {"agents": 0, "turns": 0, "total_tokens": 0, "peak_context": 0})
                agg["agents"] += 1
                agg["turns"] += u["turns"]
                agg["total_tokens"] += u["total_tokens"]
                agg["peak_context"] = max(agg["peak_context"], u["peak_context"])
                break
    return out
