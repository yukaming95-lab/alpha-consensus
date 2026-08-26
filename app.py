import os
from flask import Flask, render_template, request, jsonify, redirect, url_for
from datetime import datetime, timezone

import database as db
import analyzer
import fetcher
import portfolio as pf
import news as nf
import signals as sg
import futu_portfolio as fp

app = Flask(__name__)
app.config["PROPAGATE_EXCEPTIONS"] = True


@app.before_request
def _setup():
    app.before_request_funcs[None].remove(_setup)
    db.init_db()
    db.init_portfolio_snapshot_table()
    nf.init_news_db()
    sg.init_signals_db()
    fetcher.start_scheduler(app)


# ── Dashboard ──────────────────────────────────────────────────────────────────

@app.route("/")
def dashboard():
    period = request.args.get("period", "weekly")
    data   = db.get_dashboard_data(period)
    totals = {
        "stocks":   len(data),
        "bullish":  sum(1 for d in data if d["consensus"] == "bullish"),
        "bearish":  sum(1 for d in data if d["consensus"] == "bearish"),
        "contested":sum(1 for d in data if d["consensus"] == "contested"),
    }
    return render_template("dashboard.html", data=data, period=period, totals=totals)


# ── Stock detail ───────────────────────────────────────────────────────────────

@app.route("/stock/<ticker>")
def stock(ticker):
    detail = db.get_stock_detail(ticker.upper())
    return render_template("stock.html", detail=detail)


# ── Trader detail ──────────────────────────────────────────────────────────────

@app.route("/trader/<handle>")
def trader(handle):
    detail    = db.get_trader_detail(handle)
    portfolio = pf.get_portfolio(handle)
    return render_template("trader.html", detail=detail, portfolio=portfolio)


# ── My Portfolio (Futu) ───────────────────────────────────────────────────────

PORTFOLIO_SYNC_KEY = os.environ.get("PORTFOLIO_SYNC_KEY", "")

@app.route("/api/portfolio/sync", methods=["POST"])
def portfolio_sync():
    key = request.headers.get("X-Sync-Key", "")
    if not PORTFOLIO_SYNC_KEY or key != PORTFOLIO_SYNC_KEY:
        return jsonify({"error": "unauthorized"}), 401
    body = request.get_json(force=True)
    db.save_portfolio_snapshot(body.get("positions", []), body.get("account", {}))
    return jsonify({"ok": True})


@app.route("/api/tweets/sync", methods=["POST"])
def tweets_sync():
    """Receive raw tweets from local PC, analyze with DeepSeek, store mentions."""
    key = request.headers.get("X-Sync-Key", "")
    if not PORTFOLIO_SYNC_KEY or key != PORTFOLIO_SYNC_KEY:
        return jsonify({"error": "unauthorized"}), 401
    body   = request.get_json(force=True)
    tweets = body.get("tweets", [])
    saved  = 0
    for tw in tweets:
        handle    = tw.get("handle", "").strip()
        text      = tw.get("text", "").strip()
        url       = tw.get("url", "")
        posted_at = tw.get("posted_at", datetime.now(timezone.utc).isoformat())
        if not handle or not text:
            continue
        trader_id = db.get_trader_id(handle)
        if not trader_id:
            continue
        post_id  = db.add_post(trader_id, text, url, posted_at)
        mentions = analyzer.analyze_post(text, handle)
        for m in mentions:
            db.add_mention(post_id, m["ticker"], m["stance"], m["reasoning"],
                           m["confidence"], m.get("sentiment_score", 0),
                           m.get("key_phrases", ""))
        saved += 1
    return jsonify({"ok": True, "saved": saved})


@app.route("/my-portfolio")
def my_portfolio():
    connected = fp.is_connected()
    env       = request.args.get("env", "REAL")
    positions = []
    account   = {}
    synced_at = None

    if connected:
        raw_pos   = fp.get_positions(trade_env=env)
        positions = fp.enrich_with_signals(raw_pos)
        account   = fp.get_account_info(trade_env=env)
    else:
        snapshot = db.get_portfolio_snapshot()
        if snapshot:
            positions = fp.enrich_with_signals(snapshot["positions"])
            account   = snapshot["account"]
            synced_at = snapshot["synced_at"]

    return render_template("my_portfolio.html",
                           connected=connected,
                           positions=positions,
                           account=account,
                           env=env,
                           synced_at=synced_at)

# ── Trading Signals ───────────────────────────────────────────────────────────

@app.route("/signals")
def signals():
    all_sigs = sg.get_all_signals()
    return render_template("signals.html", signals=all_sigs)


@app.route("/api/signals/refresh", methods=["POST"])
def api_signals_refresh():
    import threading
    threading.Thread(target=sg.refresh_all_signals, daemon=True).start()
    return jsonify({"status": "refreshing"})


# ── News ──────────────────────────────────────────────────────────────────────

@app.route("/news")
def news():
    feed = nf.get_news_feed()
    return render_template("news.html", feed=feed)


@app.route("/api/news/refresh", methods=["POST"])
def api_news_refresh():
    tickers = nf.get_active_tickers()
    import threading
    threading.Thread(target=nf.refresh_all_news, args=(tickers,), daemon=True).start()
    return jsonify({"status": "refreshing", "tickers": tickers})


# ── Q&A ────────────────────────────────────────────────────────────────────────

@app.route("/qa")
def qa():
    return render_template("qa.html")


@app.route("/api/qa", methods=["POST"])
def api_qa():
    import re
    import signals as sg
    body     = request.get_json(force=True)
    question = (body.get("question") or "").strip()
    if not question:
        return jsonify({"answer": "Please enter a question."}), 400
    context = db.get_qa_context(question)

    # Detect any ticker mentioned (e.g. $NVDA or just NVDA)
    tickers = re.findall(r'\$([A-Z]{1,6})', question.upper())
    if not tickers:
        tickers = re.findall(r'\b([A-Z]{2,6})\b', question.upper())
    signal = None
    for t in tickers:
        sig = sg.get_signal_for_ticker(t)
        if sig:
            signal = sig
            break

    answer = analyzer.answer_question(question, context, signal)
    return jsonify({"answer": answer, "signal": signal})


# ── Admin ──────────────────────────────────────────────────────────────────────

@app.route("/admin")
def admin():
    traders = db.get_all_traders()
    return render_template("admin.html", traders=traders)


@app.route("/admin/add-post", methods=["POST"])
def admin_add_post():
    handle    = request.form.get("handle", "").strip()
    post_text = request.form.get("post_text", "").strip()
    post_url  = request.form.get("post_url", "").strip()
    posted_at = request.form.get("posted_at", "").strip()

    if not handle or not post_text:
        return jsonify({"error": "handle and post_text are required"}), 400

    if not posted_at:
        posted_at = datetime.now(timezone.utc).isoformat(sep=" ")

    trader_id = db.get_trader_id(handle)
    if not trader_id:
        return jsonify({"error": f"Unknown trader handle: {handle}"}), 404

    post_id  = db.add_post(trader_id, post_text, post_url, posted_at)
    mentions = analyzer.analyze_post(post_text, handle)
    for m in mentions:
        db.add_mention(post_id, m["ticker"], m["stance"], m["reasoning"], m["confidence"],
                       m.get("sentiment_score", 0), m.get("key_phrases", ""))

    return jsonify({
        "status":   "ok",
        "post_id":  post_id,
        "mentions": mentions,
    })


@app.route("/admin/toggle-trader", methods=["POST"])
def toggle_trader():
    handle = request.form.get("handle", "").strip()
    import sqlite3
    with db.get_conn() as conn:
        conn.execute(
            "UPDATE traders SET active = 1 - active WHERE x_handle = ?", (handle,)
        )
    return redirect(url_for("admin"))


# ── API: dashboard data (JSON) ─────────────────────────────────────────────────

@app.route("/api/dashboard")
def api_dashboard():
    period = request.args.get("period", "daily")
    return jsonify(db.get_dashboard_data(period))


if __name__ == "__main__":
    app.run(debug=False, host="0.0.0.0", port=5000)
