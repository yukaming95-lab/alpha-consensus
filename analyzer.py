import json
from openai import OpenAI
from config import DEEPSEEK_API_KEY, DEEPSEEK_BASE_URL, DEEPSEEK_MODEL

_client = None


def _get_client() -> OpenAI:
    global _client
    if _client is None:
        _client = OpenAI(api_key=DEEPSEEK_API_KEY, base_url=DEEPSEEK_BASE_URL)
    return _client


SYSTEM_PROMPT = """You are a financial sentiment classifier for stock market posts from X (Twitter).

Given a post text and the author's handle, extract every stock ticker mentioned and classify the author's stance.

Return ONLY a JSON array. Each element:
{
  "ticker": "AAPL",
  "stance": "bullish",
  "sentiment_score": 72,
  "key_phrases": ["strong demand", "pricing power", "positive signal for margins"],
  "reasoning": "Author highlights surging demand and pricing power as revenue tailwinds.",
  "confidence": 0.88
}

Field rules:
- ticker: uppercase, no $ prefix. Only include if a clear directional view exists.
- stance: exactly one of "bullish", "bearish", "neutral"
- sentiment_score: integer from -100 (extreme bearish) to +100 (extreme bullish).
  Use this scale:
    +80 to +100 = Strongly bullish (explicit buy, high conviction)
    +40 to +79  = Bullish (positive language, constructive view)
    +10 to +39  = Mildly bullish (slight lean positive)
    -10 to +9   = Neutral (informational, no directional bias)
    -39 to -11  = Mildly bearish
    -79 to -40  = Bearish
    -100 to -80 = Strongly bearish (explicit short, high conviction)
- key_phrases: list of 2-5 EXACT short phrases copied from the post that most justify the stance.
  These must be real substrings from the original text — do not paraphrase.
- reasoning: 1-2 sentence explanation of the classification
- confidence: 0.0–1.0

Additional rules:
- If no ticker is clearly mentioned with a directional view, return [].
- Sarcasm or irony: flag as neutral with low confidence.
- Multiple tickers in one post: one entry per ticker with independent scores.
- Do NOT include crypto unless it is explicitly discussed as a stock proxy (e.g., COIN, MSTR).
- Return raw JSON array only — no markdown, no explanation outside the array."""


def analyze_post(post_text: str, trader_handle: str) -> list[dict]:
    if not DEEPSEEK_API_KEY:
        return []

    user_msg = f"Author: @{trader_handle}\n\nPost:\n{post_text}"

    try:
        response = _get_client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.1,
            max_tokens=800,
        )
        raw = response.choices[0].message.content.strip()
        raw = raw.strip("```json").strip("```").strip()
        mentions = json.loads(raw)
        valid = []
        for m in mentions:
            if (
                isinstance(m, dict)
                and isinstance(m.get("ticker"), str)
                and m.get("stance") in ("bullish", "bearish", "neutral")
            ):
                phrases = m.get("key_phrases", [])
                if isinstance(phrases, list):
                    phrases_str = " | ".join(str(p) for p in phrases[:5])
                else:
                    phrases_str = str(phrases)

                valid.append({
                    "ticker":          m["ticker"].upper().lstrip("$"),
                    "stance":          m["stance"],
                    "sentiment_score": max(-100, min(100, int(m.get("sentiment_score", 0)))),
                    "key_phrases":     phrases_str,
                    "reasoning":       str(m.get("reasoning", ""))[:500],
                    "confidence":      float(m.get("confidence", 0.8)),
                })
        return valid
    except Exception as e:
        print(f"[analyzer] error: {e}")
        return []


def answer_question(question: str, context: str, signal: dict = None) -> str:
    if not DEEPSEEK_API_KEY:
        return "API key not configured."

    system = """You are Alpha Consensus, an AI assistant that summarizes market sentiment from tracked X traders.
Answer the user's question using the provided context data.
Be concise. Cite trader handles (@handle) when referencing specific views.
When available, reference key phrases from original posts to support your answer.
If signal data is provided, weave in the consensus view alongside the technical picture.
If the data is insufficient, say so clearly. Never invent data."""

    signal_block = ""
    if signal:
        def _fmt(tf: dict, label: str) -> str:
            if not tf or not isinstance(tf, dict):
                return ""
            return (
                f"\n{label}:\n"
                f"  Buy Zone : ${tf.get('buy_zone_low', 0):.2f} – ${tf.get('buy_zone_high', 0):.2f}\n"
                f"  Target 1 : ${tf.get('take_profit_1', 0):.2f}\n"
                f"  Target 2 : ${tf.get('take_profit_2', 0):.2f}\n"
                f"  Stop Loss: ${tf.get('stop_loss', 0):.2f}\n"
                f"  R:R      : {tf.get('risk_reward', '—')}\n"
                f"  Note     : {tf.get('entry_note', '')}"
            )
        signal_block = f"""

--- TECHNICAL SIGNAL DATA FOR ${signal['ticker']} ---
Price  : ${signal['price']:.2f}
RSI(14): {signal.get('rsi', '—')}  |  MACD hist: {signal.get('macd_hist', '—')}
Support   : {signal.get('support', [])}
Resistance: {signal.get('resistance', [])}
{_fmt(signal.get('short_term'), 'Short-Term (3–10 days)')}
{_fmt(signal.get('swing_trade'), 'Swing Trade (1–4 weeks)')}
{_fmt(signal.get('position_trade'), 'Position Trade (1–3 months)')}
Technical summary: {signal.get('technical_summary', '')}
Chart pattern: {signal.get('chart_pattern', '')}
Confidence: {signal.get('confidence', '')}"""

    user_msg = f"Context (recent trader stances):\n{context}{signal_block}\n\nQuestion: {question}"

    try:
        response = _get_client().chat.completions.create(
            model=DEEPSEEK_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user",   "content": user_msg},
            ],
            temperature=0.3,
            max_tokens=800,
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        return f"Error: {e}"
