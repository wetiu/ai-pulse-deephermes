# AI Pulse

> **Every AI signal. One page. Zero noise.**

A self-updating AI news dashboard that runs entirely in GitHub's cloud — 4× a day, forever, at **$0/month**. No servers you maintain, no accounts, no API bills, no machine required: your PC can be off for months and the site keeps updating.

**Live:** `https://<owner>.github.io/ai-pulse/`

---

## What you get

- **47 keyless sources** — OpenAI, Anthropic, Google DeepMind, Google Research, Meta, Microsoft, Mistral, HuggingFace, NVIDIA, arXiv, HN, Lobsters, TLDR AI, Last Week in AI, The Verge, TechCrunch, Ars Technica, Import AI, Interconnects, Simon Willison, Latent Space, Google News… all free, no keys, no signup.
- **Today's Briefing** — an executive summary auto-edited by a free LLM (GitHub Models tier), ranked by source quality + community signal + freshness. Gracefully degrades to algorithmic ranking if the LLM tier is unavailable.
- **Search / archive** — full-text search across ~1,200+ tracked stories with an on-demand archive search over the whole history.
- **Filters** — 7 categories (Models, Research, Policy, Business, Tools, Open Source, Industry) + per-source toggles, all client-side.
- **Trending rail** — the 48-hour heat list, deduplicated against the briefing.
- **RSS feed** — `feed.xml` for your reader of choice.
- **Self-healing** — per-source failure tolerance (one dead feed can never kill the build), history merge keeps stories across builds, noise filters purge junk automatically.
- **Cyberpunk-neon glassmorphic UI** — search, sort, load-older paging, zero dependencies, pure static HTML.

## How it works

```
        GitHub Actions runner (ubuntu-latest, cloud)
   ┌────────────────────────────────────────────────────┐
   │  cron 17 */4 * *    +    push to main    +    manual │
   │                     │                                │
   │              python build.py                         │
   │   47 sources ──► fetch (threads) ──► clean/dedupe    │
   │        │              │                   │          │
   │        │         per-source stats    history merge   │
   │        ▼              ▼                   ▼          │
   │   Feed + digest + rail + articles.json + feed.xml    │
   └───────────────────────────┬────────────────────────┘
                               ▼
                    GitHub Pages (static, free CDN)
                               ▼
                     https://<owner>.github.io/ai-pulse/
```

- **Schedule:** `17 */4 * * *` — 6 runs/day, offset :17 to dodge the top-of-hour cron stampede.
- **Hosting:** GitHub Pages, deployed from Actions artifacts. Free, global CDN, HTTPS.
- **Build time:** ~4 s (fetch) + deploy, well under the 20-minute timeout.
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
  {"id": "openai", "name": "OpenAI", "kind": "rss", "url": "https://openai.com/news/rss.xml", "weight": 3}
]}
```

| kind | behavior |
|---|---|
| `rss` | any RSS/Atom feed (feedparser) |
| `arxiv` | arXiv export-API category feed (recent AI papers) |
| `hn` | Hacker News Algolia API (`points` boost) |
| `gnews` | Google News RSS search query |
| `hfpapers` | HuggingFace Daily Papers API |
| `anthropic` | dedicated parser (Anthropic has no RSS) |

`weight` (0–3) feeds ranking. Add `"require": "ai"` to a source to keep only AI-matching items (used for the general GitHub Trending firehose). Noise filters and the AI gate live in `build.py` (regexes at the top).

After editing, run the probe to confirm health: `.venv/bin/python tools/check_sources.py --all`.

## Files

```
build.py                     engine: fetch → clean → rank → render (std lib + feedparser)
sources.json                 47-source registry
templates/index.html         the entire UI (design system + client JS, zero dependencies)
tools/check_sources.py       feed health probe (STATUS entries bozo ms url)
.github/workflows/update.yml 4-hourly build + Pages deploy
deploy.sh                    one-shot GitHub setup
data/                        persistent store (history.json — merged across builds, auto-committed)
_site/                       build output (index.html, articles.json, feed.xml, stats.json)
```

## License

MIT — see [LICENSE](LICENSE).
