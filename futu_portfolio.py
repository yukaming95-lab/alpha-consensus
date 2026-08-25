"""
Futu OpenAPI integration.
Connects to a locally-running FutuOpenD gateway (127.0.0.1:11111).
Fetches real account positions and merges them with our signals & consensus data.

Setup (one time):
  1. Download FutuOpenD from www.futunn.com → Developer → OpenAPI
  2. Install, log in with your Futu account, keep it running
  3. Default host: 127.0.0.1  port: 11111
"""
import json
from datetime import datetime, timezone


FUTU_HOST = "127.0.0.1"
FUTU_PORT = 11111


def _is_opend_running() -> bool:
    """Quick TCP probe — returns True if FutuOpenD is reachable."""
    import socket
    try:
        s = socket.create_connection((FUTU_HOST, FUTU_PORT), timeout=2)
        s.close()
        return True
    except OSError:
        return False


def get_positions(trade_env: str = "REAL") -> list[dict]:
    """
    Fetch all positions from FutuOpenD (US + HK markets combined).

    Returns list of dicts:
      ticker, full_code, market, name, qty, cost_price, current_price,
      market_val, pnl, pnl_pct, currency
    """
    if not _is_opend_running():
        return []

    all_positions = []

    # Fetch US market positions
    for market_filter, market_label, currency_default in [
        ("US", "US", "USD"),
        ("HK", "HK", "HKD"),
    ]:
        try:
            import futu as ft
            env       = ft.TrdEnv.REAL if trade_env == "REAL" else ft.TrdEnv.SIMULATE
            mkt_const = ft.TrdMarket.US if market_label == "US" else ft.TrdMarket.HK
            trd_ctx   = ft.OpenSecTradeContext(
                filter_trdmarket=mkt_const,
                host=FUTU_HOST, port=FUTU_PORT,
                security_firm=ft.SecurityFirm.FUTUSECURITIES,
            )
            ret, data = trd_ctx.position_list_query(trd_env=env)
            trd_ctx.close()
        except Exception as e:
            print(f"[futu] error fetching {market_label} positions: {e}")
            continue

        if ret != 0 or data is None or data.empty:
            continue

        expected_prefix = market_label + "."   # "US." or "HK."
        for _, row in data.iterrows():
            full_code = str(row.get("code", ""))
            # Only keep positions that belong to this market (avoid cross-market duplicates)
            if not full_code.startswith(expected_prefix):
                continue
            ticker = full_code[len(expected_prefix):]   # "US.NVDA" → "NVDA"
            display_ticker = ticker.lstrip("0") if market_label == "HK" else ticker
            qty    = int(row.get("qty", 0))
            if qty == 0:
                continue
            cost  = float(row.get("cost_price", 0) or 0)
            mval  = float(row.get("market_val", 0) or 0)
            cur   = mval / qty if qty else 0
            pnl   = float(row.get("pl_val", 0) or 0)
            # Futu returns pl_ratio already as a percentage (e.g. -28.5 means -28.5%)
            pnl_pct = float(row.get("pl_ratio", 0) or 0)

            all_positions.append({
                "ticker":        ticker,
                "display_ticker": display_ticker,
                "full_code":     full_code,
                "market":        market_label,
                "name":          str(row.get("stock_name", ticker)),
                "qty":           qty,
                "cost_price":    round(cost, 2),
                "current_price": round(cur, 3),
                "market_val":    round(mval, 2),
                "pnl":           round(pnl, 2),
                "pnl_pct":       round(pnl_pct, 2),
                "currency":      str(row.get("currency", currency_default)),
            })

    return all_positions


def get_account_info(trade_env: str = "REAL") -> dict:
    """Return combined account summary (HK account as primary)."""
    if not _is_opend_running():
        return {}
    results = {}
    for mkt_const_name, label in [("HK", "HK"), ("US", "US")]:
        try:
            import futu as ft
            env     = ft.TrdEnv.REAL if trade_env == "REAL" else ft.TrdEnv.SIMULATE
            mkt_c   = ft.TrdMarket.HK if mkt_const_name == "HK" else ft.TrdMarket.US
            trd_ctx = ft.OpenSecTradeContext(
                filter_trdmarket=mkt_c,
                host=FUTU_HOST, port=FUTU_PORT,
                security_firm=ft.SecurityFirm.FUTUSECURITIES,
            )
            ret, data = trd_ctx.accinfo_query(trd_env=env)
            trd_ctx.close()
            if ret == 0 and data is not None and not data.empty:
                row = data.iloc[0]
                results[label] = {
                    "total_assets": round(float(row.get("total_assets", 0) or 0), 2),
                    "cash":         round(float(row.get("cash",         0) or 0), 2),
                    "market_val":   round(float(row.get("market_val",   0) or 0), 2),
                    "buying_power": round(float(row.get("power",        0) or 0), 2),
                    "currency":     str(row.get("currency", label == "HK" and "HKD" or "USD")),
                }
        except Exception as e:
            print(f"[futu] account info {label} error: {e}")

    # Prefer HK account as primary (most users have HK-based Futu account)
    primary = results.get("HK") or results.get("US") or {}
    primary["fetched_at"] = datetime.now(timezone.utc).isoformat(sep=" ")
    primary["markets"] = list(results.keys())
    return primary


def enrich_with_signals(positions: list[dict]) -> list[dict]:
    """
    For each position, attach:
    - consensus (bullish/bearish/neutral) from our dashboard
    - cached trading signal (buy zone, stop loss, targets) if available
    - alert flags (near stop loss, overbought, etc.)
    """
    import database as db, signals as sg

    # Grab consensus data (weekly window)
    dashboard = db.get_dashboard_data("weekly")
    consensus_map = {d["ticker"].upper(): d for d in dashboard}

    # Grab cached signals
    all_sigs = sg.get_all_signals()
    signal_map = {s["ticker"].upper(): s for s in all_sigs}

    enriched = []
    for pos in positions:
        ticker  = pos["ticker"].upper()
        cons    = consensus_map.get(ticker, {})
        sig     = signal_map.get(ticker, {})

        alerts = []

        # Check if near stop loss for any timeframe
        price = pos["current_price"] or pos["cost_price"]
        for tf_key in ("short_term", "swing_trade", "position_trade"):
            tf = sig.get(tf_key) or {}
            if isinstance(tf, str):
                try: tf = json.loads(tf)
                except: tf = {}
            sl = tf.get("stop_loss")
            if sl and price and price <= sl * 1.03:
                alerts.append(f"⚠️ Near {tf_key.replace('_',' ')} stop loss (${sl:.2f})")
                break

        # Check P&L status
        if pos["pnl_pct"] <= -10:
            alerts.append(f"🔴 Down {pos['pnl_pct']:.1f}% — review stop loss")
        elif pos["pnl_pct"] >= 20:
            alerts.append(f"🟢 Up {pos['pnl_pct']:.1f}% — consider taking partial profit")

        # Check consensus
        consensus = cons.get("consensus", "")
        if consensus == "bearish":
            alerts.append("⚡ Traders are BEARISH on this stock")
        elif consensus == "contested":
            alerts.append("⚡ Trader consensus is CONTESTED")

        enriched.append({
            **pos,
            "consensus":     consensus,
            "consensus_score": cons.get("score", 0),
            "signal":        sig,
            "alerts":        alerts,
        })

    # Sort: most alerts first, then by market value
    enriched.sort(key=lambda x: (-len(x["alerts"]), -x.get("market_val", 0)))
    return enriched


def is_connected() -> bool:
    return _is_opend_running()
