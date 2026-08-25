"""
X data fetcher.
- Manual mode (default): posts are added via the admin UI.
- X API mode: if X_BEARER_TOKEN is set, fetches the last 10 tweets per tracked
  handle every UPDATE_INTERVAL_HOURS hours using APScheduler.
"""
import requests
from datetime import datetime, timezone

from config import X_BEARER_TOKEN, UPDATE_INTERVAL_HOURS
import database as db
import analyzer


def fetch_and_store_handle(handle: str):
    """Fetch recent tweets for one handle via X API v2 and store them."""
    if not X_BEARER_TOKEN:
        return

    trader_id = db.get_trader_id(handle)
    if not trader_id:
        return

    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    params  = {
        "query": f"from:{handle} -is:retweet",
        "max_results": 10,  # API minimum is 10
        "tweet.fields": "created_at,text",
        "expansions": "author_id",
    }
    try:
        resp = requests.get(
            "https://api.twitter.com/2/tweets/search/recent",
            headers=headers,
            params=params,
            timeout=15,
        )
        resp.raise_for_status()
        data = resp.json().get("data", [])
    except Exception as e:
        print(f"[fetcher] X API error for @{handle}: {e}")
        return

    for tweet in data:
        posted_at = tweet.get("created_at", datetime.now(timezone.utc).isoformat())
        url = f"https://x.com/{handle}/status/{tweet['id']}"
        post_id = db.add_post(trader_id, tweet["text"], url, posted_at)
        mentions = analyzer.analyze_post(tweet["text"], handle)
        for m in mentions:
            db.add_mention(post_id, m["ticker"], m["stance"], m["reasoning"], m["confidence"],
                           m.get("sentiment_score", 0), m.get("key_phrases", ""))


def fetch_all_handles():
    """Called by the scheduler — refresh all active traders."""
    traders = db.get_all_traders()
    for t in traders:
        if t.get("active"):
            fetch_and_store_handle(t["x_handle"])
    print(f"[fetcher] completed refresh at {datetime.utcnow().isoformat()}")


def refresh_portfolios():
    """Refresh 13F portfolio cache — runs weekly (SEC filings are quarterly)."""
    import portfolio as pf
    traders = db.get_all_traders()
    for t in traders:
        handle = t["x_handle"]
        if handle in pf.FUND_CIKS:
            print(f"[portfolio] refreshing 13F for @{handle}...")
            pf.get_portfolio(handle)
    print(f"[portfolio] weekly 13F refresh done at {datetime.utcnow().isoformat()}")


def start_scheduler(app):
    """Start background scheduler if X API token is available."""
    from apscheduler.schedulers.background import BackgroundScheduler
    scheduler = BackgroundScheduler()

    if X_BEARER_TOKEN:
        scheduler.add_job(
            fetch_all_handles,
            "interval",
            hours=UPDATE_INTERVAL_HOURS,
            id="x_refresh",
            replace_existing=True,
        )
        print(f"[fetcher] Tweet refresh every {UPDATE_INTERVAL_HOURS}h.")
    else:
        print("[fetcher] No X_BEARER_TOKEN — tweet fetching is manual.")

    # 13F portfolio: refresh once a week (SEC filings are quarterly)
    scheduler.add_job(
        refresh_portfolios,
        "interval",
        weeks=1,
        id="portfolio_refresh",
        replace_existing=True,
    )
    print("[fetcher] 13F portfolio refresh every 7 days.")

    # Daily news: refresh every 24 hours
    scheduler.add_job(
        _refresh_news_job,
        "interval",
        hours=24,
        id="news_refresh",
        replace_existing=True,
    )
    print("[fetcher] News refresh every 24h.")

    # Daily trading signals: refresh every 24 hours (after market close)
    scheduler.add_job(
        _refresh_signals_job,
        "interval",
        hours=24,
        id="signals_refresh",
        replace_existing=True,
    )
    print("[fetcher] Trading signals refresh every 24h.")

    import threading
    # Run tweets + news + signals once at startup in background threads
    if X_BEARER_TOKEN:
        threading.Thread(target=fetch_all_handles, daemon=True).start()
    threading.Thread(target=_refresh_news_job,    daemon=True).start()
    threading.Thread(target=_refresh_signals_job, daemon=True).start()

    scheduler.start()


def _refresh_news_job():
    try:
        import news as nf
        tickers = nf.get_active_tickers()
        nf.refresh_all_news(tickers)
    except Exception as e:
        print(f"[news] refresh job error: {e}")


def _refresh_signals_job():
    try:
        import signals as sg
        sg.refresh_all_signals()
        sg.refresh_portfolio_signals()   # also always cover live portfolio
    except Exception as e:
        print(f"[signals] refresh job error: {e}")
