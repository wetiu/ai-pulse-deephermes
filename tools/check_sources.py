#!/usr/bin/env python3
"""Probe candidate feeds before adding them to sources.json.

Usage:  .venv/bin/python tools/check_sources.py [url ...]
        .venv/bin/python tools/check_sources.py --all     # probe everything in sources.json

Prints: STATUS  entries=N  bozo=...  url
Handy for maintaining sources.json when a publisher moves its feed.
"""
from __future__ import annotations

import json
import sys
import time
import urllib.request
from pathlib import Path

import feedparser

ROOT = Path(__file__).resolve().parent.parent
UA = {"User-Agent": "Mozilla/5.0 (compatible; AIPulse/1.0)"}


def probe(url: str) -> str:
    t0 = time.time()
    try:
        req = urllib.request.Request(url, headers=UA)
        raw = urllib.request.urlopen(req, timeout=25).read()
        f = feedparser.parse(raw)
        n = len(f.get("entries", []))
        ms = int((time.time() - t0) * 1000)
        status = "OK  " if n else "EMPTY"
        bozo = f"bozo={type(f.get('bozo_exception')).__name__}" if f.get("bozo") else "bozo=no"
        return f"{status} entries={n:<4} {bozo:<24} {ms:>5}ms  {url}"
    except Exception as exc:
        return f"FAIL {type(exc).__name__}: {str(exc)[:90]}  {url}"


def main() -> None:
    args = sys.argv[1:]
    if args == ["--all"]:
        srcs = json.loads((ROOT / "sources.json").read_text(encoding="utf-8"))["sources"]
        urls = [s["url"] for s in srcs if s["kind"] in ("rss", "arxiv", "gnews")]
    else:
        urls = args
    if not urls:
        print(__doc__)
        return
    for u in urls:
        print(probe(u))
        time.sleep(0.7)


if __name__ == "__main__":
    main()
