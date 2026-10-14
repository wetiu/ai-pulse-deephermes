#!/usr/bin/env python3
"""AI Pulse — zero-cost, self-updating AI-dev news site (ai/game/web/app).

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
    "ai":   ("AI & ML",      "#6C5CE7"),
    "game": ("Game Dev",     "#00D2FF"),
    "web":  ("Web Dev",      "#FF6B9D"),
    "app":  ("App Dev",      "#4ADE80"),
}
CAT_PRIORITY = ["ai", "game", "web", "app"]

# -------- Vertical gate: each must match one of these on title+snippet --------
GAME_RE = re.compile(
    r"\b(gam(e|ing|es|er)|gameplay|video\s*game|videogame|indie\s*game|"
    r"steam(\s*deck)?|unity(\s*engine)?|unreal(\s*engine)?|godot|"
    r"nintendo|playstation|xbox|minecraft|fortnite|epic\s*games|"
    r"twitch|esports|game\s*dev(elo?pment|eloper)?|gamedev|"
    r"game\s*studio|game\s*engine|level\s*design|"
    r"shader|ray\s*tracing|dlss|fsr|gpu\s*benchmark|"
    r"fps|rpg|mmorpg|moba|roguelike|roguelite|"
    r"battle\s*pass|season\s*pass|dlc|early\s*access|alpha|beta|"
    r"kickstarter|backerkit|crowdfund(ing)?|"
    r"switch(\s*2)?|ps5|xbox\s*series|playstation\s*5|"
    r"GDC|gamescom|E3|"
    r"godot|unreal|unity|ue5|ue4|"
    r"baldur'?s\s*gate|stardew|terraria|hades|hollow\s*knight|"
    r"playtest|gameplay|dungeon|raid|loot|boss|"
    r"nintendo\s*direct|ps\s*showcase|state\s*of\s*play|xbox\s*showcase|"
    r"valve|steamworks|steam\s*next\s*fest|"
    r"ray\s*tracing|path\s*tracing|framerate|"
    r"modder|modding|game\s*mod|game\s*hack|cheat\s*engine|"
    r"speedrun|"
    r"artstation|itch\.io|game\s*jolt|"
    r"game\s*award|the\s*game\s*awards|"
    r"console|controller|gaming\s*pc|handheld|steam\s*deck|"
    r"switch\s*2)\b", re.I)

APP_RE = re.compile(
    r"\b(app\s*store|play\s*store|"
    r"\b(ios|ipados|macos|android|windows|linux)\b|"
    r"iphone|ipad|ipod|"
    r"pixel\s*\d|galaxy|surface\s*(pro|laptop|go)|"
    r"macbook|airpods|apple\s*watch|"
    r"xcode|swift|swiftui|objective-?c|kotlin|jetpack|compose|flutter|dart|"
    r"react\s*native|ionic|cordova|xamarin|"
    r"testflight|app\s*store\s*connect|google\s*play\s*console|"
    r"ipa|apk|aab|app\s*bundle|"
    r"app\s*launch|app\s*release|app\s*update|app\s*beta|app\s*review|app\s*rating|"
    r"app\s*developer|app\s*development|app\s*dev|"
    r"app\s*downloads|monthly\s*active\s*users|mau|"
    r"in-?app\s*purchase|subscription|freemium|premium\s*app|"
    r"ASO|app\s*store\s*optimization|app\s*localization|"
    r"vision\s*pro|visionos|watchos|tvos|"
    r"indie\s*app|indie\s*developer|indie\s*hacker|"
    r"mobile\s*dev|mobile\s*app|tablet|smartphone|"
    r"google\s*play|apple\s*store|microsoft\s*store|"
    r"vision\s*os|foldable|"
    r"public\s*beta|developer\s*preview|gm\s*\(|gold\s*master|"
    r"release\s*candidate|stable\s*release|"
    r"app\s*maintenance|app\s*deprecation|app\s*removal|"
    r"app\s*store\s*award|app\s*store\s*editor|"
    r"wear\s*os|fitbit|garmin|oura|"
    r"e-?ink|e-reader|kindle|kobo|"
    r"roku|fire\s*tv|chromecast|apple\s*tv|android\s*tv|"
    r"smart\s*speaker|alexa|siri|google\s*assistant|"
    r"app\s*icon|app\s*screenshot|app\s*preview|"
    r"localization|translation\s*app|"
    r"e-?commerce|marketplace\s*app|on-?demand|"
    r"super\s*app|chat\s*app|messaging\s*app|video\s*call|"
    r"saas\s*app|mobile\s*saas|"
    r"app\s*analytics|app\s*attribution|app\s*tracking|"
    r"app\s*shortcut|app\s*clip|"
    r"apple\s*vision|meta\s*quest|"
    r"iOS\s*\d|iPadOS\s*\d|macOS\s*\d|android\s*\d|visionOS\s*\d|watchOS\s*\d|"
    r"app\s*store\s*connect|testflight|app\s*store\s*server|"
    r"app\s*intents|app\s*shortcuts|siri\s*kit|app\s*clips|"
    r"core\s*haptics|metal\s*api|vulkan\s*api|"
    r"uikit|appkit|swiftui|combine|swiftdata|swift\s*concurrency|"
    r"push\s*notification|deep\s*link|universal\s*link|app\s*link|"
    r"in-app\s*event|server-?to-?server\s*notification|"
    r"apple\s*sign\s*in|google\s*sign\s*in|passkey\s*in\s*app|"
    r"on-?device\s*ml\s*in\s*app|core\s*ml|tflite|ml\s*kit|"
    r"app\s*widget|home\s*screen|live\s*activity|"
    r"app\s*update\s*required|app\s*compatibility|"
    r"ios\s*app\s*store|mac\s*app\s*store|"
    r"app\s*pre-?order|app\s*subscription\s*pricing|"
    r"app\s*store\s*featured|app\s*store\s*charts|"
    r"app\s*analytics|crashlytics|firebase\s*crashlytics|"
    r"app\s*size|app\s*binary|app\s*thinning|"
    r"play\s*store\s*featured|play\s*store\s*charts|"
    r"app\s*signing|app\s*notarization|app\s*sandbox)\b", re.I)

ART_RE = re.compile(
    r"\b("
    r"illustration|illustrator|illustrated|"
    r"digital\s*art|concept\s*art|character\s*design|environment\s*design|"
    r"2d\s*art|3d\s*art|2d\s*animation|3d\s*animation|"
    r"3d\s*model|3d\s*modeling|3d\s*modelling|"
    r"cgi|vfx|visual\s*effect|"
    r"animation\s*(studio|director|lead)?|animator|animated|"
    r"motion\s*capture|mocap|motion\s*graphics|"
    r"storyboard|storyboarding|"
    r"graphic\s*novel|manga|anime|webtoon|manhwa|"
    r"artstation|behance|dribbble|deviant\s*art|"
    r"ray\s*tracing|path\s*tracing|pathtracer|"
    r"octane\s*render|redshift|arnold|cycles|eevee|"
    r"blender(\s+(news|foundation|conference|survey|release|annual|siggraph|annecy|in\s+))?|"
    r"cinema\s*4d|\bc4d\b|autodesk\s*maya|\bmaya\b|"
    r"zbrush|substance\s*(painter|designer)?|"
    r"houdini|\bnuke\b|after\s*effects|"
    r"procreate|\bkrita\b|"
    r"speedpaint|time-?lapse|art\s*process|art\s*tutorial|"
    r"color\s*theory|matte\s*painting|prop\s*design|creature\s*design|"
    r"sculpting|sculptor|\bsculpt\b|rigging|retopology|uv\s*map|texturing|"
    r"pixel\s*art|voxel|low-?poly|high-?poly|"
    r"graphic\s*design|graphic\s*designer|"
    r"typography|font\s*design|logo\s*design|branding|"
    r"ux\s*design|interaction\s*design|"
    r"fashion\s*design|interior\s*design|architectural\s*design|"
    r"art\s*director|art\s*team|art\s*direction|"
    r"designboom|dezeen|creative\s*bloq|"
    r"art\s*gallery|art\s*exhibition|art\s*show|"
    r"art\s*book|art\s*print|art\s*commission|"
    r"sketchfab|polyhaven|textures\.com|"
    r"artstation\s*challenge|concept\s*art\s*challenge|"
    r"render\s*week|render\s*competition|"
    r"artstation\s*mag|artstation\s*featured|"
    r"adobe\s*creative|adobe\s*max|adobe\s*firefly|adobe\s*substance|"
    r"figma\s*art|figma\s*design|figma\s*community|"
    r"artstation\s*trending|artstation\s*frontpage|"
    r"character\s*art(ist)?|character\s*sheet|"
    r"creature\s*art(ist)?|"
    r"environment\s*art(ist)?|"
    r"hard-?surface|hard\s*surface\s*modeling|"
    r"organic\s*modeling|"
    r"prop\s*art(ist)?|"
    r"vfx\s*artist|vfx\s*breakdown|vfx\s*reel|"
    r"art\s*reel|art\s*showcase|art\s*portfolio|"
    r"art\s*school|art\s*college|art\s*university|art\s*degree|"
    r"art\s*funding|art\s*grant|art\s*residency|"
    r"art\s*kickstarter|art\s*crowdfund|"
    r"art\s*news|art\s*world|art\s*market|art\s*auction|"
    r"art\s*collector|art\s*collection|"
    r"illustration\s*award|art\s*award|art\s*prize|"
    r"artstation\s*live|artstation\s*podcast|artstation\s*interview|"
    r"artstation\s*tutorial|artstation\s*course|artstation\s*class|"
    r"artstation\s*magazine|"
    r"creative\s*review|creative\s*boom|"
    r"art\s*direction\s*award|adc\s*award|"
    r"d&ad|clio\s*award|one\s*show|"
    r"red\s*dot|if\s*design|ida\s*design|"
    r"design\s*week|milan\s*design|"
    r"art\s*basel|frieze|"
    r"adobe\s*stock|shutterstock|getty\s*images|"
    r"envato|creative\s*market|"
    r"cgtrader|turbo\s*squid|3docean|"
    r"texture\s*artist|texture\s*art|"
    r"lighting\s*artist|lighting\s*art|"
    r"matte\s*painter|matte\s*artist|"
    r"compositing|composit(ing|er)|nuke\s*compositing|"
    r"look\s*dev|lookdev|look\s*development|"
    r"asset\s*creation|asset\s*artist|"
    r"character\s*modeler|environment\s*modeler|"
    r"prop\s*modeler|hard\s*surface\s*modeler|"
    r"rigging\s*artist|technical\s*artist|"
    r"shader\s*artist|effects\s*artist|"
    r"storyboard\s*artist|story\s*artist|"
    r"previs|pre-visualization|"
    r"animatic|"
    r"shotgun|flow\s*production\s*tracking|ftrack)\b", re.I)

WEB_RE = re.compile(
    r"\b("
    r"web\s*dev(elo?pment|eloper)?|"
    r"web\s*developer|web\s*app(lication)?\b|"
    r"front-?end|back-?end|fullstack|full-?stack|"
    r"web\s*framework|"
    r"javascript|typescript|js\s+frame|ts\s+compiler|"
    r"react(\.?js)?|vue(\.?js)?|angular|svelte|solidjs|qwik|astro|"
    r"next(\.?js|\s*js)?|nuxt|remix|gatsby|"
    r"html5|css3|scss|sass|less|postcss|"
    r"tailwind(\s*css)?|bootstrap|chakra|mui|"
    r"webpack|vite|parcel|esbuild|turbopack|rollup|swc|babel|"
    r"npm|yarn|pnpm|bun|deno|"
    r"node(\.?js)?|express(\.?js)?|fastify|koa|nestjs|"
    r"graphql|trpc|"
    r"prisma|drizzle|supabase|planetscale|neon|"
    r"vercel\s+(blog|changelog|platform)|netlify\s+(blog|changelog)|"
    r"jamstack|ssr|ssg|isr|spa|hydration|"
    r"lighthouse|core\s*web\s*vitals|web\s*vitals|page\s*speed|"
    r"web\s*accessibility|a11y|wai-aria|wcag|"
    r"csp|cors|xss|csrf|samesite|"
    r"webassembly|wasm|webgpu|webgl|webaudio|"
    r"service\s*worker|web\s*worker|progressive\s*web\s*app|pwa\b|"
    r"container\s*queries|view\s*transitions|"
    r"web\s*components|custom\s*elements|shadow\s*dom|"
    r"responsive\s*design|mobile-?first|adaptive\s*design|"
    r"variable\s*font|woff2|"
    r"chrome\s*devtools|firefox\s*dev|safari\s*tech|"
    r"whatwg|w3c|csswg|"
    r"polyfill|tsc\s*config|"
    r"sveltekit|nextjs|nuxtjs|"
    r"eslint|prettier|biome|"
    r"vitest|jest|playwright|cypress|testing\s*library|"
    r"web\s*performance|bundle\s*size|tree-?shaking|"
    r"web\s*app\s*manifest|web\s*push|notifications\s*api|"
    r"clipboard\s*api|share\s*api|web\s*share|"
    r"intersection\s*observer|mutation\s*observer|"
    r"localstorage|sessionstorage|indexed\s*db|"
    r"cdn|edge\s*computing|edge\s*function|"
    r"serverless|lambda|cloud\s*function|"
    r"web\s*standard|web\s*platform|"
    r"turborepo|monorepo|"
    r"package\.json|tsconfig|jsconfig|"
    r"caniuse|mdn\s*blog|mdn\s*reference|"
    r"hsts|preload|prefetch|preconnect|"
    r"t3-?app|t3\s*stack|"
    r"astro\s*islands|qwik\s*city|"
    r"htmx|hotwire|turbo\s*frames|"
    r"unocss|windicss|vanilla-?extract|stitches|"
    r"styled-?components|emotion\s*css|"
    r"radix\s*ui|shadcn(\s*ui)?|"
    r"framer-?motion|gsap|lottie\s*web|"
    r"three\.js|babylon\.js|react\s*three\s*fiber|"
    r"d3(\.?js)?|chart\.js|plotly|observable\s*plot|"
    r"tanstack\s*query|swr|react\s*query|"
    r"zod|yup|valibot|"
    r"svelte\s*5|runes\s*svelte|"
    r"hono|elysia|solidstart|"
    r"qwik\s*resumable|remix\s*v2|"
    r"css\s*subgrid|@layer|css\s*cascade|"
    r":has\(\)|:where\(\)|:is\(\)|"
    r"popover|toplayer|dialog\s*element|"
    r"anchor\s*positioning|"
    r"view\s*transitions\s*api|"
    r"webtransport|webcodecs|"
    r"web\s*locks|web\s*streams|"
    r"web\s*transport|web\s*codecs|"
    r"web\s*nfc|web\s*usb|web\s*serial|web\s*bluetooth|"
    r"origin\s*private\s*file\s*system|"
    r"web\s*nn|web\s*machine\s*learning|"
    r"webauthn|passkey|webauthn\s*api|"
    r"web\s*crypto|crypto\s*api|"
    r"fetch\s*api|xhr|"
    r"oauth|jwt|webauthn|"
    r"webdriver|"
    r"playwright\s*api|"
    r"cypress|"
    r"webdriverio|"
    r"axe-core|"
    r"core\s*web\s*vitals\s*\d|"
    r"lighthouse\s*ci|"
    r"web\s*core\s*vitals|"
    r"first\s*contentful\s*paint|"
    r"largest\s*contentful\s*paint|"
    r"cumulative\s*layout\s*shift|"
    r"first\s*input\s*delay|"
    r"interaction\s*to\s*next\s*paint|"
    r"the\s*web\s*platform|"
    r"web\s*platform\s*status|"
    r"web\s*platform\s*incubator|"
    r"openjs|"
    r"open\s*web|"
    r"interoperability|"
    r"web\s*manifest|"
    r"web\s*app\s*spec|"
    r"web\s*spec|"
    r"web-?platform-?tests|"
    r"interop-?\d|"
    r"lighthouse\s*audit|"
    r"lighthouse\s*score|"
    r"web-?vitals-?js|"
    r"css\s*anchor\s*positioning|"
    r"scroll\s*driven\s*animations|"
    r"css\s*timeline|"
    r":view-transition|"
    r"css\s*highlight\s*api|"
    r"openjs\s*world|"
    r"openjs\s*conference|"
    r"jsconf|"
    r"css\s*conf|"
    r"smashing\s*conf|"
    r"a11y\s*project|"
    r"smashing\s*magazine|"
    r"css-?tricks|"
    r"web\s*design\s*ledger|"
    r"freecodecamp|"
    r"frontendmentor|"
    r"the\s*odin\s*project|"
    r"javascript\.info|"
    r"typescriptlang\.org|"
    r"dev\.to|"
    r"web\s*tools\s*weekly|"
    r"web\s*design\s*weekly|"
    r"javascript\s*weekly|"
    r"frontend\s*focus|"
    r"css\s*weekly|"
    r"a11y\s*weekly|"
    r"web\s*performance\s*weekly|"
    r"react\s*hooks|react\s*state|react\s*context|"
    r"react\s*router|react\s*form|"
    r"react\s*native|"
    r"vue\s*3|vue\s*2|"
    r"vue\s*cli|vuex|pinia|"
    r"nuxt\s*3|nuxt\s*2|"
    r"angular\s*\d|"
    r"angular\s*cli|"
    r"rxjs|ngrx|mobx|redux|zustand|"
    r"jotai|recoil|valtio|xstate|"
    r"manifest\.json|webmanifest|"
    r"web\s*push\s*api|push\s*api|"
    r"broadcast\s*channel|shared\s*worker|"
    r"css\s*modules|css\s*scope|"
    r"@container|@layer|@property|@scope|"
    r"@supports|@keyframes|@import|"
    r"@view-transition|@scroll-timeline|"
    r"@position-try|@anchor|"
    r"startViewTransition|"
    r"interesttarget|"
    r"position-anchor|position-try|"
    r"anchor-name|anchor-size|"
    r"css-grid|css-flexbox|"
    r"css-multicol|css-cascade|"
    r"css-values|css-syntax|"
    r"css-conditional|"
    r"css-pseudo|css-overflow|"
    r"web-animations|"
    r"compositor|"
    r"build\s*tool|bundler|"
    r"webpack-dev-server|"
    r"importmap|"
    r"json\s*module\b|"
    r"constructable\s*stylesheet|"
    r"adopted\s*stylesheet|"
    r"module\s*worker|"
    r"form-associated|"
    r"custom\s*built-in|"
    r"declarative\s*shadow|"
    r"declarative\s*custom|"
    r"speculation\s*rules|"
    r"prerender\s*api|"
    r"view\s*transitions|"
    r"view-transition|"
    r"view\s*transition-)"
    r"\b", re.I)

# (DEV_RE / DEV_GATE / REQUIRE_RES are defined later, after AI_RE is set)
# (Legacy HN_AI_RE / ARXIV_KEEP_RE aliases also defined later, after AI_RE.)
GNEWS_NOISE_RE = re.compile(
    r"\b(horoscope|editorial\s*cartoon|match\s*preview|"
    r"live\s*score|betting\s*tips?|lineup\s*prediction|"
    r"sports\s*betting|gambling\s*odds|fantasy\s*football|"
    r"daily\s*horoscope|zodiac|astrology|"
    r"celebrity\s*gossip|kardashian|"
    r"fashion\s*week\s*celebrity|red\s*carpet\s*gossip)\b", re.I)

STOPWORDS = {"the", "and", "for", "with", "that", "this", "from", "your", "you", "are", "was",
             "how", "why", "what", "when", "where", "who", "will", "can", "its", "it's", "has",
             "have", "new", "now", "out", "but", "not", "all", "one", "two", "get", "gets", "via",
             "about", "after", "into", "than", "them", "they", "their", "would", "could", "should",
             "more", "most", "other", "some", "such", "only", "over", "also", "been", "were", "his",
             "her", "our", "uses", "using", "use", "way", "day", "days", "week", "today"}

# Legacy aliases — defined later, after AI_RE is set.
# (HN_AI_RE for HN algolia filter, ARXIV_KEEP_RE for arxiv filter; both alias to AI_RE.)

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
# AI vertical regex — same pattern as the original AI Pulse gate.
# Hits: AI/ML/LLM/vendors/techniques. Used for the `ai` vertical and as the
# default "is this AI-news?" check on broad sources like GitHub Trending.
AI_RE = GH_KEEP_RE

# Legacy aliases — point to the new AI_RE gate.
# HN_AI_RE: per-HN-source filter (article must mention AI terms)
# ARXIV_KEEP_RE: per-arxiv-source filter (article must be AI/ML-relevant)
HN_AI_RE = AI_RE
ARXIV_KEEP_RE = AI_RE

# Combined gate — post must match at least one vertical: ai | game | web | app
DEV_RE = re.compile("|".join([AI_RE.pattern, GAME_RE.pattern, WEB_RE.pattern, APP_RE.pattern]), re.I)

# Content gate (replaces the old AI-only gate) — alias used by the per-source `require: dev_gate` check
DEV_GATE = DEV_RE
REQUIRE_RES = {"dev_gate": DEV_GATE}
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

CAT_RES = {"ai": AI_RE, "game": GAME_RE, "web": WEB_RE, "app": APP_RE}


def categorize(a: dict) -> str:
    """Assign one of 4 verticals.

    Prefer a source-pinned `cat` from sources.json when present (the fetcher
    passes it through). Fall back to title+snippet scoring: the vertical
    whose regex hits the most wins; ties broken by CAT_PRIORITY order.
    Post must hit at least one vertical (the strict gate in fetch_all / merge
    already filters out non-matches before we get here).
    """
    pinned = a.get("cat")
    if pinned in CATEGORIES:
        return pinned
    text = a["title"] + " " + a.get("snippet", "")
    scores = {cat: len(p.findall(text)) for cat, p in CAT_RES.items()}
    scores = {k: v for k, v in scores.items() if v > 0}
    if not scores:
        return "ai"  # soft default (one of 4 must match by gate)
    best = max(scores.values())
    for cat in CAT_PRIORITY:
        if scores.get(cat, 0) == best:
            return cat
    return "ai"


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
    "You are the editor of \"AI Pulse\", a daily AI-dev news dashboard for software "
    "engineers who build with AI. Coverage spans AI/ML news, game dev, web dev, "
    "and app dev — anything an AI developer needs to stay current. Write a tight "
    "executive briefing from the numbered stories. Be specific and factual: tool "
    "names, version numbers, model names, framework versions. No hype, no filler. "
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
    cat_name, cat_col = CATEGORIES.get(a["cat"], CATEGORIES["ai"])
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
    """Noise + dev-vertical gate re-applied to merged history, so stale junk
    (including articles from sources no longer in sources.json) gets purged.
    Strict gate: every article in the history must match one of the 4 verticals.
    """
    sid = a.get("source_id") or ""
    if sid.startswith("gnews") and GNEWS_NOISE_RE.search(a["title"]):
        return False
    # Strict dev-vertical gate: title+snippet must hit AI_RE|GAME_RE|WEB_RE|APP_RE
    if not DEV_RE.search(a["title"] + " " + (a.get("snippet") or "")):
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
    # Re-categorize every surviving article against the current 4-vertical regex.
    # (Old history may carry stale category names like "industry"/"models".)
    for a in existing.values():
        a["cat"] = categorize(a)
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

    # extra head tags for PWA + iOS + OG
    head_extras = (
        '<link rel="manifest" href="manifest.webmanifest">'
        '<link rel="apple-touch-icon" href="assets/apple-touch-icon.png">'
        '<link rel="mask-icon" href="assets/mask-icon.svg" color="#6C5CE7">'
        '<meta name="apple-mobile-web-app-title" content="AI Pulse">'
        '<meta name="apple-mobile-web-app-capable" content="yes">'
        '<meta name="apple-mobile-web-app-status-bar-style" content="black-translucent">'
        '<meta name="mobile-web-app-capable" content="yes">'
        '<meta name="theme-color" content="#050508">'
        '<meta property="og:image" content="assets/og-image.png">'
        '<meta name="twitter:card" content="summary_large_image">'
        '<meta name="twitter:image" content="assets/og-image.png">'
        '<meta name="twitter:title" content="AI Pulse — every AI-dev signal, one page">'
        '<meta name="twitter:description" content="AI, game, web, and app dev news for AI devs — auto-curated every 4 hours. $0 forever.">'
    )
    page = page.replace("<link rel=\"alternate\"", head_extras + "<link rel=\"alternate\"", 1)
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
            "<description>AI, game, web, app dev news — auto-curated every 4 hours.</description>"
            f"<lastBuildDate>{format_datetime(NOW)}</lastBuildDate>"
            + "".join(rss_items) + "</channel></rss>")
    (SITE / "feed.xml").write_text(feed, encoding="utf-8")
    (SITE / "robots.txt").write_text("User-agent: *\nAllow: /\n")

    # ---------- PWA artifacts ----------
    (SITE / "manifest.webmanifest").write_text(json.dumps({
        "name": "AI Pulse — every AI-dev signal, one page",
        "short_name": "AI Pulse",
        "description": "AI, game, web, and app dev news for AI devs — auto-curated every 4 hours. $0 forever.",
        "start_url": "./",
        "scope": "./",
        "display": "standalone",
        "background_color": "#050508",
        "theme_color": "#050508",
        "orientation": "portrait-primary",
        "icons": [
            {"src": "assets/apple-touch-icon.png", "sizes": "180x180", "type": "image/png", "purpose": "any maskable"},
            {"src": "favicon-32.png", "sizes": "32x32", "type": "image/png"},
            {"src": "favicon-192.png", "sizes": "192x192", "type": "image/png", "purpose": "any maskable"},
        ],
    }, separators=(",", ":")), encoding="utf-8")

    # service worker (cache-shell, network-first for articles.json, swr for feed.xml)
    (SITE / "sw.js").write_text((
        "const CACHE = 'ai-pulse-v3';\n"
        "const PRECACHE = ['./', 'index.html', 'articles.json', 'feed.xml', 'manifest.webmanifest', "
        "'assets/apple-touch-icon.png', 'assets/og-image.png', 'favicon-32.png', 'favicon-192.png'];\n"
        "self.addEventListener('install', e => { self.skipWaiting(); "
        "e.waitUntil(caches.open(CACHE).then(c => c.addAll(PRECACHE).catch(() => {}))); });\n"
        "self.addEventListener('activate', e => { e.waitUntil("
        "caches.keys().then(keys => Promise.all(keys.filter(k => k !== CACHE).map(k => caches.delete(k))))); "
        "self.clients.claim(); });\n"
        "self.addEventListener('fetch', e => {\n"
        "  const u = new URL(e.request.url);\n"
        "  if (u.origin !== location.origin) return;\n"
        "  if (e.request.method !== 'GET') return;\n"
        "  if (u.pathname.endsWith('/articles.json')) {\n"
        "    e.respondWith(fetch(e.request).then(r => { const c = r.clone(); "
        "caches.open(CACHE).then(x => x.put(e.request, c)); return r; }).catch(() => caches.match(e.request)));\n"
        "    return;\n"
        "  }\n"
        "  if (u.pathname.endsWith('/feed.xml')) {\n"
        "    e.respondWith(caches.open(CACHE).then(c => c.match(e.request)).then(cached => "
        "Promise.race([fetch(e.request).then(r => { c && c.put(e.request, r.clone()); return r; }), "
        "new Promise(res => setTimeout(() => res(cached || new Response('', {status:504})), 1500))]).catch(() => cached)));\n"
        "    return;\n"
        "  }\n"
        "  if (e.request.mode === 'navigate') {\n"
        "    e.respondWith(fetch(e.request).then(r => { const c = r.clone(); "
        "caches.open(CACHE).then(x => x.put('./index.html', c)); return r; }).catch(() => caches.match('./index.html')));\n"
        "    return;\n"
        "  }\n"
        "  e.respondWith(caches.match(e.request).then(c => c || fetch(e.request).then(r => "
        "{ if (r.status === 200) { const cc = r.clone(); caches.open(CACHE).then(x => x.put(e.request, cc)); } return r; })));\n"
        "});\n"
    ), encoding="utf-8")

    # register the SW in index.html
    idx = (SITE / "index.html").read_text(encoding="utf-8")
    if "navigator.serviceWorker" not in idx:
        idx = idx.replace("</body>",
            "<script>if('serviceWorker' in navigator){navigator.serviceWorker.register('sw.js');}</script></body>", 1)
        (SITE / "index.html").write_text(idx, encoding="utf-8")

    # ---------- icons (Pillow generates them; skipped if Pillow missing) ----------
    try:
        from PIL import Image, ImageDraw, ImageFont  # type: ignore
        assets = SITE / "assets"
        assets.mkdir(exist_ok=True)

        def _font(sz):
            for p in ("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
                      "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
                      "/usr/share/fonts/TTF/DejaVuSans-Bold.ttf"):
                if Path(p).exists():
                    try: return ImageFont.truetype(p, sz)
                    except Exception: pass
            return ImageFont.load_default()

        # 32 / 192 (PWA + favicon)
        for size, name in ((32, "favicon-32.png"), (192, "favicon-192.png"),
                            (180, "apple-touch-icon.png"), (1200, "og-image.png")):
            img = Image.new("RGB", (size, size), (5, 5, 8))
            d = ImageDraw.Draw(img)
            if size >= 192:
                # gradient ring
                for r in range(2, 6):
                    col = (108 + r * 4, 92 + r * 4, 231 - r * 5) if r % 2 == 0 else (0, 210, 255)
                    d.ellipse((r, r, size - r, size - r), outline=col, width=1)
            # neon line + dot
            scale = size / 32
            d.line([(int(5 * scale), int(21 * scale)), (int(12 * scale), int(13 * scale)),
                    (int(18 * scale), int(19 * scale)), (int(27 * scale), int(9 * scale))],
                   fill=(0, 210, 255), width=max(2, int(3 * scale)))
            d.ellipse((int(24 * scale), int(6 * scale), int(30 * scale), int(12 * scale)),
                      fill=(255, 107, 157))
            # mask-icon: simpler mono
            if name != "mask-icon.svg":
                img.save(assets / name if "apple" in name or "og" in name else SITE / name)

        # OG card: 1200x630 cyberpunk with gradient + title
        og = Image.new("RGB", (1200, 630), (5, 5, 8))
        od = ImageDraw.Draw(og)
        # background mesh
        for i in range(6):
            cx = 200 + i * 160
            cy = 100 + (i % 2) * 200
            for r in range(80, 0, -8):
                a = max(0, 24 - r // 4)
                col = (108, 92, 231) if i % 3 == 0 else (0, 210, 255) if i % 3 == 1 else (255, 107, 157)
                od.ellipse((cx - r, cy - r, cx + r, cy + r), outline=col)
        # title
        f1 = _font(98); f2 = _font(40)
        od.text((72, 200), "AI Pulse", fill=(240, 240, 250), font=f1)
        od.text((72, 320), "AI. Game. Web. App. Every dev signal. One page.", fill=(0, 210, 255), font=f2)
        od.text((72, 400), f"%%SOURCES_TOTAL%% sources · 4 verticals · $0 forever", fill=(154, 154, 180), font=f2)
        od.text((72, 540), "wetiu.github.io/ai-pulse-deephermes", fill=(108, 92, 231), font=f2)
        og.save(assets / "og-image.png")

        # mask-icon.svg (monochrome, theme-aware)
        (SITE / "assets").mkdir(exist_ok=True)
        (SITE / "assets" / "mask-icon.svg").write_text(
            '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
            '<path d="M5 21 L12 13 L18 19 L27 9 M24 9 L27 9 L27 12" '
            'fill="none" stroke="#000" stroke-width="3" stroke-linecap="round" stroke-linejoin="round"/>'
            '<circle cx="27" cy="9" r="3" fill="#000"/>'
            '</svg>', encoding="utf-8")
        print("  icons: generated (Pillow)")
    except Exception as ex:
        print(f"  icons: skipped ({ex})")

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
