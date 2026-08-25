"""
Load realistic demo posts so the dashboard has data to display immediately.
Run once: python seed_data.py
"""
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))

import database as db
import analyzer
from datetime import datetime, timedelta, timezone

db.init_db()

DEMO_POSTS = [
    # handle, post_text, days_ago
    ("unusual_whales",  "$NVDA seeing massive call sweeps at the 900 strike, expiring Friday. Unusual bullish activity — someone knows something. $NVDA 🟢", 0),
    ("unusual_whales",  "Dark pool prints on $AAPL today: $420M notional, bullish flow dominates. Institutions accumulating ahead of earnings?", 0),
    ("unusual_whales",  "$TSLA put volume 3x average today. Bears loading up. $TSLA 🔴", 1),
    ("OptionsHawk",     "NVDA weekly calls flying off the shelves, IV elevated. I'm watching the 920 level for a breakout. Bullish $NVDA.", 0),
    ("OptionsHawk",     "$META 480 calls very active — positive momentum play into next week. Bullish stance.", 0),
    ("OptionsHawk",     "Heavy put buying in $SPY today. Macro risk on. Neutral to slightly bearish short-term.", 1),
    ("zerohedge",       "Fed minutes signal higher-for-longer rates — bad news for high-multiple tech. $QQQ selloff imminent. Bearish $QQQ $AAPL $MSFT", 0),
    ("zerohedge",       "Oil inventories surprise to the downside. $XOM $CVX looking strong as energy tightens. Bullish energy sector.", 1),
    ("zerohedge",       "Bond market stress spreading. $TLT testing support. Neutral on equities near-term.", 2),
    ("DeItaone",        "BREAKING: $AAPL reports record Q4 iPhone sales in China. Stock up 3% pre-market. Bullish $AAPL", 0),
    ("DeItaone",        "BREAKING: $TSLA price cut 5% in Europe, margins under pressure. Bearish $TSLA", 0),
    ("DeItaone",        "$NVDA CEO interview tonight — hinting at next-gen Blackwell demand exceeding supply through 2025. Very bullish $NVDA", 1),
    ("chamath",         "We're still early in the AI infra buildout. $NVDA isn't expensive when you model 3-year forward earnings. Long $NVDA.", 0),
    ("chamath",         "$AAPL's services business is undervalued. The stock multiple doesn't reflect recurring revenue quality. Long $AAPL", 3),
    ("chamath",         "Rates staying high longer means growth multiples contract. Cautious on $ARKK type names. Bearish speculative growth.", 5),
    ("StockMoe",        "Loading up $TSLA calls! CEO tweet pump incoming, momentum is back, chart looks great. Bullish $TSLA 🚀🚀", 0),
    ("StockMoe",        "$AMC and $GME dead cat bounces — avoid. Bearish meme stocks.", 2),
    ("StockMoe",        "NVDA to $1000 EOY? Chart says yes. Breakout imminent. Bullish $NVDA 🔥", 1),
    ("TradingView",     "$AAPL daily chart: ascending triangle forming, target $210 on breakout. Bullish setup. #technicalanalysis", 0),
    ("TradingView",     "$TSLA descending wedge — breakdown below $230 confirms bearish trend. Target $200. Bearish $TSLA", 1),
    ("TradingView",     "$SPY at key resistance 505. Watch for rejection or breakout — neutral until confirmation.", 0),
    ("markminervini",   "$NVDA shows a proper VCP base, tight action, above 50-day. This is a Stage 2 leader. Bullish $NVDA", 0),
    ("markminervini",   "$META has all the SEPA criteria — earnings acceleration, relative strength. Adding to position. Bullish $META", 2),
    ("markminervini",   "$TSLA failed follow-through day, distribution days stacking up. Exiting. Bearish $TSLA", 3),
    ("realWillMeade",   "Hedge funds BUYING $NVDA on every dip per 13F filings. Smart money is clear. Bullish $NVDA", 0),
    ("realWillMeade",   "Sovereign wealth funds rotating into $MSFT $GOOG. AI picks they believe in. Bullish $MSFT $GOOG", 1),
    ("realWillMeade",   "$TSLA: 5 major funds cut stakes last quarter. Follow the money. Bearish $TSLA", 4),
    ("BurryArchive",    "Burry's latest 13F shows new put positions on $AAPL — $22M notional. Bearish signal from the Big Short. $AAPL 🔴", 0),
    ("BurryArchive",    "Burry added $JD $BABA — China tech value bet. Contrarian bullish $JD $BABA", 1),
    ("BurryArchive",    "Burry filed new position in $GEO — prison REIT. Idiosyncratic value play, neutral read for broader market.", 5),
]

print("Seeding demo data...")
count = 0
for handle, text, days_ago in DEMO_POSTS:
    trader_id = db.get_trader_id(handle)
    if not trader_id:
        print(f"  [skip] unknown handle: {handle}")
        continue

    posted_at = (datetime.now(timezone.utc) - timedelta(days=days_ago)).isoformat(sep=" ")
    post_id   = db.add_post(trader_id, text, "", posted_at)
    mentions  = analyzer.analyze_post(text, handle)

    if not mentions:
        import re
        tickers = re.findall(r'\$([A-Z]{1,5})', text)
        for ticker in tickers:
            if "bearish" in text.lower() or "🔴" in text or "put" in text.lower():
                stance, score = "bearish", -60
            elif "neutral" in text.lower() or "watch" in text.lower():
                stance, score = "neutral", 0
            else:
                stance, score = "bullish", 60
            db.add_mention(post_id, ticker, stance, "", 0.7, score, "")
            count += 1
    else:
        for m in mentions:
            db.add_mention(post_id, m["ticker"], m["stance"], m["reasoning"], m["confidence"],
                           m.get("sentiment_score", 0), m.get("key_phrases", ""))
            count += 1

print(f"Done. Inserted {len(DEMO_POSTS)} posts → {count} ticker mentions.")
print("Open http://localhost:5000 after starting the app to see the dashboard.")
