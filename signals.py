"""
Trading signals engine.
- Fetches 200 days of OHLCV data via yfinance (free)
- Calculates RSI, MACD, SMA20/50/200, Bollinger Bands, volume trend
- Detects support/resistance from pivot highs/lows
- Detects candlestick patterns (hammer, engulfing, doji, shooting star, etc.)
- Calls DeepSeek to synthesize buy zone / stop loss / targets for 3 timeframes
- Only processes bullish-consensus stocks from the dashboard
"""
import json
import math
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import yfinance as yf
from openai import OpenAI

from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

# ── Ticker mappings ────────────────────────────────────────────────────────────

# Full display names (overrides what Futu/yfinance returns)
DISPLAY_NAMES = {
    # User's portfolio
    "02369": "酷派集团 (Coolpad Group)",
    "2369":  "酷派集团 (Coolpad Group)",
    "DLLL":  "GraniteShares 2x Long DELL Daily ETF",
    "CRWV":  "CoreWeave, Inc.",
    "NOK":   "Nokia",
    # Common tickers
    "NVDA":  "NVIDIA Corporation",
    "AAPL":  "Apple Inc.",
    "TSLA":  "Tesla, Inc.",
    "META":  "Meta Platforms",
    "MSFT":  "Microsoft Corporation",
    "ARKK":  "ARK Innovation ETF",
    "TSM":   "Taiwan Semiconductor",
}

# TradingView exchange prefix (US stocks)
TV_EXCHANGE = {
    "NVDA":"NASDAQ","AAPL":"NASDAQ","TSLA":"NASDAQ","META":"NASDAQ",
    "MSFT":"NASDAQ","AMZN":"NASDAQ","GOOGL":"NASDAQ","GOOG":"NASDAQ",
    "SMCI":"NASDAQ","AMD":"NASDAQ","INTC":"NASDAQ","QCOM":"NASDAQ",
    "ARKK":"AMEX","BITO":"AMEX","ARR":"NYSE","CTAS":"NASDAQ",
    "MA":"NYSE","V":"NYSE","AI":"NYSE","TSM":"NYSE","BRK":"NYSE",
    "SPX":"SP500","SPY":"AMEX","QQQ":"NASDAQ","COIN":"NASDAQ",
    # User's portfolio
    "DLLL":"NASDAQ","CRWV":"NASDAQ","NOK":"NYSE",
}

# yfinance symbol overrides (display ticker → yfinance symbol)
YF_SYMBOL = {
    "SPX":   "^GSPC",
    "BRK":   "BRK-B",
    "BTC":   "BTC-USD",
    "ETH":   "ETH-USD",
    # HK stocks: Futu 5-digit code → yfinance .HK format
    "02369": "2369.HK",
    "2369":  "2369.HK",
}

# HK stocks — use HKEX: prefix in TradingView, .HK suffix in yfinance
HK_TICKERS = {"02369", "2369", "2368", "0700", "9988", "1810", "3690"}

def _is_hk(ticker: str) -> bool:
    """Return True if this is a Hong Kong-listed stock."""
    t = ticker.upper()
    return t in HK_TICKERS or t.endswith(".HK") or (t.isdigit() and len(t) >= 4)

_client = None
def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


# ── Technical calculations ─────────────────────────────────────────────────────

def _rsi(series: pd.Series, period: int = 14) -> float:
    delta = series.diff()
    gain  = delta.clip(lower=0).rolling(period).mean()
    loss  = (-delta.clip(upper=0)).rolling(period).mean()
    rs    = gain / loss.replace(0, np.nan)
    rsi   = 100 - (100 / (1 + rs))
    return round(float(rsi.iloc[-1]), 1)


def _macd(series: pd.Series):
    ema12 = series.ewm(span=12, adjust=False).mean()
    ema26 = series.ewm(span=26, adjust=False).mean()
    macd  = ema12 - ema26
    sig   = macd.ewm(span=9, adjust=False).mean()
    hist  = macd - sig
    return (round(float(macd.iloc[-1]), 3),
            round(float(sig.iloc[-1]),  3),
            round(float(hist.iloc[-1]), 3))


def _bollinger(series: pd.Series, period: int = 20):
    mid   = series.rolling(period).mean()
    std   = series.rolling(period).std()
    upper = mid + 2 * std
    lower = mid - 2 * std
    return (round(float(upper.iloc[-1]), 2),
            round(float(mid.iloc[-1]),   2),
            round(float(lower.iloc[-1]), 2))


def _support_resistance(df: pd.DataFrame, window: int = 5, n: int = 3):
    """Pivot-based support and resistance from last 90 trading days."""
    recent = df.tail(90)
    highs, lows = [], []
    for i in range(window, len(recent) - window):
        hi = recent["High"].iloc[i]
        lo = recent["Low"].iloc[i]
        if all(hi >= recent["High"].iloc[i - j] for j in range(1, window + 1)) and \
           all(hi >= recent["High"].iloc[i + j] for j in range(1, window + 1)):
            highs.append(round(float(hi), 2))
        if all(lo <= recent["Low"].iloc[i - j] for j in range(1, window + 1)) and \
           all(lo <= recent["Low"].iloc[i + j] for j in range(1, window + 1)):
            lows.append(round(float(lo), 2))

    # Cluster nearby levels (within 2%)
    def cluster(levels, pct=0.02):
        levels = sorted(set(levels))
        out = []
        while levels:
            l = levels.pop(0)
            group = [x for x in levels if abs(x - l) / l < pct]
            for g in group:
                levels.remove(g)
            out.append(round(np.mean([l] + group), 2))
        return out

    resistance = sorted(cluster(highs), reverse=True)[:n]
    support    = sorted(cluster(lows),  reverse=True)[:n]
    return support, resistance


def _candle_patterns(df: pd.DataFrame) -> list[str]:
    """Detect candlestick patterns from last 2 candles."""
    if len(df) < 3:
        return []
    last, prev = df.iloc[-1], df.iloc[-2]
    patterns = []

    def body(c):     return abs(c.Close - c.Open)
    def range_(c):   return c.High - c.Low
    def upper_wick(c): return c.High - max(c.Close, c.Open)
    def lower_wick(c): return min(c.Close, c.Open) - c.Low
    def is_green(c): return c.Close > c.Open
    def is_red(c):   return c.Close < c.Open

    r = range_(last)
    b = body(last)

    if r > 0:
        # Doji
        if b / r < 0.1:
            patterns.append("Doji (indecision — trend may reverse)")

        # Hammer (bullish reversal): small body top, long lower wick
        if is_green(last) and lower_wick(last) > 2 * b and upper_wick(last) < b * 0.5:
            patterns.append("Hammer (bullish reversal signal)")

        # Shooting star (bearish): small body bottom, long upper wick
        if is_red(last) and upper_wick(last) > 2 * b and lower_wick(last) < b * 0.5:
            patterns.append("Shooting Star (bearish reversal signal)")

        # Bullish engulfing
        if (is_green(last) and is_red(prev) and
                last.Open < prev.Close and last.Close > prev.Open):
            patterns.append("Bullish Engulfing (strong buy signal)")

        # Bearish engulfing
        if (is_red(last) and is_green(prev) and
                last.Open > prev.Close and last.Close < prev.Open):
            patterns.append("Bearish Engulfing (sell signal)")

        # Marubozu (strong momentum candle — body >80% of range)
        if b / r > 0.8:
            direction = "Bullish" if is_green(last) else "Bearish"
            patterns.append(f"{direction} Marubozu (strong momentum, no wicks)")

        # Spinning top (indecision: small body, wicks on both sides)
        if 0.1 < b / r < 0.3 and upper_wick(last) > b and lower_wick(last) > b:
            patterns.append("Spinning Top (indecision, watch next candle)")

    # Three white soldiers (3 consecutive green candles)
    if len(df) >= 4:
        c1, c2, c3 = df.iloc[-3], df.iloc[-2], df.iloc[-1]
        if all(is_green(c) for c in [c1, c2, c3]) and \
           c2.Open > c1.Open and c3.Open > c2.Open and \
           c3.Close > c2.Close > c1.Close:
            patterns.append("Three White Soldiers (powerful bullish continuation)")

    return patterns or ["No clear candlestick pattern detected"]


def _volume_trend(df: pd.DataFrame) -> str:
    avg = df["Volume"].rolling(20).mean().iloc[-1]
    last_vol = df["Volume"].iloc[-1]
    pct = (last_vol / avg - 1) * 100 if avg else 0
    if pct > 50:   return f"+{pct:.0f}% vs avg (very high — confirms move)"
    if pct > 15:   return f"+{pct:.0f}% vs avg (above average)"
    if pct > -15:  return f"{pct:+.0f}% vs avg (average)"
    return f"{pct:.0f}% vs avg (below average — weak conviction)"


# ── DeepSeek synthesis ─────────────────────────────────────────────────────────

def _synthesize(ticker: str, data: dict) -> dict:
    """Call DeepSeek to produce buy/sell/stop signals for 3 timeframes."""
    price      = data["price"]
    support_s  = ", ".join(f"${s}" for s in data["support"])
    resist_s   = ", ".join(f"${r}" for r in data["resistance"])
    candles_s  = "\n".join(f"  - {p}" for p in data["candle_patterns"])

    last5 = data["last5_candles"]
    candle_rows = "\n".join(
        f"  {r['date']}  O:{r['open']:.2f}  H:{r['high']:.2f}  L:{r['low']:.2f}  C:{r['close']:.2f}  Vol:{r['volume']:,}"
        for r in last5
    )

    sma_pos = []
    if data["sma20"]:  sma_pos.append(f"price {'above' if price > data['sma20'] else 'below'} SMA20 ({data['sma20']})")
    if data["sma50"]:  sma_pos.append(f"{'above' if price > data['sma50'] else 'below'} SMA50 ({data['sma50']})")
    if data["sma200"]: sma_pos.append(f"{'above' if price > data['sma200'] else 'below'} SMA200 ({data['sma200']})")

    prompt = f"""You are a professional technical analyst covering equities. Analyze ${ticker} and generate precise trading signals for three timeframes.

PRICE DATA:
Current price : ${price:.2f}
52-week high  : ${data['week52_high']:.2f}
52-week low   : ${data['week52_low']:.2f}

INDICATORS:
RSI(14)       : {data['rsi']} {"(oversold — bullish)" if data['rsi'] < 35 else "(overbought — caution)" if data['rsi'] > 70 else "(neutral zone)"}
MACD          : {data['macd']}  Signal: {data['macd_signal']}  Histogram: {data['macd_hist']} {"(bullish crossover)" if data['macd_hist'] > 0 else "(bearish)"}
{"; ".join(sma_pos)}
Bollinger     : Upper ${data['bb_upper']}  Mid ${data['bb_mid']}  Lower ${data['bb_lower']}
Volume        : {data['volume_trend']}

KEY LEVELS:
Support       : {support_s}
Resistance    : {resist_s}

CANDLESTICK PATTERNS (recent):
{candles_s}

LAST 5 DAILY CANDLES:
{candle_rows}

Generate three distinct trading setups. For each, calculate levels based on the actual support/resistance and the current price. Return ONLY valid JSON (no markdown):

{{
  "short_term": {{
    "label": "Short-Term (3–10 days)",
    "buy_zone_low": 0.0,
    "buy_zone_high": 0.0,
    "stop_loss": 0.0,
    "take_profit_1": 0.0,
    "take_profit_2": 0.0,
    "risk_reward": "1:X",
    "entry_note": "one sentence on when/why to enter"
  }},
  "swing_trade": {{
    "label": "Swing Trade (1–4 weeks)",
    "buy_zone_low": 0.0,
    "buy_zone_high": 0.0,
    "stop_loss": 0.0,
    "take_profit_1": 0.0,
    "take_profit_2": 0.0,
    "risk_reward": "1:X",
    "entry_note": "one sentence"
  }},
  "position_trade": {{
    "label": "Position Trade (1–3 months)",
    "buy_zone_low": 0.0,
    "buy_zone_high": 0.0,
    "stop_loss": 0.0,
    "take_profit_1": 0.0,
    "take_profit_2": 0.0,
    "risk_reward": "1:X",
    "entry_note": "one sentence"
  }},
  "chart_pattern": "e.g. Ascending triangle breakout / Bull flag / Double bottom",
  "candle_signal": "one sentence describing what the recent candles suggest",
  "technical_summary": "2-3 sentences: overall setup, what to watch, key catalyst",
  "key_level": 0.0,
  "key_level_note": "one sentence: what happens if price breaks above or below this",
  "confidence": "low|medium|high",
  "disclaimer": "Educational only — not financial advice"
}}"""

    try:
        resp = _get_client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_tokens=700,
        )
        raw = resp.choices[0].message.content.strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        return json.loads(raw.strip())
    except Exception as e:
        print(f"[signals] DeepSeek error for {ticker}: {e}")
        return {}


# ── Main entry point ───────────────────────────────────────────────────────────

def compute_signals(ticker: str) -> dict | None:
    """
    Full TA pipeline for one ticker (US or HK).
    Returns a signals dict or None on failure.
    """
    t_upper = ticker.upper()
    is_hk   = _is_hk(ticker)

    # Resolve yfinance symbol
    if t_upper in YF_SYMBOL:
        yf_sym = YF_SYMBOL[t_upper]
    elif is_hk:
        # Convert Futu 5-digit code to yfinance .HK format (e.g. "02369" → "2369.HK")
        clean = ticker.lstrip("0") or "0"
        yf_sym = f"{clean}.HK"
    else:
        yf_sym = t_upper

    # Try to fetch data; for HK stocks with limited history use 100d
    period = "200d"
    for attempt_sym in ([yf_sym] + ([f"0{yf_sym}"] if is_hk and not yf_sym.startswith("0") else [])):
        try:
            df = yf.download(attempt_sym, period=period, interval="1d",
                             auto_adjust=True, progress=False)
            if df is not None and len(df) >= 20:
                yf_sym = attempt_sym
                break
        except Exception:
            pass
    else:
        # One more try with 60d (new IPOs / low-history stocks)
        try:
            df = yf.download(yf_sym, period="60d", interval="1d",
                             auto_adjust=True, progress=False)
        except Exception as e:
            print(f"[signals] yfinance error for {ticker}: {e}")
            return None

    if df is None or len(df) < 15:
        print(f"[signals] not enough data for {ticker} ({yf_sym})")
        return None

    # Flatten multi-level columns if present
    if isinstance(df.columns, pd.MultiIndex):
        df.columns = df.columns.get_level_values(0)

    close = df["Close"].squeeze()
    price = float(close.iloc[-1])

    # Indicators
    rsi                    = _rsi(close)
    macd, macd_sig, macd_h = _macd(close)
    bb_up, bb_mid, bb_lo   = _bollinger(close)
    sma20  = round(float(close.rolling(20).mean().iloc[-1]),  2) if len(close) >= 20  else None
    sma50  = round(float(close.rolling(50).mean().iloc[-1]),  2) if len(close) >= 50  else None
    sma200 = round(float(close.rolling(200).mean().iloc[-1]), 2) if len(close) >= 200 else None

    support, resistance = _support_resistance(df)
    candle_patterns     = _candle_patterns(df)
    vol_trend           = _volume_trend(df)

    week52_high = round(float(df["High"].tail(252).max()), 2)
    week52_low  = round(float(df["Low"].tail(252).min()),  2)

    last5 = []
    for idx, row in df.tail(5).iterrows():
        last5.append({
            "date":   str(idx.date()) if hasattr(idx, "date") else str(idx)[:10],
            "open":   round(float(row["Open"]),   4),
            "high":   round(float(row["High"]),   4),
            "low":    round(float(row["Low"]),    4),
            "close":  round(float(row["Close"]),  4),
            "volume": int(row["Volume"]),
        })

    raw_data = {
        "price": round(price, 4),
        "rsi": rsi,
        "macd": macd, "macd_signal": macd_sig, "macd_hist": macd_h,
        "sma20": sma20, "sma50": sma50, "sma200": sma200,
        "bb_upper": bb_up, "bb_mid": bb_mid, "bb_lower": bb_lo,
        "support": support, "resistance": resistance,
        "candle_patterns": candle_patterns,
        "volume_trend": vol_trend,
        "week52_high": week52_high, "week52_low": week52_low,
        "last5_candles": last5,
    }

    ai = _synthesize(ticker, raw_data)
    if not ai:
        return None

    # Build TradingView symbol
    if is_hk:
        # TradingView HKEX format uses 4-digit codes without leading zeros
        # e.g. Tencent = HKEX:700, Coolpad = HKEX:2369
        clean_code = ticker.lstrip("0") or ticker
        # But codes < 1000 get zero-padding: 700, not 0700
        # Codes >= 1000 (like 2369) are used as-is
        tv_sym = f"HKEX:{clean_code}"
    elif t_upper == "SPX":
        tv_sym = "SP:SPX"
    else:
        exch = TV_EXCHANGE.get(t_upper, "NASDAQ")
        tv_sym = f"{exch}:{t_upper}"

    display_name = DISPLAY_NAMES.get(t_upper) or DISPLAY_NAMES.get(ticker) or ""

    return {
        "ticker":       t_upper,
        "display_name": display_name,
        "tv_symbol":    tv_sym,
        "is_hk":        is_hk,
        **raw_data,
        **ai,
        "fetched_at": datetime.now(timezone.utc).isoformat(sep=" "),
    }


# ── Database persistence ───────────────────────────────────────────────────────

def init_signals_db():
    import database as db
    with db.get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS trading_signals (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                ticker           TEXT NOT NULL UNIQUE,
                display_name     TEXT DEFAULT '',
                is_hk            INTEGER DEFAULT 0,
                tv_symbol        TEXT,
                price            REAL,
                rsi              REAL,
                macd             REAL,
                macd_signal      REAL,
                macd_hist        REAL,
                sma20            REAL,
                sma50            REAL,
                sma200           REAL,
                bb_upper         REAL,
                bb_mid           REAL,
                bb_lower         REAL,
                week52_high      REAL,
                week52_low       REAL,
                support          TEXT,
                resistance       TEXT,
                candle_patterns  TEXT,
                volume_trend     TEXT,
                last5_candles    TEXT,
                short_term       TEXT,
                swing_trade      TEXT,
                position_trade   TEXT,
                chart_pattern    TEXT,
                candle_signal    TEXT,
                technical_summary TEXT,
                key_level        REAL,
                key_level_note   TEXT,
                confidence       TEXT,
                fetched_at       TEXT
            );
        """)


def save_signal(sig: dict):
    import database as db
    with db.get_conn() as conn:
        # Add new columns to existing DB if upgrading
        for col_def in [
            "ALTER TABLE trading_signals ADD COLUMN display_name TEXT DEFAULT ''",
            "ALTER TABLE trading_signals ADD COLUMN is_hk INTEGER DEFAULT 0",
        ]:
            try: conn.execute(col_def)
            except Exception: pass

        conn.execute("""
            INSERT INTO trading_signals
            (ticker, display_name, is_hk, tv_symbol,
             price, rsi, macd, macd_signal, macd_hist,
             sma20, sma50, sma200, bb_upper, bb_mid, bb_lower,
             week52_high, week52_low, support, resistance, candle_patterns,
             volume_trend, last5_candles,
             short_term, swing_trade, position_trade,
             chart_pattern, candle_signal, technical_summary,
             key_level, key_level_note, confidence, fetched_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(ticker) DO UPDATE SET
              display_name=excluded.display_name, is_hk=excluded.is_hk,
              tv_symbol=excluded.tv_symbol, price=excluded.price,
              rsi=excluded.rsi, macd=excluded.macd,
              macd_signal=excluded.macd_signal, macd_hist=excluded.macd_hist,
              sma20=excluded.sma20, sma50=excluded.sma50, sma200=excluded.sma200,
              bb_upper=excluded.bb_upper, bb_mid=excluded.bb_mid,
              bb_lower=excluded.bb_lower,
              week52_high=excluded.week52_high, week52_low=excluded.week52_low,
              support=excluded.support, resistance=excluded.resistance,
              candle_patterns=excluded.candle_patterns,
              volume_trend=excluded.volume_trend,
              last5_candles=excluded.last5_candles,
              short_term=excluded.short_term, swing_trade=excluded.swing_trade,
              position_trade=excluded.position_trade,
              chart_pattern=excluded.chart_pattern,
              candle_signal=excluded.candle_signal,
              technical_summary=excluded.technical_summary,
              key_level=excluded.key_level, key_level_note=excluded.key_level_note,
              confidence=excluded.confidence, fetched_at=excluded.fetched_at
        """, (
            sig["ticker"], sig.get("display_name", ""), int(sig.get("is_hk", False)),
            sig["tv_symbol"],
            sig["price"], sig["rsi"],
            sig["macd"], sig["macd_signal"], sig["macd_hist"],
            sig["sma20"], sig["sma50"], sig["sma200"],
            sig["bb_upper"], sig["bb_mid"], sig["bb_lower"],
            sig["week52_high"], sig["week52_low"],
            json.dumps(sig["support"]), json.dumps(sig["resistance"]),
            json.dumps(sig["candle_patterns"]),
            sig["volume_trend"], json.dumps(sig["last5_candles"]),
            json.dumps(sig.get("short_term", {})),
            json.dumps(sig.get("swing_trade", {})),
            json.dumps(sig.get("position_trade", {})),
            sig.get("chart_pattern", ""),
            sig.get("candle_signal", ""),
            sig.get("technical_summary", ""),
            sig.get("key_level"), sig.get("key_level_note", ""),
            sig.get("confidence", "medium"),
            sig["fetched_at"],
        ))


def get_all_signals() -> list[dict]:
    import database as db
    with db.get_conn() as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM trading_signals ORDER BY fetched_at DESC"
        ).fetchall()]
    valid = []
    for r in rows:
        for col in ("support", "resistance", "candle_patterns",
                    "last5_candles", "short_term", "swing_trade", "position_trade"):
            try:
                r[col] = json.loads(r[col] or "[]")
            except Exception:
                r[col] = []
        if r.get("price"):
            valid.append(r)
    return valid


def get_bullish_tickers() -> list[str]:
    """Return tickers that currently have bullish consensus (weekly)."""
    import database as db
    data = db.get_dashboard_data("weekly")
    return [d["ticker"] for d in data
            if d.get("consensus") == "bullish" and d.get("ticker")]


def get_dashboard_tickers() -> list[str]:
    """Return ALL tickers mentioned in the dashboard regardless of consensus."""
    import database as db
    data = db.get_dashboard_data("weekly")
    return [d["ticker"] for d in data if d.get("ticker")]


def get_signal_for_ticker(ticker: str) -> dict | None:
    """Return the cached signal for one ticker, or None if not found."""
    import database as db
    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM trading_signals WHERE ticker=?", (ticker.upper(),)
        ).fetchone()
    if not row:
        return None
    r = dict(row)
    for col in ("support", "resistance", "candle_patterns",
                "last5_candles", "short_term", "swing_trade", "position_trade"):
        try:
            r[col] = json.loads(r[col] or "[]")
        except Exception:
            r[col] = []
    return r if r.get("price") else None


def refresh_portfolio_signals():
    """
    Compute signals for the user's live Futu portfolio positions.
    Called separately from refresh_all_signals so portfolio stocks always
    get analysis regardless of consensus state.
    """
    init_signals_db()
    try:
        import futu_portfolio as fp
        if not fp.is_connected():
            print("[signals] FutuOpenD not running — skipping portfolio refresh")
            return
        positions = fp.get_positions()
        tickers   = [p["ticker"] for p in positions if p.get("ticker")]
    except Exception as e:
        print(f"[signals] portfolio fetch error: {e}")
        return

    if not tickers:
        return

    print(f"[signals] computing TA for {len(tickers)} portfolio stocks: {tickers}")
    for t in tickers:
        print(f"[signals] {t}...", flush=True)
        sig = compute_signals(t)
        if sig:
            save_signal(sig)
        else:
            print(f"[signals] no data for {t}")
    print(f"[signals] portfolio signals done at {datetime.utcnow().isoformat()}")


def refresh_all_signals():
    """Compute and save signals for all dashboard tickers."""
    init_signals_db()
    tickers = get_dashboard_tickers()
    if not tickers:
        print("[signals] no dashboard tickers to process")
        return
    print(f"[signals] computing TA for {len(tickers)} dashboard tickers: {tickers}")
    for t in tickers:
        print(f"[signals] {t}...", flush=True)
        sig = compute_signals(t)
        if sig:
            save_signal(sig)
        else:
            print(f"[signals] skipped {t} (no data)")
    print(f"[signals] done at {datetime.utcnow().isoformat()}")
