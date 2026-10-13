# Dev Pulse

> **Game. App. Art. Web. One dev feed. Zero noise.**

A self-updating dev-news dashboard that runs entirely in GitHub's cloud — 4× a day, forever, at **$0/month**. No servers you maintain, no accounts, no API bills, no machine required: your PC can be off for months and the site keeps updating.

Strictly **game development, app development, art development, and website development** news. Every post is filtered through a per-vertical regex gate — anything off-topic is dropped at the source.

**Live:** `https://wetiu.github.io/ai-pulse-deephermes/`

---

## What you get

- **35 keyless sources** across 4 verticals:
  - **Game** (7) — Game Developer, Unity, Godot, PC Gamer, IGN, Rock Paper Shotgun, r/gamedev (HN)
  - **App** (9) — The Verge Tech, TechCrunch Apps, MacRumors, 9to5Mac, Ars Technica, Android Dev, Apple Dev, App Store Top Free, Product Hunt
  - **Art** (9) — Blender, Creative Bloq, ArtStation, Designboom, Dezeen, The Verge (art), r/Art (HN), DeviantArt, Concept Art Empire
  - **Web** (10) — CSS-Tricks, Smashing Magazine, MDN, web.dev, r/webdev (HN), TechCrunch, GitHub Engineering, Vercel, CSS Weekly, Frontend Focus
- **Strict title gate** — every post must match one of the 4 verticals' regex; off-topic posts never make it in
- **Today's Briefing** — an executive summary auto-edited by a free LLM (GitHub Models tier), ranked by source quality + community signal + freshness. Gracefully degrades to algorithmic ranking if the LLM tier is unavailable.
- **Search / archive** — full-text search across tracked stories with on-demand archive search over the whole history.
- **Filters** — 4 categories (Game, App, Art, Web) + per-source toggles, all client-side.
- **Trending rail** — the 48-hour heat list, deduplicated against the briefing.
- **RSS feed** — `feed.xml` for your reader of choice.
- **PWA** — installable to phone home-screen, offline cache, custom 1200×630 OG image.
- **Premium micro-interactions** — Cmd/Ctrl-K command palette, j/k keyboard nav, image lightbox, scroll progress, back-to-top, source health summary, topbar freshness dot.
- **Self-healing** — per-source failure tolerance (one dead feed can never kill the build), history merge keeps stories across builds.
- **Cyberpunk-neon glassmorphic UI** — zero dependencies, pure static HTML.

## How it works

```
        GitHub Actions runner (ubuntu-latest, cloud)
   ┌────────────────────────────────────────────────────┐
   │  cron 17 */4 * *    +    push to main    +    manual │
   │                     │                                │
   │              python build.py                         │
   │   35 sources ──► fetch (threads) ──► strict gate    │
   │        │              │                   │          │
   │        │         per-source stats    history merge   │
   │        ▼              ▼                   ▼          │
   │   Feed + digest + rail + articles.json + feed.xml    │
   │   + manifest + sw.js + icons + OG image              │
   └───────────────────────────┬────────────────────────┘
                               ▼
                    GitHub Pages (static, free CDN)
                               ▼
                  https://wetiu.github.io/ai-pulse-deephermes/
```

- **Schedule:** `17 */4 * * *` — 6 runs/day, offset :17 to dodge the top-of-hour cron stampede.
- **Hosting:** GitHub Pages, deployed from Actions artifacts. Free, global CDN, HTTPS.
- **Build time:** ~5 s (fetch) + deploy, well under the 20-minute timeout.
- **State:** `data/history.json` (repo root) is committed back by the workflow each run, so stories persist across builds.

## Why $0 forever

| Component | Free tier used |
|---|---|
| Compute | **Public repo → unlimited GitHub Actions minutes** |
| Hosting | GitHub Pages (free for public repos) |
| CDN + HTTPS | GitHub Pages edge |
| Sources | Keyless RSS/Atom/APIs — no subscriptions, no API keys |
| AI briefing | GitHub Models free tier (`models: read` token permission) |
| Storage | Repo + Pages ≤ 1 GB (actual: ~1 MB) |

There is no billing surface: no credit card is attached to any component.

## Local preview

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python build.py                # builds into ./_site
python3 -m http.server 10400 -d _site    # http://localhost:10400
```

Useful flags:

```bash
.venv/bin/python build.py                # full build (fetch + render)
DIGEST=off .venv/bin/python build.py     # skip the LLM briefing (pure algorithmic)
.venv/bin/python tools/check_sources.py --all   # probe every feed, print status + latency
```

## Deploy (one-time, ~30 seconds)

```bash
gh auth login        # once — GitHub CLI, choose HTTPS + browser
./deploy.sh          # creates the public repo, pushes, enables Pages, first run
```

`deploy.sh` will print the live URL and the command to watch the first workflow run. From then on, **nothing to do, ever** — the cron in `.github/workflows/update.yml` rebuilds and redeploys every 4 hours.

## Customize

`sources.json` — the whole registry:

```json
{"sources": [
  {"id": "unity_blog", "name": "Unity Blog", "kind": "rss",
   "url": "https://blog.unity.com/feed", "weight": 3, "cat": "game"}
]}
```

| kind | behavior |
|---|---|
| `rss` | any RSS/Atom feed (feedparser) |
| `hn` | Hacker News Algolia API (`points` boost) |

`weight` (0–3) feeds ranking. `cat` pins the article to one of the 4 verticals; without it, the strict regex gate categorizes on title+snippet. Sources are dropped if neither the source's vertical nor the article's title matches any of the 4 verticals.

After editing, run the probe to confirm health: `.venv/bin/python tools/check_sources.py --all`.

## Files

```
build.py                     engine: fetch → strict-gate → clean → rank → render
sources.json                 35-source registry, 4 verticals
templates/index.html         the entire UI (design system + client JS + PWA, zero deps)
tools/check_sources.py       feed health probe (STATUS entries bozo ms url)
.github/workflows/update.yml 4-hourly build + Pages deploy
deploy.sh                    one-shot GitHub setup
data/                        persistent store (history.json — merged across builds, auto-committed)
_site/                       build output (index.html, articles.json, feed.xml, stats.json,
                             manifest.webmanifest, sw.js, icons)
```

## License

MIT — see [LICENSE](LICENSE).
