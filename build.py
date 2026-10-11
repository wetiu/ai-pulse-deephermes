#!/usr/bin/env python3
"""AI Pulse — zero-cost, self-updating AI news site.

Fetches ~45 keyless sources (lab blogs, AI press, newsletters, Hacker News,
Hugging Face Daily Papers, arXiv, Google News), cleans + dedupes + categorizes,
optionally writes an AI executive briefing via GitHub Models (free tier),
and emits a fully static site into _site/ for GitHub Pages.

Designed to run unattended in GitHub Actions every 4 hours. $0 forever.
If any single source fails it is skipped; the build only fails if there is
no data at all (no sources fetched AND no history).
"""
from __future__ import annotations

import concurrent.futures as cf
import gzip
import hashlib
import html as html_mod
import json
import os
import re
import shutil
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.sax.saxutils as saxutils
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from pathlib import Path

try:
    import feedparser
except ImportError:  # pragma: no cover
    print("FATAL: feedparser missing — run: pip install -r requirements.txt")
    sys.exit(2)

# --------------------------------------------------------------------------
# Config
# --------------------------------------------------------------------------
ROOT = Path(__file__).resolve().parent
SITE = ROOT / "_site"
DATA = ROOT / "data"
TEMPLATE = ROOT / "templates" / "index.html"
SOURCES_FILE = ROOT / "sources.json"
HISTORY_FILE = DATA / "history.json"

TZ = timezone(timedelta(hours=7))            # Metin's timezone (UTC+7, no DST)
UTC = timezone.utc
NOW = datetime.now(UTC)

UA = "Mozilla/5.0 (compatible; AIPulse/1.0; +https://github.com)"
REPO_URL = os.environ.get("REPO_URL", "https://github.com").rstrip("/")
SITE_URL = os.environ.get("SITE_URL", REPO_URL).rstrip("/")

MAX_ARTICLES = 40000        # tracked history cap
MAX_AGE_DAYS = 730          # drop articles older than 2 years
FRESH_DAYS = 10             # days rendered server-side into index.html
FRESH_CAP = 900             # cap of server-rendered cards
FETCH_WORKERS = 12
PER_FEED_CAP = 60           # max entries taken from a single RSS feed
HTTP_TIMEOUT = 25
TRACKING_PARAMS = re.compile(
    r"^(utm_|ref$|ref_|fbclid|gclid|mc_cid|mc_eid|igshid|s=|t=|si=|$)", re.I)

CATEGORIES = {
    "models":     ("Models & Releases", "#6C5CE7"),
    "research":   ("Research",          "#00D2FF"),
    "business":   ("Business",          "#FF6B9D"),
    "tools":      ("Tools & Products",  "#4ADE80"),
    "policy":     ("Policy & Safety",   "#FFD93D"),
    "opensource": ("Open Source",       "#FB923C"),
    "industry":   ("Industry",          "#8B95A9"),
}
CAT_PRIORITY = ["models", "research", "policy", "business", "tools", "opensource", "industry"]

CAT_RULES = {
    "models": [r"\breleases?\b", r"\blaunch(es|ed|ing)?\b", r"\bunveil", r"\bannounc\w* (new|model)",
               r"\bgpt-?\d", r"\bclaude\b", r"\bgemini\b", r"\bllama\b", r"\bmistral\b", r"\bqwen\b",
               r"\bdeepseek\b", r"\bgrok\b", r"\bopen.?weights\b", r"\bfoundation model", r"\bfrontier model",
               r"\breasoning model", r"\bmultimodal\b", r"\bstate.of.the.art model", r"\bmodel (family|card|weights)\b",
               r"\bnew model\b", r"\bnext.gen\w* model"],
    "research": [r"\bpaper\b", r"\barxiv\b", r"\bresearch", r"\bstud(y|ies)\b", r"\bbenchmark", r"\bstate.of.the.art\b",
                 r"\bsota\b", r"\btraining (method|technique|run)", r"\bdataset\b", r"\bneurips\b", r"\bicml\b",
                 r"\biclr\b", r"\bscaling law", r"\bfine.tun", r"\brlhf\b", r"\brlvr\b", r"\binterpretab",
                 r"\bmechanistic", r"\bpretraining\b", r"\bbreakthrough"],
    "business": [r"\bfunding\b", r"\braises?\b", r"\braised\b", r"\bvaluation\b", r"\bacquisiti", r"\bacquires?\b",
                 r"\bipo\b", r"\brevenue\b", r"\bpartnership\b", r"\binvest", r"\bbillion\b", r"\bmillion\b",
                 r"\bstartup", r"\blayoff", r"\bhire[sd]?\b", r"\bprofit", r"\bdeal\b", r"\bmarket"],
    "tools": [r"\bapi\b", r"\bsdk\b", r"\bframework\b", r"\bplugin", r"\bextension\b", r"\bintegrat",
              r"\bcopilot\b", r"\bcodex\b", r"\bcursor\b", r"\bwindsurf\b", r"\bmcp\b", r"\bapp\b",
              r"\bavailable (now|today)\b", r"\bnow available\b", r"\btool(s)?\b", r"\bfeature",
              r"\bagent(s)? (can|that|which)\b", r"\bworkflow"],
    "policy": [r"\bregulat", r"\blaw(suit)?\b", r"\blegal\b", r"\bcourt\b", r"\bsue[sd]?\b", r"\bsenate\b",
               r"\bcongress\b", r"\beu ai act\b", r"\bban(ned|ning)?\b", r"\bcopyright\b", r"\bprivacy\b",
               r"\bgdpr\b", r"\bantitrust\b", r"\bexecutive order\b", r"\bwhite house\b", r"\bpolicy\b",
               r"\bsafety (bill|act|framework|institute)\b", r"\bgovernance\b", r"\bexport control"],
    "opensource": [r"\bopen.source\b", r"\bopen.weights\b", r"\bgithub\b", r"\bhugging face\b",
                   r"\bapache\b", r"\bmit license\b", r"\bcommunity (model|release)\b", r"\bollama\b",
                   r"\bllama.cpp\b", r"\bgguf\b", r"\bquantiz", r"\blocal(ly)? (run|run|host|model)"],
}

STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "your", "you", "are", "was",
             "how", "why", "what", "when", "where", "who", "will", "can", "its", "it's", "has",
             "have", "new", "now", "out", "but", "not", "all", "one", "two", "get", "gets", "via",
             "about", "after", "into", "than", "them", "they", "their", "would", "could", "should",
             "more", "most", "other", "some", "such", "only", "over", "also", "been", "were", "his",
             "her", "our", "uses", "using", "use", "way", "day", "days", "week", "today"}

HN_AI_RE = re.compile(
    r"\b(ai|a\.i\.|llm|llms|gpt|chatgpt|openai|anthropic|claude|gemini|deepmind|mistral|llama|"
    r"machine learning|deep learning|neural|transformer|diffusion|ai agents?|copilot|cursor|"
    r"prompt|rag\b|embedding|fine.?tun|hugging ?face|inference|robots?|gpu|nvidia|cuda|"
    r"reasoning model|foundation model|generative|text.to.\w+|image model|voice model|speech model)\b",
    re.I)

ARXIV_KEEP_RE = re.compile(
    r"\b(llm|large language|language model|agent|reasoning|benchmark|diffusion|transformer|"
    r"multimodal|retrieval|rlhf|rlvr|fine.tun|instruction|prompt|inference|efficien|distill|"
    r"vision.language|tool use|alignment|safety|world model|robot|code|math|planning|memory|"
    r"long.context|mixture.of.experts|moe\b|quantiz|speculative|chain.of.thought|test.time)\b",
    re.I)

# Google-News-only noise: horoscopes (the zodiac sign "Gemini"), cartoon captions,
# sports "AI predictions" SEO spam — none of it is AI-news.
GNEWS_NOISE_RE = re.compile(
    r"\b(horoscope|editorial cartoon|expected result of|match preview|betting (tips|odds)|"
    r"live score|lineup prediction)\b",
    re.I)

# Content gate for sources that carry the general firehose (registry flag "require": "ai").
GH_KEEP_RE = re.compile(
    r"\b(ai|a\.i\.|llm|llms|gpt|agents?|agentic|mcp|machine learning|deep learning|neural|"
    r"transformer|diffusion|embedding|inference|fine.?tun|rag\b|chatbot|generative|genai|"
    r"openai|anthropic|claude|gemini|llama|mistral|copilot|cursor|deepseek|qwen|ollama|"
    r"prompt|langchain|vector|models?)\b",
    re.I)
REQUIRE_RES = {"ai": GH_KEEP_RE}
# Populated by load_sources(): source id -> require flag (for history purging too)
REQUIRE_BY_ID: dict = {}

DIGEST_MODELS = ["openai/gpt-4o-mini", "openai/gpt-4.1-mini"]
DIGEST_ENDPOINT = "https://models.github.ai/inference/chat/completions"

# --------------------------------------------------------------------------
# Utilities
# --------------------------------------------------------------------------


def http_get(url: str, timeout: int = HTTP_TIMEOUT) -> bytes:
    req = urllib.request.Request(url, headers={
        "User-Agent": UA,
        "Accept": "application/rss+xml, application/atom+xml, application/xml, application/json, text/xml, */*",
        "Accept-Encoding": "gzip",
    })
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = resp.read()
    if data[:2] == b"\x1f\x8b":  # some servers gzip regardless of headers (e.g. deepmind.google)
        data = gzip.decompress(data)
    return data


def strip_html(s: str) -> str:
    s = html_mod.unescape(s or "")
    s = re.sub(r"<script.*?</script>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<style.*?</style>", " ", s, flags=re.S | re.I)
    s = re.sub(r"<[^>]+>", " ", s)
    s = html_mod.unescape(s)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def clean_text(s: str, limit: int = 240) -> str:
    s = strip_html(s)
    if len(s) <= limit:
        return s
    cut = s[:limit]
    if " " in cut:
        cut = cut[:cut.rfind(" ")]
    return cut.rstrip(" ,;:.-") + "…"


def norm_url(url: str) -> str:
    try:
        p = urllib.parse.urlsplit(url.strip())
        host = (p.hostname or "").lower()
        if host.startswith("www."):
            host = host[4:]
        q = [(k, v) for k, v in urllib.parse.parse_qsl(p.query, keep_blank_values=False)
             if not TRACKING_PARAMS.match(k)]
        path = p.path.rstrip("/") or "/"
        return urllib.parse.urlunsplit((p.scheme or "https", host, path,
                                        urllib.parse.urlencode(q), ""))
    except Exception:
        return url.strip()


def dt_from_iso(s: str) -> datetime:
    dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return dt.astimezone(UTC)


def clamp_dt(dt: datetime) -> datetime:
    if dt > NOW:
        return NOW - timedelta(minutes=1)
    return dt


def parse_entry_date(entry) -> datetime:
    for key in ("published_parsed", "updated_parsed"):
        v = entry.get(key)
        if v:
            try:
                return datetime(*v[:6], tzinfo=UTC)
            except Exception:
                pass
    return NOW


def entry_image(entry) -> str | None:
    try:
        for mc in (entry.get("media_content") or []):
            u = mc.get("url")
            if u and (str(mc.get("medium", "")).startswith("image")
                      or re.search(r"\.(jpg|jpeg|png|webp|gif)", u, re.I)):
                return u[:500]
        for mt in (entry.get("media_thumbnail") or []):
            u = mt.get("url")
            if u:
                return u[:500]
        for ln in (entry.get("links") or []):
            if ln.get("rel") == "enclosure" and str(ln.get("type", "")).startswith("image"):
                return str(ln.get("href", ""))[:500]
        raw = entry.get("summary") or entry.get("description") or ""
        m = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', str(raw), re.I)
        if m:
            return m.group(1)[:500]
    except Exception:
        pass
    return None


def title_key(s: str) -> frozenset:
    return frozenset(w for w in re.findall(r"[a-z0-9]{3,}", (s or "").lower())
                     if w not in STOPWORDS)


def too_similar(tok: frozenset, recent: list) -> bool:
    for r in recent:
        union = len(tok | r)
        if union and len(tok & r) / union >= 0.75:
            return True
    return False


def tidy_github_snippet(s: str) -> str:
    """GitHub Trending descriptions arrive with the repo URL and a language list
    glued on ("... . https://x.y English · 简体中文 · 日本語 · ..."). Keep the prose."""
    s = re.sub(r"\s*https?://\S+.*$", "", s or "", flags=re.S)
    s = re.sub(r"\s*English\s*·.*$", "", s, flags=re.S)
    return s.strip()


def diversify(cands: list[dict], n: int) -> list[dict]:
    """Greedy briefing pick: skip a story whose headline token-set overlaps
    heavily with one already picked (same event, several outlets)."""
    picked, keys = [], []
    for a in cands:
        tok = title_key(a["title"])
        if len(tok) >= 3:
            dup = False
            for k in keys:
                inter = len(tok & k)
                union = len(tok | k)
                if union and (inter / union >= 0.34
                              or (inter >= 4 and inter / min(len(tok), len(k)) >= 0.5)):
                    dup = True
                    break
            if dup:
                continue
        picked.append(a)
        keys.append(tok)
        if len(picked) >= n:
            break
    return picked


def make_article(title, url, ts, snippet, source_name, source_id, weight,
                 points=None, comments=None, image=None, cat=None):
    nurl = norm_url(url)
    return {
        "id": hashlib.sha1(nurl.encode()).hexdigest()[:16],
        "title": clean_text(title, 300),
        "url": url,
        "snippet": snippet or "",
        "image": image,
        "source": source_name,
        "source_id": source_id,
        "weight": weight,
        "ts": clamp_dt(ts).isoformat(),
        "cat": cat or "",
        "points": points,
        "comments": comments,
    }


def host_of(url: str) -> str:
    try:
        return (urllib.parse.urlsplit(url).hostname or "").lower().replace("www.", "")
    except Exception:
        return ""

# --------------------------------------------------------------------------
# Fetchers (one per source kind)
# --------------------------------------------------------------------------


def fetch_rss(src: dict) -> list[dict]:
    raw = http_get(src["url"])
    feed = feedparser.parse(raw)
    entries = feed.get("entries", [])
    if not entries:
        bozo = feed.get("bozo_exception")
        raise RuntimeError(f"no entries ({str(bozo)[:120]})" if bozo else "no entries")
    out = []
    for e in entries[:PER_FEED_CAP]:
        title = clean_text(e.get("title", ""), 300)
        link = str(e.get("link", "")).strip()
        if not title or not link:
            continue
        if link.startswith("//"):
            link = "https:" + link
        summary_raw = e.get("summary") or e.get("description") or ""
        snip = clean_text(summary_raw, 240)
        if len(snip) < 40:
            snip = ""
        out.append(make_article(title, link, parse_entry_date(e), snip,
                                src["name"], src["id"], src.get("weight", 1),
                                image=entry_image(e)))
    return out


def fetch_arxiv(src: dict) -> list[dict]:
    arts = fetch_rss(src)
    cap = int(src.get("cap", 25))
    kept = []
    for a in arts:
        if len(kept) >= cap:
            break
        t = re.sub(r"\s*\(arXiv:.*\)\s*$", "", a["title"]).strip()
        t = re.sub(r"^\[.*?\]\s*", "", t)
        if not ARXIV_KEEP_RE.search(t + " " + a["snippet"]):
            continue
        a["title"] = t
        a["cat"] = "research"
        kept.append(a)
    return kept


def fetch_gnews(src: dict) -> list[dict]:
    arts = fetch_rss(src)
    arts = [a for a in arts if not GNEWS_NOISE_RE.search(a["title"])]
    for a in arts:
        m = re.match(r"^(.*)\s+-\s+([^\-]{2,40})$", a["title"])
        if m:
            a["title"] = m.group(1).strip()
            a["source"] = m.group(2).strip()
        a["snippet"] = ""
    return arts


def fetch_hn(src: dict) -> list[dict]:
    data = json.loads(http_get(src["url"]).decode("utf-8", "replace"))
    out = []
    for hit in (data.get("hits") or [])[:PER_FEED_CAP]:
        title = clean_text(hit.get("title") or "", 300)
        if not title or not HN_AI_RE.search(title):
            continue
        url = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        try:
            ts = dt_from_iso(hit.get("created_at") or "")
        except Exception:
            ts = NOW
        out.append(make_article(title, url, ts, "", src["name"], src["id"],
                                src.get("weight", 1),
                                points=hit.get("points"), comments=hit.get("num_comments")))
    return out


def fetch_hfpapers(src: dict) -> list[dict]:
    data = json.loads(http_get(src["url"]).decode("utf-8", "replace"))
    out = []
    for it in (data if isinstance(data, list) else data.get("dailyPapers") or [])[:PER_FEED_CAP]:
        paper = it.get("paper") or {}
        pid = paper.get("id")
        title = clean_text(paper.get("title") or "", 300)
        if not pid or not title:
            continue
        url = f"https://huggingface.co/papers/{pid}"
        snip = clean_text(paper.get("summary") or "", 240)
        if len(snip) < 40:
            snip = ""
        ts = NOW
        for raw_ts in (paper.get("publishedAt"), it.get("publishedAt")):
            if raw_ts:
                try:
                    ts = dt_from_iso(raw_ts)
                    break
                except Exception:
                    pass
        a = make_article(title, url, ts, snip, src["name"], src["id"], src.get("weight", 1),
                         points=it.get("upvotes"), comments=it.get("numComments"))
        a["cat"] = "research"
        out.append(a)
    return out


ANTHROPIC_ITEM_RE = re.compile(r'<a[^>]+href="(/news/[a-z0-9][a-z0-9\-]*)"[^>]*>(.*?)</a>', re.S | re.I)


def fetch_anthropic(src: dict) -> list[dict]:
    """Anthropic has no RSS anymore; their /news page is server-rendered — parse it."""
    body = http_get(src["url"]).decode("utf-8", "replace")
    out, seen = [], set()
    for m in ANTHROPIC_ITEM_RE.finditer(body):
        slug, chunk = m.group(1), m.group(2)
        if slug in seen:
            continue
        seen.add(slug)
        spans = re.findall(r"<span[^>]*>([^<]{2,400})</span>", chunk)
        title = clean_text(spans[-1], 300) if spans else ""
        if len(title) < 8:
            continue
        ts = NOW
        tm = re.search(r"<time[^>]*>([^<]+)</time>", chunk)
        if tm:
            for fmt in ("%b %d, %Y", "%B %d, %Y"):
                try:
                    ts = datetime.strptime(tm.group(1).strip(), fmt).replace(hour=12, tzinfo=UTC)
                    break
                except ValueError:
                    continue
        url = "https://www.anthropic.com" + slug
        out.append(make_article(title, url, ts, "", src["name"], src["id"], src.get("weight", 1)))
        if len(out) >= PER_FEED_CAP:
            break
    if not out:
        raise RuntimeError("no items parsed from news page")
    return out


FETCHERS = {"rss": fetch_rss, "arxiv": fetch_arxiv, "gnews": fetch_gnews,
            "hn": fetch_hn, "hfpapers": fetch_hfpapers, "anthropic": fetch_anthropic}

# --------------------------------------------------------------------------
# Categorize / score
# --------------------------------------------------------------------------

CAT_RES = {k: [re.compile(p, re.I) for p in pats] for k, pats in CAT_RULES.items()}


def categorize(a: dict) -> str:
    if a.get("cat"):
        return a["cat"]
    text = (a["title"] + " " + a["snippet"]).lower()
    scores = {}
    for cat, pats in CAT_RES.items():
        s = sum(1 for p in pats if p.search(text))
        if s:
            scores[cat] = s
    if not scores:
        return "industry"
    best = max(scores.values())
    for cat in CAT_PRIORITY:
        if scores.get(cat) == best:
            return cat
    return "industry"


def hot_score(a: dict) -> float:
    s = float(a.get("weight") or 1) * 10
    pts = a.get("points")
    if pts:
        s += min(float(pts), 500) / 8.0
    try:
        age_h = (NOW - dt_from_iso(a["ts"])).total_seconds() / 3600
    except Exception:
        age_h = 999
    s += 15 if age_h < 6 else 8 if age_h < 24 else 3 if age_h < 48 else 0
    return round(s, 1)

# --------------------------------------------------------------------------
# Digest (GitHub Models — free tier; best effort)
# --------------------------------------------------------------------------

DIGEST_SYSTEM = (
    "You are the editor of \"AI Pulse\", a daily AI-news dashboard read by a senior software "
    "engineer who builds with AI. Write a tight executive briefing from the numbered stories. "
    "Be specific and factual: names, numbers, releases. No hype, no filler. "
    'Respond with ONLY minified JSON: {"intro": "one-sentence overview of the day", '
    '"stories": [{"i": <story number>, "why": "one punchy sentence: why this matters"}]}. '
    "Pick the 6-8 most important stories, most important first; if several outlets cover the same event, keep only the strongest one. Keep each 'why' under 140 chars."
)


def make_digest(top: list[dict]) -> dict | None:
    token = os.environ.get("GITHUB_TOKEN")
    if not token or os.environ.get("DIGEST") == "off" or not top:
        return None
    lines = []
    for i, a in enumerate(top, 1):
        line = f"{i}. [{a['source']}] {a['title']}"
        if a.get("snippet"):
            line += f" — {a['snippet'][:150]}"
        if a.get("points"):
            line += f" ({a['points']} pts on HN)"
        lines.append(line)
    user_msg = "Stories:\n" + "\n".join(lines)
    for model in DIGEST_MODELS:
        try:
            req = urllib.request.Request(
                DIGEST_ENDPOINT,
                data=json.dumps({
                    "model": model,
                    "temperature": 0.4,
                    "max_tokens": 800,
                    "messages": [
                        {"role": "system", "content": DIGEST_SYSTEM},
                        {"role": "user", "content": user_msg},
                    ],
                }).encode(),
                headers={"Authorization": f"Bearer {token}",
                         "Content-Type": "application/json"},
                method="POST")
            with urllib.request.urlopen(req, timeout=90) as resp:
                body = json.loads(resp.read().decode())
            content = body["choices"][0]["message"]["content"]
            start, end = content.find("{"), content.rfind("}")
            if start < 0 or end <= start:
                raise ValueError("no JSON in digest response")
            parsed = json.loads(content[start:end + 1])
            stories = []
            for s in (parsed.get("stories") or [])[:8]:
                idx = int(s.get("i") or 0)
                if not (1 <= idx <= len(top)):
                    continue
                src = top[idx - 1]
                stories.append({
                    "title": src["title"], "url": src["url"], "source": src["source"],
                    "why": clean_text(str(s.get("why") or ""), 200),
                })
            if not stories:
                raise ValueError("empty digest stories")
            return {"intro": clean_text(str(parsed.get("intro") or ""), 400),
                    "stories": stories, "model": model, "auto": False}
        except Exception as exc:
            print(f"  digest[{model}] failed: {exc}")
    return None

# --------------------------------------------------------------------------
# Rendering
# --------------------------------------------------------------------------


def e(s: str) -> str:
    return html_mod.escape(str(s or ""), quote=True)


def src_color(sid: str) -> str:
    """djb2 hash — must stay identical to hueColor() in templates/index.html."""
    h = 5381
    for ch in sid:
        h = ((h * 33) + ord(ch)) & 0xFFFFFFFF
    return f"hsl({h % 360} 65% 62%)"


def initials(name: str) -> str:
    parts = re.findall(r"[A-Za-z0-9]+", name)
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[1][0]).upper()


def rel_time(dt: datetime) -> str:
    secs = max(0, (NOW - dt).total_seconds())
    if secs < 90:
        return "just now"
    mins = secs / 60
    if mins < 60:
        return f"{int(mins)}m ago"
    hours = mins / 60
    if hours < 24:
        return f"{int(hours)}h ago"
    days = hours / 24
    if days < 30:
        return f"{int(days)}d ago"
    return dt.strftime("%b %d")


def day_label(dt: datetime) -> tuple[str, str]:
    d = dt.astimezone(TZ).date()
    today = NOW.astimezone(TZ).date()
    delta = (today - d).days
    if delta == 0:
        label = "Today"
    elif delta == 1:
        label = "Yesterday"
    else:
        label = d.strftime("%A")
    return label, d.strftime("%b %d")


def render_card(a: dict) -> str:
    cat_name, cat_col = CATEGORIES.get(a["cat"], CATEGORIES["industry"])
    dt = dt_from_iso(a["ts"])
    epoch = int(dt.timestamp())
    search_blob = e((a["title"] + " " + a.get("snippet", ""))[:300])
    pts = ""
    if a.get("points"):
        pts = f'<span class="pts" title="Hacker News / upvotes">▲ {int(a["points"])}</span>'
    img = ""
    if a.get("image") and str(a["image"]).startswith("http"):
        img = f'<img class="thumb" src="{e(a["image"])}" alt="" loading="lazy" onerror="this.remove()">'
    snip = f'<p class="snip">{e(a["snippet"])}</p>' if a.get("snippet") else ""
    return (
        f'<article class="card" data-cat="{a["cat"]}" data-src="{a["source_id"]}" '
        f'data-ts="{epoch}" data-score="{hot_score(a)}" data-id="{a["id"]}" data-text="{search_blob}">'
        f'<i class="c-accent" style="background:{cat_col}"></i>'
        f'<div class="c-main">'
        f'<div class="c-top">'
        f'<span class="src"><b class="mono" style="color:{src_color(a["source_id"])}">{e(initials(a["source"]))}</b>{e(a["source"])}</span>'
        f'<span class="c-dot">·</span><time data-ts="{epoch}" datetime="{a["ts"]}">{rel_time(dt)}</time>'
        f'{pts}<span class="newpill" hidden>NEW</span>'
        f'</div>'
        f'<h3><a href="{e(a["url"])}" target="_blank" rel="noopener">{e(a["title"])}</a></h3>'
        f'{snip}'
        f'<div class="c-meta"><span class="catpill" style="color:{cat_col};border-color:{cat_col}33">{cat_name}</span></div>'
        f'</div>{img}'
        f'</article>')


def render_feed(fresh: list[dict]) -> tuple[str, int]:
    groups: dict = {}
    for a in fresh:
        d = dt_from_iso(a["ts"]).astimezone(TZ).date()
        groups.setdefault(d, []).append(a)
    parts = []
    for d in sorted(groups.keys(), reverse=True):
        items = sorted(groups[d], key=lambda x: x["ts"], reverse=True)
        label, pretty = day_label(datetime(d.year, d.month, d.day, 12, tzinfo=TZ))
        parts.append(
            f'<section class="day" data-daykey="{d.isoformat()}"><h2 class="dayhead"><span>{label}</span>'
            f'<time>{pretty}</time><em>{len(items)} stories</em></h2>')
        parts.append("".join(render_card(a) for a in items))
        parts.append("</section>")
    return "".join(parts), len(fresh)

# --------------------------------------------------------------------------
# Main build
# --------------------------------------------------------------------------


def load_sources() -> list[dict]:
    srcs = json.loads(SOURCES_FILE.read_text(encoding="utf-8"))["sources"]
    REQUIRE_BY_ID.clear()
    REQUIRE_BY_ID.update({s["id"]: s["require"] for s in srcs if s.get("require")})
    return srcs


def fetch_all(sources: list[dict]) -> tuple[list[dict], list[dict]]:
    results, stats = [], []
    with cf.ThreadPoolExecutor(max_workers=FETCH_WORKERS) as ex:
        started: dict = {}
        futs = {}
        for s in sources:
            fut = ex.submit(FETCHERS[s["kind"]], s)
            started[fut] = time.time()
            futs[fut] = s
        for fut in cf.as_completed(futs):
            src = futs[fut]
            ms = int((time.time() - started[fut]) * 1000)
            try:
                arts = fut.result()
                req = REQUIRE_RES.get(src.get("require") or "")
                if req:
                    arts = [a for a in arts if req.search(a["title"] + " " + a["snippet"])]
                stats.append({"id": src["id"], "name": src["name"], "ok": True,
                              "count": len(arts), "ms": ms, "error": None})
                results.extend(arts)
            except Exception as exc:
                stats.append({"id": src["id"], "name": src["name"], "ok": False,
                              "count": 0, "ms": ms, "error": str(exc)[:160]})
    stats.sort(key=lambda s: next(i for i, x in enumerate(sources) if x["id"] == s["id"]))
    return results, stats


def item_allowed(a: dict) -> bool:
    """Noise gate re-applied to merged history, so stale junk gets purged —
    not just kept out at fetch time (feed rules can tighten between builds)."""
    sid = a.get("source_id") or ""
    if sid.startswith("gnews") and GNEWS_NOISE_RE.search(a["title"]):
        return False
    req = REQUIRE_BY_ID.get(sid)
    if req and not REQUIRE_RES[req].search(a["title"] + " " + (a.get("snippet") or "")):
        return False
    return True


def merge_history(fetched: list[dict]) -> tuple[list[dict], int]:
    DATA.mkdir(exist_ok=True)
    if HISTORY_FILE.exists():
        hist = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
    else:
        hist = {"updated": None, "articles": []}
    existing = {a["id"]: a for a in hist["articles"]}
    # order fetched: prefer older-ts first so ties keep the more specific source? keep as-is
    recent_keys = []
    for a in hist["articles"][:3500]:
        recent_keys.append(title_key(a["title"]))
    accepted_keys = []
    new_count = 0
    for a in fetched:
        old = existing.get(a["id"])
        if old is not None:
            if a.get("points") and (not old.get("points") or a["points"] > old["points"]):
                old["points"] = a["points"]
                old["comments"] = a.get("comments") or old.get("comments")
            continue
        tok = title_key(a["title"])
        if len(tok) >= 3 and (too_similar(tok, recent_keys) or too_similar(tok, accepted_keys)):
            continue
        a["cat"] = categorize(a)
        existing[a["id"]] = a
        accepted_keys.append(tok)
        new_count += 1
    for a in existing.values():
        if a.get("source_id") == "ghtrend":
            a["snippet"] = tidy_github_snippet(a.get("snippet") or "")
    articles = [a for a in existing.values() if item_allowed(a)]
    articles.sort(key=lambda x: x["ts"], reverse=True)
    # prune
    cutoff = (NOW - timedelta(days=MAX_AGE_DAYS)).isoformat()
    articles = [a for a in articles if a["ts"] >= cutoff][:MAX_ARTICLES]
    hist["articles"] = articles
    hist["updated"] = NOW.isoformat()
    tmp = HISTORY_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(hist, ensure_ascii=False, separators=(",", ":")))
    os.replace(tmp, HISTORY_FILE)
    return articles, new_count


def render_rail(articles: list[dict], stats: list[dict], fresh: list[dict],
                exclude: set | None = None) -> dict:
    week_ago = (NOW - timedelta(days=7)).isoformat()
    recent = [a for a in articles if a["ts"] >= week_ago]
    # trending — the 48h heat list *beyond* what the briefing already shows
    exclude = exclude or set()
    last48 = [a for a in articles
              if (NOW - dt_from_iso(a["ts"])).total_seconds() < 48 * 3600]
    top = sorted((a for a in last48 if a["url"] not in exclude),
                 key=hot_score, reverse=True)[:6]
    trending = "".join(
        f'<li><span class="rank">{i}</span><a href="{e(a["url"])}" target="_blank" rel="noopener">{e(a["title"])}</a>'
        f'<span class="tsrc">{e(a["source"])}</span></li>'
        for i, a in enumerate(top, 1))
    # category bars
    counts = {k: 0 for k in CATEGORIES}
    for a in recent:
        counts[a["cat"]] = counts.get(a["cat"], 0) + 1
    mx = max(counts.values()) if counts else 1
    catbars = "".join(
        f'<li><span class="cb-name">{CATEGORIES[k][0]}</span>'
        f'<span class="cb-track"><i style="width:{max(4, round(counts[k] * 100 / mx))}%;background:{CATEGORIES[k][1]}"></i></span>'
        f'<b>{counts[k]}</b></li>'
        for k in CAT_PRIORITY)
    # source health
    ok = sum(1 for s in stats if s["ok"])
    rows = "".join(
        f'<li class="srow">'
        f'<span class="dot {"ok" if s["ok"] else "err"}" title="{e(s["error"] or "healthy")}"></span>'
        f'<span class="sname">{e(s["name"])}</span><b>{s["count"]}</b></li>'
        for s in sorted(stats, key=lambda s: (not s["ok"], -s["count"])))
    # pill + source counts cover exactly the server-rendered feed window ("in view")
    pill_counts = {k: 0 for k in CAT_PRIORITY}
    for a in fresh:
        pill_counts[a["cat"]] = pill_counts.get(a["cat"], 0) + 1
    pills = f'<button class="pill active" data-cat="all">All <b>{len(fresh)}</b></button>' + "".join(
        f'<button class="pill" data-cat="{k}" style="--pc:{CATEGORIES[k][1]}">{CATEGORIES[k][0]} <b>{pill_counts[k]}</b></button>'
        for k in CAT_PRIORITY if pill_counts[k])
    src_opts = "".join(
        f'<label><input type="checkbox" value="{s["id"]}"> <span>{e(s["name"])}</span> <b>{sum(1 for a in fresh if a["source_id"] == s["id"])}</b></label>'
        for s in sorted(stats, key=lambda s: s["name"]))
    return {"trending": trending, "catbars": catbars, "sources": rows, "pills": pills,
            "src_opts": src_opts, "ok": ok, "total": len(stats)}


def render_digest(articles: list[dict]) -> tuple[str, str, set]:
    window = [a for a in articles
              if (NOW - dt_from_iso(a["ts"])).total_seconds() < 24 * 3600]
    if len(window) < 12:
        window = [a for a in articles
                  if (NOW - dt_from_iso(a["ts"])).total_seconds() < 48 * 3600]
    top = sorted(window, key=hot_score, reverse=True)[:50]
    d = make_digest(top)
    if d:
        used = {s["url"] for s in d["stories"]}
        badge = f'<span class="digest-badge">✦ AI-edited · {e(d["model"])}</span>'
        intro = f'<p class="digest-intro">{e(d["intro"])}</p>'
        items = "".join(
            f'<li><a href="{e(s["url"])}" target="_blank" rel="noopener">{e(s["title"])}</a>'
            f'<span class="why">{e(s["why"])}</span></li>'
            for s in d["stories"])
    else:
        # keep obvious non-AI posts (dev-blog noise) out of the auto briefing when possible
        ai_pool = [a for a in top
                   if GH_KEEP_RE.search(a["title"] + " " + (a.get("snippet") or ""))]
        pool = diversify(ai_pool if len(ai_pool) >= 6 else top, 6)
        used = {a["url"] for a in pool}
        badge = '<span class="digest-badge auto">◆ auto-ranked</span>'
        intro = '<p class="digest-intro">Top stories right now, ranked by source quality, community signal and freshness.</p>'
        items = "".join(
            f'<li><a href="{e(a["url"])}" target="_blank" rel="noopener">{e(a["title"])}</a>'
            f'<span class="why">{e(a["source"])}</span></li>'
            for a in pool)
    return f'{intro}<ol class="digest-list">{items}</ol>', badge, used


def build():
    t0 = time.time()
    sources = load_sources()
    print(f"AI Pulse build — {NOW.astimezone(TZ).strftime('%Y-%m-%d %H:%M %Z')} "
          f"| {len(sources)} sources")
    fetched, stats = fetch_all(sources)
    articles, new_count = merge_history(fetched)

    fresh = [a for a in articles
             if (NOW - dt_from_iso(a["ts"])).days < FRESH_DAYS][:FRESH_CAP]
    fresh_html, fresh_n = render_feed(fresh)
    digest_html, digest_badge, digest_urls = render_digest(articles)
    rail = render_rail(articles, stats, fresh, digest_urls)

    today = [a for a in articles
             if dt_from_iso(a["ts"]).astimezone(TZ).date() == NOW.astimezone(TZ).date()]

    if SITE.exists():
        shutil.rmtree(SITE)
    SITE.mkdir(parents=True)
    template = TEMPLATE.read_text(encoding="utf-8")

    date_line = NOW.astimezone(TZ).strftime("%A, %B %d · %H:%M")
    pulse_js = {
        "generated": int(NOW.timestamp()),
        "total": len(articles),
        "today": len(today),
        "sourcesOk": rail["ok"],
        "sourcesTotal": rail["total"],
        "repo": REPO_URL,
        "site": SITE_URL,
    }
    repl = {
        "%%GENERATED_ISO%%": NOW.isoformat(),
        "%%DATE_LINE%%": date_line,
        "%%TOTAL_TODAY%%": f"{len(today):,}",
        "%%TOTAL_ALL%%": f"{len(articles):,}",
        "%%SOURCES_OK%%": str(rail["ok"]),
        "%%SOURCES_TOTAL%%": str(rail["total"]),
        "%%DIGEST_HTML%%": digest_html,
        "%%DIGEST_BADGE%%": digest_badge,
        "%%FEED_HTML%%": fresh_html,
        "%%TRENDING_HTML%%": rail["trending"],
        "%%CATBARS_HTML%%": rail["catbars"],
        "%%SOURCES_HTML%%": rail["sources"],
        "%%PILLS_HTML%%": rail["pills"],
        "%%SRC_OPTS_HTML%%": rail["src_opts"],
        "%%FRESH_COUNT%%": f"{fresh_n:,}",
        "%%REPO_URL%%": REPO_URL,
        "%%PULSE_JSON%%": json.dumps(pulse_js, separators=(",", ":")),
    }
    page = template
    for k, v in repl.items():
        page = page.replace(k, v)
    leftover = re.findall(r"%%[A-Z_]+%%", page)
    if leftover:
        print(f"  WARNING: unreplaced template tokens: {leftover}")
    (SITE / "index.html").write_text(page, encoding="utf-8")

    # articles.json (full history, for "load older" + search-all)
    slim = [{k: a.get(k) for k in
             ("id", "title", "url", "snippet", "image", "source", "source_id",
              "ts", "cat", "points", "comments")} | {"score": hot_score(a)}
            for a in articles]
    (SITE / "articles.json").write_text(
        json.dumps({"generated": NOW.isoformat(), "articles": slim},
                   ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # stats.json
    (SITE / "stats.json").write_text(json.dumps(
        {"generated": NOW.isoformat(), "sources": stats,
         "totals": {"fetched": len(fetched), "new": new_count, "tracked": len(articles)}},
        ensure_ascii=False, separators=(",", ":")), encoding="utf-8")

    # feed.xml (site's own RSS)
    rss_items = []
    for a in articles[:60]:
        try:
            pub = format_datetime(dt_from_iso(a["ts"]))
        except Exception:
            pub = format_datetime(NOW)
        rss_items.append(
            "<item>"
            f"<title>{saxutils.escape(a['title'])}</title>"
            f"<link>{saxutils.escape(a['url'])}</link>"
            f"<guid isPermaLink=\"false\">{a['id']}</guid>"
            f"<pubDate>{pub}</pubDate>"
            f"<description>{saxutils.escape(a.get('snippet', '') or a['source'])}</description>"
            "</item>")
    feed = ('<?xml version="1.0" encoding="UTF-8"?>\n'
            '<rss version="2.0"><channel>'
            "<title>AI Pulse</title>"
            f"<link>{saxutils.escape(SITE_URL)}</link>"
            "<description>Every AI signal that matters — auto-curated every 4 hours.</description>"
            f"<lastBuildDate>{format_datetime(NOW)}</lastBuildDate>"
            + "".join(rss_items) + "</channel></rss>")
    (SITE / "feed.xml").write_text(feed, encoding="utf-8")
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\n")

    # summary
    print("\nSOURCE HEALTH")
    for s in sorted(stats, key=lambda x: (not x["ok"], -x["count"])):
        flag = "ok " if s["ok"] else "FAIL"
        err = f"  {s['error']}" if s["error"] else ""
        print(f"  {flag} {s['id']:<16} {s['count']:>4} items {s['ms']:>5}ms{err}")
    took = time.time() - t0
    ok_n = sum(1 for s in stats if s["ok"])
    size = (SITE / "index.html").stat().st_size
    print(f"\nTOTALS {ok_n}/{len(stats)} sources ok · fetched {len(fetched)} · "
          f"new {new_count} · tracked {len(articles)} · today {len(today)} · "
          f"index {size // 1024}KB · {took:.1f}s")
    if ok_n == 0 and not articles:
        print("FATAL: no data at all")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(build())
