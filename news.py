"""
Daily news fetcher for tracked stocks.
Sources:
  1. Yahoo Finance RSS  — ticker-specific headlines
  2. Google News RSS    — aggregates BBC, Reuters, CNBC, Bloomberg, FT, WSJ, etc.
Then DeepSeek synthesises everything into a bullish/bearish summary.
"""
import requests
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from urllib.parse import quote_plus
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

HEADERS = {"User-Agent": "Mozilla/5.0 (compatible; AlphaConsensus/1.0; +https://localhost)"}

# Company names for better Google News search queries
TICKER_COMPANY = {
    "AAPL":  "Apple",        "MSFT": "Microsoft",    "NVDA": "NVIDIA",
    "TSLA":  "Tesla",        "META":  "Meta",         "AMZN": "Amazon",
    "GOOGL": "Google",       "GOOG":  "Alphabet",     "BRK":  "Berkshire Hathaway",
    "TSMC":  "TSMC",         "TSM":   "TSMC",         "SMCI": "Super Micro Computer",
    "AMD":   "AMD",          "INTC":  "Intel",        "BIDU": "Baidu",
    "BABA":  "Alibaba",      "COIN":  "Coinbase",     "ARKK": "ARK Invest",
    "AI":    "C3.ai",        "SPX":   "S&P 500",      "MA":   "Mastercard",
    "CTAS":  "Cintas",       "ARR":   "ARMOUR REIT",
}

_client = None
def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


# ── RSS helpers ────────────────────────────────────────────────────────────────

def _parse_rss(content: bytes) -> list[dict]:
    """Parse RSS XML bytes → list of {title, url, source, published}."""
    try:
        root = ET.fromstring(content)
    except ET.ParseError:
        return []
    items = []
    for item in root.findall(".//item"):
        title = item.findtext("title", "").strip()
        link  = item.findtext("link",  "").strip()
        pub   = item.findtext("pubDate", "").strip()
        # Google News puts source in <source> element; Yahoo doesn't
        src_el = item.find("source")
        source = src_el.text.strip() if src_el is not None and src_el.text else ""
        desc = item.findtext("description", "").strip()
        if title:
            items.append({"title": title, "url": link, "source": source,
                          "published": pub, "desc": desc})
    return items


def fetch_yahoo_news(ticker: str, max_items: int = 6) -> list[dict]:
    """Yahoo Finance RSS — ticker-specific, highly relevant."""
    url = (f"https://feeds.finance.yahoo.com/rss/2.0/headline"
           f"?s={quote_plus(ticker)}&region=US&lang=en-US")
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        items = _parse_rss(r.content)[:max_items]
        for i in items:
            i.setdefault("source", "Yahoo Finance")
        return items
    except Exception as e:
        print(f"[news] Yahoo RSS error for {ticker}: {e}")
        return []


def fetch_google_news(ticker: str, max_items: int = 8) -> list[dict]:
    """
    Google News RSS — aggregates BBC, Reuters, CNBC, Bloomberg, WSJ, FT, etc.
    Searches by company name for higher-quality matches.
    """
    company = TICKER_COMPANY.get(ticker.upper(), ticker)
    query = quote_plus(f"{company} stock market")
    url = f"https://news.google.com/rss/search?q={query}&hl=en-US&gl=US&ceid=US:en"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        return _parse_rss(r.content)[:max_items]
    except Exception as e:
        print(f"[news] Google News RSS error for {ticker}: {e}")
        return []


def fetch_all_sources(ticker: str) -> list[dict]:
    """
    Merge Yahoo Finance + Google News, deduplicate near-identical titles.
    Returns combined list tagged with source.
    """
    yahoo  = fetch_yahoo_news(ticker, max_items=5)
    google = fetch_google_news(ticker, max_items=8)

    seen, combined = set(), []
    for item in yahoo + google:
        key = item["title"].lower()[:60]
        if key not in seen:
            seen.add(key)
            combined.append(item)
    return combined[:12]


# ── DeepSeek summarization ────────────────────────────────────────────────────

def summarize_news(ticker: str, headlines: list[dict]) -> dict:
    """
    Send headlines (with source names) to DeepSeek for bullish/bearish analysis.
    """
    if not DEEPSEEK_API_KEY or not headlines:
        return {}

    lines = []
    for h in headlines:
        src  = f" [{h['source']}]" if h.get("source") else ""
        desc = f" — {h['desc'][:100]}" if h.get("desc") else ""
        lines.append(f"- {h['title']}{src}{desc}")
    headlines_text = "\n".join(lines)

    prompt = f"""You are a senior financial analyst. Analyze these recent news headlines from multiple outlets (BBC, Reuters, CNBC, Bloomberg, Yahoo Finance, etc.) for ${ticker}.

Headlines:
{headlines_text}

Provide a JSON response with:
1. "overall_sentiment": "bullish", "bearish", or "neutral"
2. "sentiment_score": integer -100 to +100
3. "bull_points": list of 2-3 short bullish signals (max 12 words each)
4. "bear_points": list of 2-3 short risk/bearish factors (max 12 words each)
5. "summary": 2-sentence plain English summary of what is moving ${ticker} right now
6. "sources_used": comma-separated list of the news outlets that appeared in the headlines above

Return ONLY valid JSON, no markdown fences:
{{"overall_sentiment":"...","sentiment_score":0,"bull_points":[],"bear_points":[],"summary":"...","sources_used":"..."}}"""

    try:
        resp = _get_client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_tokens=450,
        )
        import json
        raw = resp.choices[0].message.content.strip()
        # Strip markdown code fences if present
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[news] DeepSeek error for {ticker}: {e}")
        return {}


# ── Database helpers ──────────────────────────────────────────────────────────

def init_news_db():
    """Create news tables if they don't exist."""
    import database as db
    with db.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS news_items (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker       TEXT NOT NULL,
                headline     TEXT NOT NULL,
                url          TEXT,
                source_name  TEXT,
                published_at TEXT,
                fetched_at   TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS news_summaries (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker            TEXT NOT NULL,
                overall_sentiment TEXT,
                sentiment_score   INTEGER DEFAULT 0,
                bull_points       TEXT,
                bear_points       TEXT,
                summary           TEXT,
                sources_used      TEXT,
                headline_count    INTEGER DEFAULT 0,
                fetched_at        TEXT DEFAULT (datetime('now'))
            );
            CREATE INDEX IF NOT EXISTS idx_news_ticker     ON news_items(ticker);
            CREATE INDEX IF NOT EXISTS idx_summary_ticker  ON news_summaries(ticker);
        """)
        # Add source_name column if upgrading from older schema
        try:
            conn.execute("ALTER TABLE news_items ADD COLUMN source_name TEXT")
        except Exception:
            pass
        # Add sources_used column if upgrading from older schema
        try:
            conn.execute("ALTER TABLE news_summaries ADD COLUMN sources_used TEXT")
        except Exception:
            pass


def refresh_news_for_ticker(ticker: str):
    """Fetch from all sources, summarise, and store in DB."""
    import database as db
    headlines = fetch_all_sources(ticker)
    if not headlines:
        return

    fetched_at = datetime.now(timezone.utc).isoformat(sep=" ")

    with db.get_conn() as conn:
        conn.execute("DELETE FROM news_items WHERE ticker=?", (ticker,))
        for h in headlines:
            conn.execute(
                """INSERT INTO news_items
                   (ticker, headline, url, source_name, published_at, fetched_at)
                   VALUES (?,?,?,?,?,?)""",
                (ticker, h["title"], h["url"], h.get("source",""),
                 h["published"], fetched_at),
            )

    analysis = summarize_news(ticker, headlines)
    if analysis:
        import json
        with db.get_conn() as conn:
            conn.execute("DELETE FROM news_summaries WHERE ticker=?", (ticker,))
            conn.execute("""
                INSERT INTO news_summaries
                (ticker, overall_sentiment, sentiment_score, bull_points,
                 bear_points, summary, sources_used, headline_count, fetched_at)
                VALUES (?,?,?,?,?,?,?,?,?)
            """, (
                ticker,
                analysis.get("overall_sentiment", "neutral"),
                int(analysis.get("sentiment_score", 0)),
                json.dumps(analysis.get("bull_points", [])),
                json.dumps(analysis.get("bear_points", [])),
                analysis.get("summary", ""),
                analysis.get("sources_used", ""),
                len(headlines),
                fetched_at,
            ))


def refresh_all_news(tickers: list[str]):
    """Refresh news for all given tickers."""
    init_news_db()
    print(f"[news] refreshing {len(tickers)} tickers from Yahoo Finance + Google News...")
    for ticker in tickers:
        print(f"[news] {ticker}...", flush=True)
        refresh_news_for_ticker(ticker)
    print(f"[news] done at {datetime.utcnow().isoformat()}")


def get_news_feed() -> list[dict]:
    """Return all news summaries with headlines for the news page."""
    import database as db, json
    with db.get_conn() as conn:
        summaries = [dict(r) for r in conn.execute(
            "SELECT * FROM news_summaries ORDER BY ABS(sentiment_score) DESC"
        ).fetchall()]
        for s in summaries:
            s["bull_points"] = json.loads(s.get("bull_points") or "[]")
            s["bear_points"] = json.loads(s.get("bear_points") or "[]")
            s["headlines"]   = [dict(r) for r in conn.execute(
                "SELECT headline, url, source_name FROM news_items "
                "WHERE ticker=? ORDER BY fetched_at DESC LIMIT 8",
                (s["ticker"],)
            ).fetchall()]
    return summaries


def get_active_tickers() -> list[str]:
    """Get all tickers currently in the dashboard plus key defaults."""
    import database as db
    data = db.get_dashboard_data("weekly")
    tickers = [d["ticker"] for d in data if d["ticker"] and d["ticker"] != "$"]
    for t in ["NVDA", "AAPL", "TSLA", "META", "MSFT"]:
        if t not in tickers:
            tickers.append(t)
    return tickers[:15]
