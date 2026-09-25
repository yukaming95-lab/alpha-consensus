"""
sync_portfolio.py — runs on your LOCAL computer.
Reads Futu positions and pushes them to the cloud server every 10 minutes.

Run once manually to test:  python sync_portfolio.py
Auto-start: add to Task Scheduler (runs at login, repeats every 10 min)
"""
import os, time, json, requests
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

CLOUD_URL  = "https://web-production-9424.up.railway.app/api/portfolio/sync"
SYNC_KEY   = os.getenv("PORTFOLIO_SYNC_KEY", "")
INTERVAL   = 600  # seconds (10 minutes)


def push_once():
    try:
        import futu_portfolio as fp
        if not fp.is_connected():
            print(f"[{datetime.now():%H:%M}] FutuOpenD not running — skipping")
            return False

        positions = fp.get_positions()
        account   = fp.get_account_info()

        payload = {"positions": positions, "account": account}
        r = requests.post(
            CLOUD_URL,
            json=payload,
            headers={"X-Sync-Key": SYNC_KEY},
            timeout=15,
        )
        if r.status_code == 200:
            print(f"[{datetime.now():%H:%M}] Synced {len(positions)} positions → cloud OK")
            return True
        else:
            print(f"[{datetime.now():%H:%M}] Sync failed: {r.status_code} {r.text[:200]}")
            return False
    except Exception as e:
        print(f"[{datetime.now():%H:%M}] Error: {e}")
        return False


if __name__ == "__main__":
    print(f"Portfolio sync started — pushing to {CLOUD_URL} every {INTERVAL//60} min")
    while True:
        push_once()
        time.sleep(INTERVAL)
