import sqlite3
import json
from datetime import datetime, timedelta
from config import DB_PATH, TRADERS


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db():
    with get_conn() as conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS traders (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                x_handle    TEXT UNIQUE NOT NULL,
                name        TEXT NOT NULL,
                description TEXT,
                active      INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS posts (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                trader_id   INTEGER NOT NULL REFERENCES traders(id),
                post_text   TEXT NOT NULL,
                post_url    TEXT,
                posted_at   TEXT NOT NULL,
                fetched_at  TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE TABLE IF NOT EXISTS mentions (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                post_id         INTEGER NOT NULL REFERENCES posts(id),
                ticker          TEXT NOT NULL,
                stance          TEXT NOT NULL CHECK(stance IN ('bullish','bearish','neutral')),
                sentiment_score INTEGER DEFAULT 0,
                reasoning       TEXT,
                key_phrases     TEXT,
                confidence      REAL DEFAULT 0.8,
                analyzed_at     TEXT NOT NULL DEFAULT (datetime('now'))
            );

            CREATE INDEX IF NOT EXISTS idx_mentions_ticker ON mentions(ticker);
            CREATE INDEX IF NOT EXISTS idx_posts_trader   ON posts(trader_id);
            CREATE INDEX IF NOT EXISTS idx_posts_date     ON posts(posted_at);
        """)
        # Migrate existing DB — add new columns if missing
        existing = [r[1] for r in conn.execute("PRAGMA table_info(mentions)").fetchall()]
        if "sentiment_score" not in existing:
            conn.execute("ALTER TABLE mentions ADD COLUMN sentiment_score INTEGER DEFAULT 0")
        if "key_phrases" not in existing:
            conn.execute("ALTER TABLE mentions ADD COLUMN key_phrases TEXT")

        for t in TRADERS:
            conn.execute("""
                INSERT INTO traders (x_handle, name, description)
                VALUES (?, ?, ?)
                ON CONFLICT(x_handle) DO UPDATE SET
                    name=excluded.name,
                    description=excluded.description
            """, (t["handle"], t["name"], t["description"]))


def get_trader_id(handle: str) -> int | None:
    with get_conn() as conn:
        row = conn.execute("SELECT id FROM traders WHERE x_handle=?", (handle,)).fetchone()
        return row["id"] if row else None


def add_post(trader_id: int, text: str, url: str, posted_at: str) -> int:
    with get_conn() as conn:
        cur = conn.execute(
            "INSERT INTO posts (trader_id, post_text, post_url, posted_at) VALUES (?,?,?,?)",
            (trader_id, text, url, posted_at)
        )
        return cur.lastrowid


def add_mention(post_id: int, ticker: str, stance: str, reasoning: str, confidence: float,
                sentiment_score: int = 0, key_phrases: str = ""):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO mentions
               (post_id, ticker, stance, reasoning, confidence, sentiment_score, key_phrases)
               VALUES (?,?,?,?,?,?,?)""",
            (post_id, ticker.upper(), stance, reasoning, confidence, sentiment_score, key_phrases)
        )


def _period_cutoff(period: str) -> str:
    days = {"daily": 1, "weekly": 7, "monthly": 28}.get(period, 1)
    return (datetime.utcnow() - timedelta(days=days)).isoformat(sep=" ")


def get_dashboard_data(period: str = "daily") -> list[dict]:
    cutoff = _period_cutoff(period)
    sql = """
        SELECT
            m.ticker,
            COUNT(*)                                         AS total,
            SUM(m.stance = 'bullish')                        AS bullish,
            SUM(m.stance = 'bearish')                        AS bearish,
            SUM(m.stance = 'neutral')                        AS neutral,
            AVG(m.sentiment_score)                           AS avg_score,
            MAX(p.posted_at)                                 AS last_update,
            GROUP_CONCAT(DISTINCT t.x_handle)               AS handles
        FROM mentions m
        JOIN posts    p ON p.id = m.post_id
        JOIN traders  t ON t.id = p.trader_id
        WHERE p.posted_at >= ?
          AND m.ticker != ''
          AND m.ticker != 'N/A'
        GROUP BY m.ticker
        ORDER BY total DESC
    """
    with get_conn() as conn:
        rows = conn.execute(sql, (cutoff,)).fetchall()

    result = []
    for r in rows:
        b, be, n = (r["bullish"] or 0), (r["bearish"] or 0), (r["neutral"] or 0)
        total = b + be + n
        avg_score = round(r["avg_score"] or 0)
        if b > be and b > n:
            consensus = "bullish"
        elif be > b and be > n:
            consensus = "bearish"
        elif b == be and b > 0:
            consensus = "contested"
        else:
            consensus = "neutral"
        result.append({
            "ticker":      r["ticker"],
            "total":       total,
            "bullish":     b,
            "bearish":     be,
            "neutral":     n,
            "avg_score":   avg_score,
            "consensus":   consensus,
            "last_update": r["last_update"],
            "handles":     (r["handles"] or "").split(","),
        })
    return result


def get_stock_detail(ticker: str) -> dict:
    ticker = ticker.upper()
    sql_latest = """
        SELECT
            t.x_handle, t.name,
            m.stance, m.reasoning, m.confidence,
            m.sentiment_score, m.key_phrases,
            p.posted_at, p.post_url, p.post_text
        FROM mentions m
        JOIN posts   p ON p.id = m.post_id
        JOIN traders t ON t.id = p.trader_id
        WHERE m.ticker = ?
        ORDER BY p.posted_at DESC
    """
    sql_history = """
        SELECT m.stance, m.sentiment_score, p.posted_at, t.x_handle
        FROM mentions m
        JOIN posts   p ON p.id = m.post_id
        JOIN traders t ON t.id = p.trader_id
        WHERE m.ticker = ?
        ORDER BY p.posted_at DESC
        LIMIT 100
    """
    with get_conn() as conn:
        all_rows = [dict(r) for r in conn.execute(sql_latest, (ticker,)).fetchall()]
        history  = [dict(r) for r in conn.execute(sql_history, (ticker,)).fetchall()]

    seen_handles = set()
    latest_per_trader = []
    for r in all_rows:
        if r["x_handle"] not in seen_handles:
            seen_handles.add(r["x_handle"])
            latest_per_trader.append(r)

    counts = {"bullish": 0, "bearish": 0, "neutral": 0}
    for r in latest_per_trader:
        counts[r["stance"]] += 1

    return {"ticker": ticker, "latest": latest_per_trader, "history": history, "counts": counts}


def get_trader_detail(handle: str) -> dict:
    sql = """
        SELECT
            m.ticker, m.stance, m.reasoning, m.confidence,
            m.sentiment_score, m.key_phrases,
            p.posted_at, p.post_url, p.post_text
        FROM mentions m
        JOIN posts   p ON p.id = m.post_id
        JOIN traders t ON t.id = p.trader_id
        WHERE t.x_handle = ?
        ORDER BY p.posted_at DESC
    """
    sql_trader = "SELECT * FROM traders WHERE x_handle=?"
    # Allocation: % of total mentions per ticker
    sql_alloc = """
        SELECT m.ticker,
               COUNT(*) AS cnt,
               AVG(m.sentiment_score) AS avg_score,
               SUM(m.stance='bullish') AS bullish,
               SUM(m.stance='bearish') AS bearish
        FROM mentions m
        JOIN posts   p ON p.id = m.post_id
        JOIN traders t ON t.id = p.trader_id
        WHERE t.x_handle = ?
        GROUP BY m.ticker
        ORDER BY cnt DESC
    """
    with get_conn() as conn:
        mentions  = [dict(r) for r in conn.execute(sql, (handle,)).fetchall()]
        trader    = dict(conn.execute(sql_trader, (handle,)).fetchone() or {})
        alloc_raw = [dict(r) for r in conn.execute(sql_alloc, (handle,)).fetchall()]

    total_mentions = sum(r["cnt"] for r in alloc_raw) or 1
    allocation = []
    for r in alloc_raw:
        pct = round(r["cnt"] / total_mentions * 100, 1)
        allocation.append({
            "ticker":    r["ticker"],
            "pct":       pct,
            "cnt":       r["cnt"],
            "avg_score": round(r["avg_score"] or 0),
            "bullish":   r["bullish"] or 0,
            "bearish":   r["bearish"] or 0,
        })

    return {"trader": trader, "mentions": mentions, "allocation": allocation}


def get_all_traders() -> list[dict]:
    with get_conn() as conn:
        return [dict(r) for r in conn.execute("SELECT * FROM traders ORDER BY name").fetchall()]


def get_qa_context(query: str) -> str:
    import re
    tickers = re.findall(r'\$([A-Z]{1,5})', query.upper())
    handles = re.findall(r'@(\w+)', query)

    conditions, params = [], []
    if tickers:
        placeholders = ",".join("?" * len(tickers))
        conditions.append(f"m.ticker IN ({placeholders})")
        params.extend(tickers)
    if handles:
        placeholders = ",".join("?" * len(handles))
        conditions.append(f"t.x_handle IN ({placeholders})")
        params.extend(handles)

    where = ("WHERE " + " OR ".join(conditions)) if conditions else ""
    sql = f"""
        SELECT t.x_handle, t.name, m.ticker, m.stance, m.sentiment_score,
               m.reasoning, m.key_phrases, p.posted_at, p.post_url
        FROM mentions m
        JOIN posts   p ON p.id = m.post_id
        JOIN traders t ON t.id = p.trader_id
        {where}
        ORDER BY p.posted_at DESC
        LIMIT 40
    """
    with get_conn() as conn:
        rows = [dict(r) for r in conn.execute(sql, params).fetchall()]

    if not rows:
        with get_conn() as conn:
            rows = [dict(r) for r in conn.execute("""
                SELECT t.x_handle, t.name, m.ticker, m.stance, m.sentiment_score,
                       m.reasoning, m.key_phrases, p.posted_at
                FROM mentions m JOIN posts p ON p.id=m.post_id JOIN traders t ON t.id=p.trader_id
                ORDER BY p.posted_at DESC LIMIT 40
            """).fetchall()]

    lines = []
    for r in rows:
        phrases = f" | Key phrases: {r['key_phrases']}" if r.get("key_phrases") else ""
        lines.append(
            f"[@{r['x_handle']}] {r['ticker']} — {r['stance'].upper()} "
            f"(score: {r.get('sentiment_score',0):+d}): {r['reasoning'] or ''}{phrases} ({r['posted_at']})"
        )
    return "\n".join(lines)


# ── Portfolio snapshot (pushed from local PC via sync script) ─────────────────

def init_portfolio_snapshot_table():
    with get_conn() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS portfolio_snapshot (
                id          INTEGER PRIMARY KEY,
                positions   TEXT NOT NULL,
                account     TEXT NOT NULL,
                synced_at   TEXT NOT NULL
            )
        """)


def save_portfolio_snapshot(positions: list, account: dict):
    with get_conn() as conn:
        conn.execute("DELETE FROM portfolio_snapshot")
        conn.execute(
            "INSERT INTO portfolio_snapshot (positions, account, synced_at) VALUES (?,?,?)",
            (json.dumps(positions), json.dumps(account), datetime.utcnow().isoformat(sep=" "))
        )


def get_portfolio_snapshot() -> dict | None:
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT * FROM portfolio_snapshot ORDER BY id DESC LIMIT 1").fetchone()
        if not row:
            return None
        return {
            "positions": json.loads(row["positions"]),
            "account":   json.loads(row["account"]),
            "synced_at": row["synced_at"],
        }
    except Exception:
        return None
