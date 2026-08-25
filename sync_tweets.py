"""
sync_tweets.py — runs on your LOCAL computer.
Fetches tweets from X API (not blocked locally) and pushes raw tweet text
to the cloud server, which then analyzes them with DeepSeek.

Run once manually:   python sync_tweets.py
Auto-start: Windows Task Scheduler, repeat every 3 hours.
"""
import os, time, requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

CLOUD_URL      = "https://yukaming.pythonanywhere.com/api/tweets/sync"
SYNC_KEY       = os.getenv("PORTFOLIO_SYNC_KEY", "")
X_BEARER_TOKEN = os.getenv("X_BEARER_TOKEN", "")
INTERVAL       = 3 * 3600   # 3 hours in seconds
MAX_RESULTS    = 10          # X API minimum

HANDLES = [
    "unusual_whales", "OptionsHawk", "DeItaone", "jimcramer", "kevinolearytv",
    "BillAckman", "CathieDWood", "chamath", "DanielSLoeb", "BurryArchive",
    "RaoulGMI", "elerianm", "PeterSchiff",
]


def fetch_tweets_for(handle: str) -> list:
    """Fetch up to MAX_RESULTS recent original tweets for one handle."""
    headers = {"Authorization": f"Bearer {X_BEARER_TOKEN}"}
    params  = {
        "query":       f"from:{handle} -is:retweet",
        "max_results": MAX_RESULTS,
        "tweet.fields": "created_at,text",
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
        return [
            {
                "handle":    handle,
                "text":      tw["text"],
                "url":       f"https://x.com/{handle}/status/{tw['id']}",
                "posted_at": tw.get("created_at", datetime.utcnow().isoformat()),
            }
            for tw in data
        ]
    except Exception as e:
        print(f"  [{handle}] X API error: {e}")
        return []


def push_once():
    if not X_BEARER_TOKEN:
        print("No X_BEARER_TOKEN in .env — cannot fetch tweets.")
        return
    if not SYNC_KEY:
        print("No PORTFOLIO_SYNC_KEY in .env — cannot authenticate with cloud.")
        return

    all_tweets = []
    print(f"[{datetime.now():%H:%M}] Fetching tweets for {len(HANDLES)} traders...")
    for handle in HANDLES:
        tweets = fetch_tweets_for(handle)
        print(f"  @{handle}: {len(tweets)} tweets")
        all_tweets.extend(tweets)

    if not all_tweets:
        print("No tweets fetched — skipping push.")
        return

    try:
        r = requests.post(
            CLOUD_URL,
            json={"tweets": all_tweets},
            headers={"X-Sync-Key": SYNC_KEY},
            timeout=60,   # DeepSeek analysis takes time server-side
        )
        if r.status_code == 200:
            data = r.json()
            print(f"[{datetime.now():%H:%M}] Pushed {len(all_tweets)} tweets → cloud saved {data.get('saved',0)} (duplicates skipped)")
        else:
            print(f"[{datetime.now():%H:%M}] Push failed: {r.status_code} {r.text[:300]}")
    except Exception as e:
        print(f"[{datetime.now():%H:%M}] Push error: {e}")


if __name__ == "__main__":
    print(f"Tweet sync started — fetching every {INTERVAL//3600}h and pushing to cloud")
    while True:
        push_once()
        print(f"Next sync in {INTERVAL//3600} hours...")
        time.sleep(INTERVAL)
