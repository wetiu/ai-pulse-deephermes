#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""AI Pulse -> dev-news candidates (pre-agent script for the `ai-pulse-dev-digest` cron job).

Reads Metin's AI Pulse aggregator (rebuilt in the cloud every 4h), keeps the last
~26h of items, scores them against his four focus areas (game dev, web dev,
art & design, app dev), and prints a compact candidate list for the cron agent
to curate into the Telegram digest.

Silent by design: prints exactly {"wakeAgent": false} as its last line when there
is nothing worth sending, so the cron job skips the agent run entirely and nothing
is delivered. Never exits non-zero -- failures are logged and gate silent;
repeated fetch failures wake the agent once on day 3, then every 7 days.

Usage: python3 ai-pulse-dev-digest.py [--dry]     (--dry does not touch state)
"""
import json
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE_FILE = HERE / ".state-ai-pulse-digest.json"
LOG_FILE = HERE / "logs" / "ai-pulse-digest.log"
URL = "https://wetiu.github.io/ai-pulse-deephermes/articles.json"

WINDOW_H = 26          # look back this many hours
TOP_N = 12             # max candidates shown to the agent
MIN_CANDIDATES = 3     # wake only if at least this many candidates...
STANDOUT_SCORE = 6.0   # ...or a single standout item
DRY = "--dry" in sys.argv
_printed = False

# Source weights mirrored from the project's sources.json (keyless registry, 47 sources).
SRC_W = {
    "openai": 3, "anthropic": 3, "deepmind": 3, "googleresearch": 2.5, "googleai": 2.5,
    "metaeng": 2.5, "metanews": 2, "microsoft": 2, "msresearch": 2.5, "hf": 3,
    "nvidia": 2, "apple": 2.5, "aws": 2, "mistral": 3,
    "arxiv_ai": 2, "arxiv_cl": 2, "arxiv_lg": 2, "hfpapers": 3.5, "alignment": 2,
    "techcrunch": 2.5, "verge": 2.5, "arstechnica": 2, "wired": 2, "mittr": 2.5,
    "thedecoder": 2.5, "ieeespectrum": 2, "register": 2, "techxplore": 1.5,
    "marktechpost": 1.5, "kdnuggets": 1.5, "infoq": 1.5, "bbc": 1.5,
    "simonw": 3, "importai": 2.5, "interconnects": 2.5, "latentspace": 2.5,
    "oneusefulthing": 2, "aheadofai": 2, "lastweekai": 2.5, "smol": 2.5, "tldrai": 2,
    "gnews_ai": 1.5, "gnews_llm": 1.5,
    "hn_front": 3, "hn_llm": 2.5, "lobsters": 2, "ghtrend": 2,
}


def rx(alt: str):
    return re.compile(r"(?<![\w-])(?:" + alt + r")(?![\w-])", re.I)


TOPICS = {
    "game": rx(r"games?|gaming|gamedev|game[- ]dev(?:elopment)?|game[- ]engine|gameplay|"
               r"in[- ]game|unity|unreal|godot|roblox|minecraft|steam|xbox|playstation|"
               r"nintendo|esports|npcs?"),
    "web": rx(r"web|websites?|web[- ]?dev(?:elopment)?|frontend|front[- ]end|back[- ]?end|"
              r"full[- ]?stack|react|next\.js|nextjs|vue|svelte|angular|css3?|html5?|"
              r"javascript|typescript|node\.js|nodejs|deno|bun|browsers?|chrome|firefox|"
              r"safari|webassembly|wasm|tailwind|django|rails|laravel|wordpress|vercel|"
              r"netlify|cloudflare|webgl|webgpu|web apps?|shopify"),
    "art": rx(r"(?<!state of the )(?<!state-of-the-)arts?|artists?|artworks?|"
              r"design|designers?|illustrations?|image gen(?:eration)?|text[- ]to[- ]image|"
              r"diffusion|midjourney|stable diffusion|dall[- ]?e|comfyui|"
              r"blender|animation|text[- ]to[- ]video|video gen(?:eration)?|sora|"
              r"runway|pika|concept art|creatives?|creativity|aesthetic|photoshop|figma|"
              r"canva|3d (?:model|art|render|asset)s?"),
    "app": rx(r"apps?|mobile|ios|iphone|ipad|ipados|android|flutter|react native|swiftui|"
              r"kotlin|xcode|app store|play store|saas|pwa|tauri|electron|desktop apps?"),
}


def log(msg: str) -> None:
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with LOG_FILE.open("a", encoding="utf-8") as fh:
            fh.write(msg + "\n")
        lines = LOG_FILE.read_text(encoding="utf-8", errors="replace").splitlines()
        if len(lines) > 400:
            LOG_FILE.write_text("\n".join(lines[-400:]) + "\n", encoding="utf-8")
    except OSError:
        pass


def stamped() -> str:
    return datetime.now().astimezone().strftime("%Y-%m-%d %H:%M %z")


def load_state() -> dict:
    try:
        st = json.loads(STATE_FILE.read_text(encoding="utf-8"))
        if isinstance(st, dict):
            st.setdefault("sent_ids", [])
            st.setdefault("fail_streak", 0)
            return st
    except (OSError, ValueError):
        pass
    return {"sent_ids": [], "fail_streak": 0}


def save_state(state: dict) -> None:
    if DRY:
        return
    try:
        tmp = STATE_FILE.with_suffix(".tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        tmp.replace(STATE_FILE)
    except OSError:
        pass


def fetch(url: str):
    req = urllib.request.Request(url, headers={"User-Agent": "ai-pulse-digest/1.0 (+DeepHermes cron)"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read())


def parse_ts(value):
    if not value:
        return None
    try:
        dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def tkey(title: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", (title or "").lower())[:80]


def score_item(item: dict):
    text = f"{item.get('title', '')} {item.get('snippet', '')}".lower()
    classes = [name for name, r in TOPICS.items() if r.search(text)]
    hits = sum(len(r.findall(text)) for r in TOPICS.values())
    src = str(item.get("source_id") or "")
    s = 2.5 * len(classes) + 0.4 * max(0, min(hits - len(classes), 3)) + 0.3 * SRC_W.get(src, 2.0)
    pts = item.get("points")
    if isinstance(pts, (int, float)):
        if pts >= 200:
            s += 1.0
        elif pts >= 100:
            s += 0.5
    return s, classes


def print_line(text: str) -> None:
    global _printed
    _printed = True
    print(text)


def gate_silent() -> None:
    print(json.dumps({"wakeAgent": False}))


def main() -> None:
    state = load_state()
    now = datetime.now(timezone.utc)

    try:
        data = fetch(URL)
        items = data if isinstance(data, list) else (data.get("articles") or [])
        items = [it for it in items if isinstance(it, dict)]
    except Exception as exc:  # any failure must stay silent (logged instead)
        streak = int(state.get("fail_streak", 0)) + 1
        state["fail_streak"] = streak
        save_state(state)
        log(f"{stamped()} | FETCH-FAIL x{streak} | {type(exc).__name__}: {exc}")
        if streak == 3 or (streak > 3 and (streak - 3) % 7 == 0):
            print_line(
                f"PROBLEM: the AI Pulse news source has been unreachable for {streak} consecutive days "
                f"({type(exc).__name__}). Tell Metin in 1-2 friendly lines that the news check is failing "
                f"and will keep retrying."
            )
        else:
            gate_silent()
        return

    state["fail_streak"] = 0
    sent = set(map(str, state.get("sent_ids", [])))
    scored = []
    seen = set()
    fresh = 0
    for it in items:
        ts = parse_ts(it.get("ts"))
        if ts is None or (now - ts) > timedelta(hours=WINDOW_H):
            continue
        fresh += 1
        iid = str(it.get("id") or it.get("url") or "")
        if not iid or iid in sent:
            continue
        k = tkey(it.get("title"))
        if k and k in seen:
            continue
        s, classes = score_item(it)
        if not classes:
            continue
        scored.append({"it": it, "ts": ts, "score": s, "classes": classes, "id": iid})
        if k:
            seen.add(k)

    scored.sort(key=lambda c: (c["score"], c["ts"]), reverse=True)
    cands = scored[:TOP_N]
    wake = len(cands) >= MIN_CANDIDATES or (bool(cands) and cands[0]["score"] >= STANDOUT_SCORE)

    if not wake:
        save_state(state)
        log(f"{stamped()} | ok | tracked={len(items)} fresh={fresh} matched={len(scored)} cands={len(cands)} wake=no")
        gate_silent()
        return

    print_line(f"AI PULSE · dev-topic candidates · last {WINDOW_H}h · generated {stamped()}")
    print_line(f"tracked {len(items)} · in-window {fresh} · matched {len(scored)} · showing {len(cands)}")
    for i, c in enumerate(cands, 1):
        it = c["it"]
        age_h = (now - c["ts"]).total_seconds() / 3600.0
        title = (it.get("title") or "").strip().replace("\n", " ")[:160]
        pts = it.get("points")
        pts_s = f" · {int(pts)} pts" if isinstance(pts, (int, float)) else ""
        print_line(
            f"[{i}] {c['score']:.1f} · {'+'.join(c['classes'])} · \"{title}\" · "
            f"{it.get('source') or it.get('source_id') or '?'} · {age_h:.0f}h ago{pts_s}"
        )
        print_line(f"    {it.get('url', '')}")
        sn = re.sub(r"<[^>]+>", "", (it.get("snippet") or "")).strip().replace("\n", " ")
        if sn:
            print_line(f"    {sn[:180]}")

    state["sent_ids"] = (list(state.get("sent_ids", [])) + [c["id"] for c in cands])[-400:]
    state["last_wake"] = stamped()
    save_state(state)
    log(
        f"{stamped()} | ok | tracked={len(items)} fresh={fresh} matched={len(scored)} "
        f"cands={len(cands)} wake=yes top={cands[0]['score']:.1f} \"{(cands[0]['it'].get('title') or '')[:60]}\""
    )


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # absolute last resort: never crash the cron run
        log(f"{stamped()} | CRASH | {type(exc).__name__}: {exc}")
        if not _printed:
            gate_silent()
