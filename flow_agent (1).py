"""
The Morning Wire v3
-------------------
A daily, phone-readable markets brief for the assets you actually hold, built to
help you (a) read flows and (b) think about positioning.

Coverage
  Crypto:   BTC, ETH  -> a simple UP/DOWN flow read for basis + options, plus
                          funding, annualized basis, ETF net flow, open interest,
                          and a plain-language "what this means for your book".
  Equities: KO, BRK.B, PLTR, CRCL -> THREE charts each (1W / 1M / YTD), what moved
                          the stock and the mechanism, long-term outlook, what large
                          holders are positioning into, and options strategies a
                          long-term holder uses to harvest upside (educational, not
                          advice), with bull/bear reads.
  FX:       USD/PEN   -> rate, 3 charts, corridor context.

Output
  docs/index.html                 -> today's brief (served by GitHub Pages)
  docs/archive/YYYY-MM-DD.html    -> frozen daily snapshot (charts baked in)
  docs/archive/index.html         -> scrollable history with each day's headline
  data/history.json               -> price history + rolling headlines
  Optional email if GMAIL_* / MAIL_TO secrets exist.

Honest data model: intra-exchange crypto trades and most equity order flow are not
publicly attributable. Every causal claim is confidence-tagged. Not financial advice.

Secret required: ANTHROPIC_API_KEY
"""

import json
import math
import os
import re
import smtplib
import sys
import time
from datetime import datetime, timezone, date
from email.mime.text import MIMEText

import requests

ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY", "")
MODEL = "claude-sonnet-4-6"

# ticker -> (display name, yahoo symbol, stooq symbol)
EQUITIES = [
    ("KO",    "Coca-Cola",       "KO",    "ko.us"),
    ("BRK.B", "Berkshire H. B",  "BRK-B", "brk-b.us"),
    ("PLTR",  "Palantir",        "PLTR",  "pltr.us"),
    ("CRCL",  "Circle",          "CRCL",  "crcl.us"),
]
FX = ("USD/PEN", "USDPEN=X", "usdpen")
CRYPTO = [("BTC", "bitcoin", "BTC-USD"), ("ETH", "ethereum", "ETH-USD")]

# Actual position sizes (shares / coins). Update here when you rebalance.
HOLDINGS = {"PLTR": 184, "BRK.B": 24, "KO": 56.03, "CRCL": 34, "BTC": 0.15, "ETH": 0.36}
ASSET_COLORS = {"PLTR": "#143642", "BRK.B": "#0E7C57", "KO": "#C23B2E",
                "CRCL": "#8E44AD", "BTC": "#F7931A", "ETH": "#627EEA"}

UA = {"User-Agent": "Mozilla/5.0 (Morning Wire personal digest)"}
HISTORY_PATH = "data/history.json"


# ===========================================================================
# PRICE / CHANGE SNAPSHOTS
# ===========================================================================

def get_crypto_spot():
    url = ("https://api.coingecko.com/api/v3/simple/price"
           "?ids=bitcoin,ethereum&vs_currencies=usd"
           "&include_24hr_change=true&include_24hr_vol=true")
    try:
        d = requests.get(url, timeout=15).json()
        return {
            "BTC": {"price": d["bitcoin"]["usd"], "change": round(d["bitcoin"]["usd_24h_change"], 2)},
            "ETH": {"price": d["ethereum"]["usd"], "change": round(d["ethereum"]["usd_24h_change"], 2)},
        }
    except Exception as e:
        print(f"[warn] coingecko spot failed: {e}")
        return {}


def get_fear_greed():
    try:
        d = requests.get("https://api.alternative.me/fng/?limit=2", timeout=15).json()["data"]
        return {"today": f'{d[0]["value"]} · {d[0]["value_classification"]}',
                "prev": f'{d[1]["value"]} · {d[1]["value_classification"]}'}
    except Exception as e:
        print(f"[warn] fng failed: {e}")
        return None


# ===========================================================================
# CHART SERIES  (1W / 1M / YTD)  — returns {"1W":[(t,p)...], "1M":[...], "YTD":[...]}
# ===========================================================================

def _downsample(points, target=140):
    if len(points) <= target:
        return points
    step = len(points) / target
    return [points[int(i * step)] for i in range(target)]


def yahoo_series(symbol, rng, interval):
    url = (f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
           f"?range={rng}&interval={interval}")
    r = requests.get(url, headers=UA, timeout=20)
    r.raise_for_status()
    res = r.json()["chart"]["result"][0]
    ts = res["timestamp"]
    close = res["indicators"]["quote"][0]["close"]
    return [(t, c) for t, c in zip(ts, close) if c is not None]


def stooq_daily(symbol):
    """Full daily history CSV -> [(epoch, close)]."""
    r = requests.get(f"https://stooq.com/q/d/l/?s={symbol}&i=d", headers=UA, timeout=20)
    r.raise_for_status()
    rows = r.text.strip().split("\n")[1:]
    out = []
    for row in rows:
        c = row.split(",")
        if len(c) >= 5 and c[4] not in ("", "N/D"):
            try:
                epoch = int(time.mktime(time.strptime(c[0], "%Y-%m-%d")))
                out.append((epoch, float(c[4])))
            except Exception:
                pass
    return out


def windows_from_daily(daily):
    """Slice a daily [(epoch,price)] series into 1W/1M/YTD."""
    if not daily:
        return {"1W": [], "1M": [], "YTD": []}
    jan1 = int(time.mktime(date(datetime.now(timezone.utc).year, 1, 1).timetuple()))
    return {
        "1W": daily[-6:],
        "1M": daily[-22:],
        "YTD": _downsample([p for p in daily if p[0] >= jan1]),
    }


def get_equity_charts(yahoo_sym, stooq_sym):
    """1W intraday + 1M daily + YTD daily, Yahoo primary, Stooq fallback."""
    out = {}
    try:
        out["1W"] = _downsample(yahoo_series(yahoo_sym, "5d", "30m"))
        out["1M"] = yahoo_series(yahoo_sym, "1mo", "1d")
        out["YTD"] = _downsample(yahoo_series(yahoo_sym, "ytd", "1d"))
        if out["1W"] and out["YTD"]:
            return out
    except Exception as e:
        print(f"[warn] yahoo charts failed for {yahoo_sym}: {e}")
    try:
        return windows_from_daily(stooq_daily(stooq_sym))
    except Exception as e:
        print(f"[warn] stooq fallback failed for {stooq_sym}: {e}")
        return {"1W": [], "1M": [], "YTD": []}


def coingecko_range(coin_id, days):
    url = (f"https://api.coingecko.com/api/v3/coins/{coin_id}/market_chart"
           f"?vs_currency=usd&days={days}")
    d = requests.get(url, headers=UA, timeout=20).json()["prices"]
    return [(int(ms / 1000), p) for ms, p in d]


def coinbase_daily(product):
    url = f"https://api.exchange.coinbase.com/products/{product}/candles?granularity=86400"
    d = requests.get(url, headers=UA, timeout=20).json()  # [[t,l,h,o,c,v]], newest first
    return sorted([(row[0], row[4]) for row in d])


def get_crypto_charts(coin_id, product):
    ytd_days = (datetime.now(timezone.utc).date() - date(datetime.now(timezone.utc).year, 1, 1)).days or 1
    try:
        return {
            "1W": _downsample(coingecko_range(coin_id, 7)),
            "1M": _downsample(coingecko_range(coin_id, 30)),
            "YTD": _downsample(coingecko_range(coin_id, ytd_days)),
        }
    except Exception as e:
        print(f"[warn] coingecko charts failed for {coin_id}: {e}")
    try:
        return windows_from_daily(coinbase_daily(product))
    except Exception as e:
        print(f"[warn] coinbase fallback failed for {product}: {e}")
        return {"1W": [], "1M": [], "YTD": []}


def get_fx_charts():
    try:
        out = {
            "1W": _downsample(yahoo_series(FX[1], "5d", "30m")),
            "1M": yahoo_series(FX[1], "1mo", "1d"),
            "YTD": _downsample(yahoo_series(FX[1], "ytd", "1d")),
        }
        if out["YTD"]:
            return out
    except Exception as e:
        print(f"[warn] yahoo fx failed: {e}")
    try:
        return windows_from_daily(stooq_daily(FX[2]))
    except Exception as e:
        print(f"[warn] stooq fx failed: {e}")
        return {"1W": [], "1M": [], "YTD": []}


def window_change(points):
    if len(points) < 2:
        return None
    a, b = points[0][1], points[-1][1]
    return round((b / a - 1) * 100, 2) if a else None


def last_price(points):
    return points[-1][1] if points else None


# ===========================================================================
# CLAUDE RESEARCH + SYNTHESIS
# ===========================================================================

def build_prompt(snap, today):
    return f"""You are the markets analyst writing "The Morning Wire" for ONE reader:
a fintech product manager in Miami who actively runs BTC/ETH basis trades and options
positions on US-accessible venues (Coinbase Advanced, Kraken, Interactive Brokers /
tastytrade CME micro futures — offshore venues like Binance/Bybit are data only), and
holds KO, BRK.B, PLTR, CRCL as long-term equity positions, with real exposure to the
USD->PEN (Peru) remittance corridor. He is smart, moves fast, and has explicitly said
prior briefs were TOO THIN. Give him real depth and real numbers. Today is {today} UTC.

HARD DATA already fetched (ground truth; don't contradict, don't re-fetch prices):
{json.dumps(snap, indent=2, default=str)}

RESEARCH with web search (last ~24-48h). Be specific and quantitative:

CRYPTO — for BTC and ETH, find the positioning data a basis/options trader needs:
  • Perp funding rate (current, and annualized) — Coinglass/Velo. Positive = longs pay
    shorts (crowded longs); negative = shorts pay.
  • Annualized basis (CME and/or perp cash-and-carry) — the actual carry a long-spot /
    short-futures trade earns right now.
  • Spot BTC & ETH ETF net flow yesterday (Farside daily totals), $M, with sign.
  • Futures open interest 24h change (rising OI + rising price = new longs; rising OI +
    falling price = new shorts; falling OI = unwind/deleveraging).
  • 24h long vs short liquidations.
From those, set a single DIRECTION for each coin: "inflows / longs building",
"outflows / longs unwinding", "shorts building", or "mixed / rangebound".

EQUITIES — for EACH of KO, BRK.B, PLTR, CRCL, research: latest earnings result and
reaction, analyst rating/price-target changes, unusual options activity, insider Form-4
buying/selling, notable 13F / large-holder moves, short interest, and sector context.

FX — USD/PEN: BCRP (Peru central bank) actions, copper prices, Peru political news.

CONFIDENCE TAGS on every causal claim, inline: [STRONG] quantified direct link ·
[LIKELY] plausible, timing fits · [SPECULATIVE] circulating narrative, weak evidence.
NEVER invent a number — if not found, write "not found". Options content is EDUCATIONAL
(explain how the strategy works and its tradeoffs for a long-term holder harvesting
upside) — NOT a recommendation; do not tell him to place a specific trade.

Respond with ONLY a JSON object (no markdown fences), exactly this shape. Respect the
sentence counts — he wants depth, so hit the HIGH end:

{{
  "market_read": "one punchy line for the masthead — the day's cross-asset story",
  "lede": "4-5 sentences: the single most important thing across his book today and why it matters to him specifically",
  "portfolio": "5-7 sentences: an insightful read on how his whole book is positioned and trending, using the portfolio weights/returns in the hard data. What's carrying it today and YTD, concentration vs diversification (call out any position that dominates), how the crypto sleeve is behaving vs the equity sleeve, and the main forward risk. He has compounded ~60% annualized for 3 years — respect that, be substantive and specific, not basic. Tag causal claims.",
  "crypto": {{
    "BTC": {{
      "direction": "inflows / longs building | outflows / longs unwinding | shorts building | mixed / rangebound",
      "funding": "current funding + annualized, one line with a number",
      "basis": "annualized basis / carry, one line with a number",
      "etf_net": "yesterday's spot ETF net flow $M with sign",
      "oi": "open interest 24h direction + what it implies",
      "liquidations": "24h long vs short liquidations",
      "book_read": "5-7 sentences: where money is going and WHY and HOW, and what it means concretely for (a) his cash-and-carry basis trade and (b) long vs short options positioning. Reference the venues he can actually use. Tag claims."
    }},
    "ETH": {{ "direction": "...", "funding": "...", "basis": "...", "etf_net": "...", "oi": "...", "liquidations": "...", "book_read": "..." }}
  }},
  "stocks": {{
    "KO":    {{ "moved": "4-6 sentences: what moved it in 24h and the MECHANISM (why/how), tagged", "outlook": "4-5 sentences: 6-18 month outlook, drivers, risks", "positioning": "3-5 sentences: what large holders / insiders / options flow suggest big players are doing, tagged", "options": "4-6 sentences: strategies a long-term holder uses to harvest upside on THIS name (e.g. covered calls, cash-secured puts, collars, LEAPS / poor-man's covered call) with concrete tradeoffs and what current IV/vol makes attractive — educational, not advice", "bull": "1-2 sentences", "bear": "1-2 sentences" }},
    "BRK.B": {{ "moved": "...", "outlook": "...", "positioning": "...", "options": "...", "bull": "...", "bear": "..." }},
    "PLTR":  {{ "moved": "...", "outlook": "...", "positioning": "...", "options": "...", "bull": "...", "bear": "..." }},
    "CRCL":  {{ "moved": "...", "outlook": "...", "positioning": "...", "options": "...", "bull": "...", "bear": "..." }}
  }},
  "fx": {{ "moved": "3-4 sentences on USD/PEN drivers, tagged", "outlook": "2-3 sentences + what it means for his USD->PEN corridor economics" }},
  "lesson": "4-6 sentences: one genuinely useful flow-reading or positioning lesson drawn from TODAY's tape",
  "watch": ["4-5 detailed bullets: dated catalysts across crypto, his 4 stocks, and macro"]
}}"""


class SynthesisError(Exception):
    pass


def _extract_json(text):
    text = re.sub(r"```(?:json)?", "", text).strip()
    start = text.find("{")
    if start == -1:
        raise SynthesisError(f"no JSON object in model response: {text[:300]}")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start:i + 1]
    raise SynthesisError("unbalanced braces in model response")


def _call_api(prompt, use_web_search=True):
    body = {"model": MODEL, "max_tokens": 8000,
            "messages": [{"role": "user", "content": prompt}]}
    if use_web_search:
        body["tools"] = [{"type": "web_search_20250305", "name": "web_search", "max_uses": 16}]
    r = requests.post(
        "https://api.anthropic.com/v1/messages",
        headers={"x-api-key": ANTHROPIC_API_KEY, "anthropic-version": "2023-06-01",
                 "content-type": "application/json"},
        json=body, timeout=600)
    if r.status_code != 200:
        print(f"[api-error] HTTP {r.status_code}: {r.text[:800]}")
    r.raise_for_status()
    return r.json()


def run_claude(prompt):
    try:
        data = _call_api(prompt, use_web_search=True)
    except requests.HTTPError as e:
        body = getattr(e.response, "text", "") or ""
        if e.response is not None and e.response.status_code == 400 and "web_search" in body:
            print("[warn] web search rejected — retrying once WITHOUT live search.")
            data = _call_api(prompt + "\n\n(NOTE: web search is unavailable this run; do "
                             "NOT invent numbers — write 'not found' and keep it qualitative.)",
                             use_web_search=False)
        else:
            raise  # genuine API/config error: stay loud so it gets fixed
    text = "\n".join(b.get("text", "") for b in data.get("content", [])
                     if b.get("type") == "text").strip()
    try:
        return json.loads(_extract_json(text))
    except (json.JSONDecodeError, SynthesisError) as e:
        print(f"[error] could not parse model JSON: {e}\n--- first 900 chars of response ---\n{text[:900]}")
        raise SynthesisError(str(e))


def fallback_digest(reason):
    """Minimal digest so the site still renders (with prices/charts) if synthesis fails."""
    return {"_error": reason,
            "market_read": "Live prices below — written analysis unavailable this run.",
            "lede": "Today's market data and your portfolio rendered fine, but the written "
                    "synthesis could not be generated. The reason is shown in the banner above; "
                    "the numbers, charts, and allocations below are still live.",
            "portfolio": "", "crypto": {"BTC": {}, "ETH": {}}, "stocks": {},
            "fx": {}, "lesson": "", "watch": []}


# ===========================================================================
# RENDER
# ===========================================================================

def esc(s):
    return str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def tags(s):
    s = esc(s)
    for t, cls in [("STRONG", "s"), ("LIKELY", "l"), ("SPECULATIVE", "p")]:
        s = s.replace(f"[{t}]", f'<span class="tag {cls}">{t}</span>')
    return s


def fmt(p):
    if p is None:
        return "—"
    if p >= 100:
        return f"{p:,.2f}"
    if p >= 1:
        return f"{p:,.3f}" if p < 10 else f"{p:,.2f}"
    return f"{p:,.4f}"


def chart_svg(points, label):
    """Editorial line+area mini chart with a % badge. Responsive via viewBox."""
    chg = window_change(points)
    if len(points) < 2:
        return (f'<figure class="ch"><figcaption><b>{label}</b>'
                f'<span class="muted">no data</span></figcaption>'
                f'<div class="ch-empty">unavailable</div></figure>')
    W, H, PAD = 300, 96, 6
    ys = [p[1] for p in points]
    lo, hi = min(ys), max(ys)
    rng = (hi - lo) or 1
    n = len(points)
    xs = [PAD + i * (W - 2 * PAD) / (n - 1) for i in range(n)]
    yc = [H - PAD - (v - lo) / rng * (H - 2 * PAD) for v in ys]
    line = " ".join(f"{x:.1f},{y:.1f}" for x, y in zip(xs, yc))
    area = f"{xs[0]:.1f},{H - PAD} " + line + f" {xs[-1]:.1f},{H - PAD}"
    up = chg is not None and chg >= 0
    col = "var(--up)" if up else "var(--down)"
    fill = "var(--up-fill)" if up else "var(--down-fill)"
    sign = "+" if (chg is not None and chg >= 0) else ""
    badge = f'<span class="chg {"up" if up else "down"}">{sign}{chg}%</span>' if chg is not None else ""
    return (f'<figure class="ch"><figcaption><b>{label}</b>{badge}</figcaption>'
            f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" class="ch-svg">'
            f'<polygon points="{area}" fill="{fill}"/>'
            f'<polyline points="{line}" fill="none" stroke="{col}" stroke-width="1.8" '
            f'stroke-linejoin="round" stroke-linecap="round"/></svg></figure>')


def three_charts(charts):
    return ('<div class="charts">'
            + chart_svg(charts.get("1W", []), "1W")
            + chart_svg(charts.get("1M", []), "1M")
            + chart_svg(charts.get("YTD", []), "YTD")
            + '</div>')


def chg_badge(change):
    if change is None:
        return '<span class="pill flat">—</span>'
    up = change >= 0
    return f'<span class="pill {"up" if up else "down"}">{"+" if up else ""}{change}%</span>'


def dir_badge(direction):
    d = (direction or "").lower()
    if "outflow" in d or "unwind" in d or "short" in d:
        return f'<span class="dir down">▼ {esc(direction)}</span>'
    if "inflow" in d or "long" in d:
        return f'<span class="dir up">▲ {esc(direction)}</span>'
    return f'<span class="dir flat">◆ {esc(direction or "mixed")}</span>'


def tape(assets):
    cells = ""
    for name, price, change in assets:
        cls = "up" if (change is not None and change >= 0) else ("down" if change is not None else "flat")
        c = f'{"+" if (change is not None and change >= 0) else ""}{change}%' if change is not None else "—"
        cells += f'<span class="tape-i"><b>{esc(name)}</b> {fmt(price)} <em class="{cls}">{c}</em></span>'
    return f'<div class="tape">{cells}</div>'


def crypto_panel(sym, spot, cd, charts):
    return f"""<div class="asset crypto">
  <div class="asset-head"><h3>{sym}<span class="sub">/ USD</span></h3>
    <div class="ph">{fmt(spot.get('price'))} {chg_badge(spot.get('change'))}</div></div>
  <div class="flowbar">{dir_badge(cd.get('direction'))}</div>
  <div class="metrics">
    <div><span>Funding</span>{tags(cd.get('funding','—'))}</div>
    <div><span>Basis / carry</span>{tags(cd.get('basis','—'))}</div>
    <div><span>ETF net</span>{tags(cd.get('etf_net','—'))}</div>
    <div><span>Open interest</span>{tags(cd.get('oi','—'))}</div>
    <div><span>Liquidations</span>{tags(cd.get('liquidations','—'))}</div>
  </div>
  {three_charts(charts)}
  <div class="read"><h4>What this means for your book</h4><p>{tags(cd.get('book_read','—'))}</p></div>
</div>"""


def stock_panel(disp, name, spot, sd, charts):
    return f"""<div class="asset">
  <div class="asset-head"><h3>{disp}<span class="sub">{esc(name)}</span></h3>
    <div class="ph">{fmt(spot.get('price'))} {chg_badge(spot.get('change'))}</div></div>
  {three_charts(charts)}
  <div class="read">
    <h4>What moved it</h4><p>{tags(sd.get('moved','—'))}</p>
    <h4>Long-term outlook</h4><p>{tags(sd.get('outlook','—'))}</p>
    <h4>What big holders are doing</h4><p>{tags(sd.get('positioning','—'))}</p>
    <h4>Options perspectives <span class="edu">educational</span></h4><p>{tags(sd.get('options','—'))}</p>
    <div class="bb"><div class="bull"><span>BULL</span>{tags(sd.get('bull','—'))}</div>
      <div class="bear"><span>BEAR</span>{tags(sd.get('bear','—'))}</div></div>
  </div>
</div>"""


def money(v):
    if v is None:
        return "—"
    return f"${v:,.0f}" if abs(v) >= 100 else f"${v:,.2f}"


def rebase100(series):
    if len(series) < 2 or not series[0][1]:
        return []
    base = series[0][1]
    return [(t, p / base * 100) for t, p in series]


def portfolio_daily(ytd_by_asset, holdings):
    """Align each holding's YTD daily prices by date, forward/back-fill, sum shares*price."""
    maps, all_dates = {}, set()
    for a in holdings:
        m = {}
        for t, p in ytd_by_asset.get(a, []):
            ds = datetime.fromtimestamp(t, timezone.utc).strftime("%Y-%m-%d")
            m[ds] = p
            all_dates.add(ds)
        maps[a] = m
    if not all_dates:
        return []
    last = {a: (min(maps[a].items())[1] if maps[a] else None) for a in holdings}
    out = []
    for ds in sorted(all_dates):
        total, ok = 0.0, False
        for a, sh in holdings.items():
            if ds in maps[a]:
                last[a] = maps[a][ds]
            if last[a] is not None:
                total += sh * last[a]
                ok = True
        if ok:
            epoch = int(datetime.strptime(ds, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
            out.append((epoch, round(total, 2)))
    return out


def donut_svg(items):
    total = sum(v for _, v in items) or 1
    C = 2 * math.pi * 42
    acc, segs = 0.0, ""
    for label, v in sorted(items, key=lambda x: -x[1]):
        seg = (v / total) * C
        segs += (f'<circle cx="60" cy="60" r="42" fill="none" '
                 f'stroke="{ASSET_COLORS.get(label, "#999")}" stroke-width="16" '
                 f'stroke-dasharray="{seg:.2f} {C - seg:.2f}" stroke-dashoffset="{-acc:.2f}" '
                 f'transform="rotate(-90 60 60)"/>')
        acc += seg
    return (f'<svg viewBox="0 0 120 120" class="donut">{segs}'
            f'<circle cx="60" cy="60" r="33" fill="var(--card)"/></svg>')


def alloc_legend(items):
    total = sum(v for _, v in items) or 1
    rows = ""
    for label, v in sorted(items, key=lambda x: -x[1]):
        rows += (f'<div class="lg"><span class="dot" style="background:'
                 f'{ASSET_COLORS.get(label, "#999")}"></span><b>{esc(label)}</b>'
                 f'<span class="lg-v">{money(v)}</span>'
                 f'<span class="lg-p">{v / total * 100:.1f}%</span></div>')
    return rows


def overlay_svg(series_by_asset):
    active = {a: s for a, s in series_by_asset.items() if len(s) >= 2}
    if not active:
        return '<div class="ch-empty">unavailable</div>'
    allv = [p for s in active.values() for _, p in s]
    lo, hi = min(allv), max(allv)
    rng = (hi - lo) or 1
    W, H, PAD = 300, 120, 8
    base_y = H - PAD - (100 - lo) / rng * (H - 2 * PAD)
    svg = (f'<line x1="{PAD}" y1="{base_y:.1f}" x2="{W - PAD}" y2="{base_y:.1f}" '
           f'stroke="var(--line)" stroke-dasharray="3 3"/>')
    for a, s in active.items():
        n = len(s)
        pts = " ".join(f"{PAD + i * (W - 2 * PAD) / (n - 1):.1f},"
                       f"{H - PAD - (p - lo) / rng * (H - 2 * PAD):.1f}"
                       for i, (_, p) in enumerate(s))
        svg += (f'<polyline points="{pts}" fill="none" '
                f'stroke="{ASSET_COLORS.get(a, "#999")}" stroke-width="1.6" '
                f'stroke-linejoin="round" opacity="0.92"/>')
    return f'<svg viewBox="0 0 {W} {H}" preserveAspectRatio="none" class="ov-svg">{svg}</svg>'


def overlay_legend(series_by_asset):
    out = ""
    for a, s in series_by_asset.items():
        if len(s) >= 2 and s[0][1]:
            perf = (s[-1][1] / s[0][1] - 1) * 100
            cls = "up" if perf >= 0 else "down"
            out += (f'<span class="od"><i style="background:{ASSET_COLORS.get(a, "#999")}"></i>'
                    f'{esc(a)} <em class="{cls}">{"+" if perf >= 0 else ""}{perf:.0f}%</em></span>')
    return out


def portfolio_section(port, read_text):
    total = port["total"]
    tc = port["today_pct"]
    up = tc is not None and tc >= 0
    chg = (f'<div class="pv-chg {"up" if up else "down"}">'
           f'{"▲" if up else "▼"} {"+" if up else ""}{tc}%<span>today</span></div>'
           if tc is not None else "")
    return f"""<h2 class="sec">Your portfolio</h2>
<div class="port">
  <div class="port-top"><div><div class="pv">{money(total)}</div>
    <div class="pv-sub">total value · {len(HOLDINGS)} positions</div></div>{chg}</div>
  <div class="charts">{three_charts(port['value_charts'])}</div>
  <div class="alloc"><div class="donut-wrap">{donut_svg(port['items'])}</div>
    <div class="legend">{alloc_legend(port['items'])}</div></div>
  <h4 class="ov-title">Holdings rebased to 100 · YTD</h4>
  <div class="overlay">{overlay_svg(port['overlay'])}
    <div class="ov-legend">{overlay_legend(port['overlay'])}</div></div>
  <div class="read"><h4>Portfolio read</h4><p>{tags(read_text or '—')}</p></div>
</div>"""


def render(today, snap, digest, charts_by, port):
    dg = digest
    tape_assets = ([(s, snap["crypto"].get(s, {}).get("price"), snap["crypto"].get(s, {}).get("change")) for s in ["BTC", "ETH"]]
                   + [(d, snap["equities"].get(d, {}).get("price"), snap["equities"].get(d, {}).get("change")) for d, _, _, _ in EQUITIES]
                   + [("USD/PEN", snap.get("fx", {}).get("price"), snap.get("fx", {}).get("change"))])

    crypto_html = "".join(crypto_panel(s, snap["crypto"].get(s, {}), dg.get("crypto", {}).get(s, {}), charts_by["crypto"][s]) for s in ["BTC", "ETH"])
    stocks_html = "".join(stock_panel(d, name, snap["equities"].get(d, {}), dg.get("stocks", {}).get(d.replace(".", "."), {}) or dg.get("stocks", {}).get(d, {}), charts_by["equities"][d]) for d, name, _, _ in EQUITIES)

    fx = dg.get("fx", {})
    fx_html = f"""<div class="asset">
  <div class="asset-head"><h3>USD/PEN<span class="sub">Peru corridor</span></h3>
    <div class="ph">{fmt(snap.get('fx',{}).get('price'))} {chg_badge(snap.get('fx',{}).get('change'))}</div></div>
  {three_charts(charts_by['fx'])}
  <div class="read"><h4>What moved it</h4><p>{tags(fx.get('moved','—'))}</p>
    <h4>Corridor outlook</h4><p>{tags(fx.get('outlook','—'))}</p></div></div>"""

    fng = snap.get("fng")
    fng_line = f'Fear &amp; Greed <b>{esc(fng["today"])}</b> · prev {esc(fng["prev"])}' if fng else ""
    watch = "".join(f"<li>{tags(b)}</li>" for b in dg.get("watch", []))

    return f"""<!doctype html><html lang="en"><head>
<meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>The Morning Wire · {today}</title>
<style>
:root{{
  --bg:#F4F5F3; --card:#FFFFFF; --ink:#1B1F24; --mute:#616B75; --line:#E3E5E0;
  --head:#143642; --accent:#C36B2C;
  --up:#0E7C57; --up-fill:rgba(14,124,87,.10);
  --down:#C23B2E; --down-fill:rgba(194,59,46,.10);
}}
*{{box-sizing:border-box;margin:0}}
body{{background:var(--bg);color:var(--ink);
  font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;
  -webkit-font-smoothing:antialiased;padding-bottom:56px}}
.wrap{{max-width:760px;margin:0 auto;padding:0 16px}}
.num,.pill,.chg,.tape-i em,.metrics b,.ph{{font-variant-numeric:tabular-nums}}
.mast{{background:var(--head);color:#EEF1EE;padding:22px 0 0}}
.mast .wrap{{padding-bottom:0}}
.mast h1{{font:700 26px/1.1 Georgia,"Times New Roman",serif;letter-spacing:.2px}}
.mast .date{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;opacity:.72;margin-top:4px}}
.mast .read{{font:italic 16px/1.45 Georgia,serif;margin:12px 0 4px;
  border-left:3px solid var(--accent);padding-left:12px}}
.tape{{display:flex;gap:18px;overflow-x:auto;padding:12px 16px;background:#0F2A33;
  color:#D7DED9;font-size:13px;white-space:nowrap;scrollbar-width:none}}
.tape::-webkit-scrollbar{{display:none}}
.tape-i b{{color:#fff;font-weight:600}}
.tape-i em{{font-style:normal;margin-left:3px}}
.tape .up{{color:#57C79A}} .tape .down{{color:#E88C82}} .tape .flat{{color:#93A0A5}}
.lede{{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px;margin:18px 0;font-size:17px;line-height:1.62}}
.errbar{{background:#FBEEEA;border:1px solid var(--down);color:var(--down);
  border-radius:10px;padding:12px 14px;margin:18px 0;font-size:14px;font-weight:600}}
.fng{{font-size:13px;color:var(--mute);margin:-6px 0 20px}}
h2.sec{{font:700 12px/1 -apple-system,sans-serif;letter-spacing:.16em;text-transform:uppercase;
  color:var(--head);margin:30px 0 14px;padding-bottom:8px;border-bottom:2px solid var(--head)}}
.asset{{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:18px;margin-bottom:16px}}
.asset-head{{display:flex;justify-content:space-between;align-items:flex-start;gap:12px;margin-bottom:8px}}
.asset-head h3{{font:700 20px/1.15 -apple-system,sans-serif}}
.asset-head .sub{{display:block;font:400 12px/1.2 -apple-system,sans-serif;color:var(--mute);
  text-transform:uppercase;letter-spacing:.08em;margin-top:3px}}
.ph{{font-size:19px;font-weight:600;white-space:nowrap}}
.pill{{font-size:12px;font-weight:700;padding:2px 7px;border-radius:20px;margin-left:6px}}
.pill.up{{background:var(--up-fill);color:var(--up)}} .pill.down{{background:var(--down-fill);color:var(--down)}}
.pill.flat{{background:#EEEFEC;color:var(--mute)}}
.flowbar{{margin:4px 0 12px}}
.dir{{display:inline-block;font-weight:700;font-size:14px;padding:5px 12px;border-radius:8px}}
.dir.up{{background:var(--up-fill);color:var(--up)}} .dir.down{{background:var(--down-fill);color:var(--down)}}
.dir.flat{{background:#EEEFEC;color:var(--mute)}}
.metrics{{display:grid;grid-template-columns:1fr;gap:6px;margin:10px 0 14px;
  border-top:1px solid var(--line);padding-top:12px}}
.metrics>div{{display:grid;grid-template-columns:120px 1fr;gap:10px;font-size:14px;align-items:baseline}}
.metrics span{{color:var(--mute);font-size:12px;text-transform:uppercase;letter-spacing:.06em}}
.charts{{display:grid;grid-template-columns:repeat(3,1fr);gap:10px;margin:6px 0 4px}}
.ch figcaption{{display:flex;justify-content:space-between;align-items:baseline;
  font-size:12px;color:var(--mute);margin-bottom:3px}}
.ch figcaption b{{color:var(--ink);letter-spacing:.05em}}
.ch-svg{{width:100%;height:60px;display:block}}
.ch-empty{{height:60px;display:flex;align-items:center;justify-content:center;
  font-size:11px;color:var(--mute);background:#F7F8F6;border-radius:6px}}
.chg{{font-weight:700}} .chg.up{{color:var(--up)}} .chg.down{{color:var(--down)}}
.read{{margin-top:14px}}
.read h4{{font:700 13px/1.2 -apple-system,sans-serif;letter-spacing:.04em;margin:14px 0 5px;color:var(--head)}}
.read h4:first-child{{margin-top:0}}
.read p{{font-size:15px;line-height:1.62;color:#2A2F35}}
.edu{{font-size:10px;font-weight:700;letter-spacing:.08em;color:var(--accent);
  border:1px solid var(--accent);border-radius:4px;padding:1px 5px;margin-left:6px;vertical-align:2px}}
.bb{{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:14px}}
.bb>div{{font-size:14px;line-height:1.5;padding:10px 12px;border-radius:8px}}
.bb .bull{{background:var(--up-fill)}} .bb .bear{{background:var(--down-fill)}}
.bb span{{display:block;font-size:10px;font-weight:800;letter-spacing:.1em;margin-bottom:3px}}
.bb .bull span{{color:var(--up)}} .bb .bear span{{color:var(--down)}}
.tag{{font-size:9px;font-weight:800;letter-spacing:.05em;padding:1px 5px;border-radius:3px;
  vertical-align:1px;white-space:nowrap}}
.tag.s{{background:var(--up);color:#fff}} .tag.l{{background:var(--accent);color:#fff}}
.tag.p{{background:#8A8F8A;color:#fff}}
.port{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:18px;margin-bottom:16px}}
.port-top{{display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px}}
.pv{{font-size:30px;font-weight:700;font-variant-numeric:tabular-nums;line-height:1}}
.pv-sub{{font-size:11px;color:var(--mute);text-transform:uppercase;letter-spacing:.08em;margin-top:5px}}
.pv-chg{{font-size:20px;font-weight:700;font-variant-numeric:tabular-nums;text-align:right}}
.pv-chg span{{display:block;font-size:11px;font-weight:600;color:var(--mute);text-transform:uppercase;letter-spacing:.08em}}
.pv-chg.up{{color:var(--up)}} .pv-chg.down{{color:var(--down)}}
.alloc{{display:grid;grid-template-columns:150px 1fr;gap:18px;align-items:center;
  margin:16px 0;border-top:1px solid var(--line);padding-top:16px}}
.donut{{width:150px;height:150px;display:block}}
.legend{{display:flex;flex-direction:column;gap:8px}}
.lg{{display:grid;grid-template-columns:14px 1fr auto 50px;gap:8px;align-items:center;font-size:14px}}
.lg .dot{{width:11px;height:11px;border-radius:3px}}
.lg b{{font-weight:600}}
.lg-v{{color:var(--mute);font-variant-numeric:tabular-nums;font-size:13px}}
.lg-p{{font-weight:700;font-variant-numeric:tabular-nums;text-align:right}}
.ov-title{{font:700 13px/1.2 -apple-system,sans-serif;color:var(--head);margin:18px 0 6px;letter-spacing:.03em}}
.ov-svg{{width:100%;height:118px;display:block}}
.ov-legend{{display:flex;flex-wrap:wrap;gap:14px;margin-top:9px;font-size:12px}}
.od{{display:inline-flex;align-items:center;gap:5px;color:var(--mute);font-weight:600}}
.od i{{width:9px;height:9px;border-radius:2px;display:inline-block}}
.od em{{font-style:normal;font-variant-numeric:tabular-nums}}
.od .up{{color:var(--up)}} .od .down{{color:var(--down)}}
.lesson{{background:var(--head);color:#EDF0ED;border-radius:12px;padding:18px 20px;font-size:15px;line-height:1.62}}
.lesson h2{{font:700 12px/1 sans-serif;letter-spacing:.16em;text-transform:uppercase;
  color:#9FD9C4;margin-bottom:8px}}
ul.watch{{background:var(--card);border:1px solid var(--line);border-radius:12px;
  padding:16px 18px 16px 34px}}
ul.watch li{{margin-bottom:8px;font-size:15px}}
footer{{font-size:12px;color:var(--mute);margin-top:30px;line-height:1.55}}
footer a{{color:var(--head);font-weight:600}}
@media(max-width:560px){{
  .charts{{grid-template-columns:1fr}} .ch-svg{{height:72px}}
  .bb{{grid-template-columns:1fr}} .metrics>div{{grid-template-columns:110px 1fr}}
  .alloc{{grid-template-columns:1fr;justify-items:center}} .legend{{width:100%}}
}}
</style></head><body>
<div class="mast"><div class="wrap"><h1>The Morning Wire</h1>
<div class="date">{today} · your book</div>
<div class="read">{tags(dg.get("market_read",""))}</div></div></div>
{tape(tape_assets)}
<div class="wrap">
{('<div class="errbar">⚠ Written analysis unavailable this run: ' + esc(dg["_error"]) + '</div>') if dg.get("_error") else ""}
<div class="lede">{tags(dg.get("lede",""))}</div>
<div class="fng">{fng_line}</div>

{portfolio_section(port, dg.get("portfolio", ""))}

<h2 class="sec">Crypto · flows, basis &amp; positioning</h2>
{crypto_html}

<h2 class="sec">Equities · KO · BRK.B · PLTR · CRCL</h2>
{stocks_html}

<h2 class="sec">FX · USD/PEN corridor</h2>
{fx_html}

<div class="lesson"><h2>Today's lesson</h2>{tags(dg.get("lesson",""))}</div>

<h2 class="sec">Watch next</h2>
<ul class="watch">{watch}</ul>

<footer>Attribution is inference, not proof — intra-exchange crypto trades and most
equity order flow are not publicly visible, so causal claims are tagged by confidence.
Options content is educational, not a recommendation; sizing and risk are yours.
Not financial advice. · <a href="archive/">Full archive →</a></footer>
</div></body></html>"""


# ===========================================================================
# HISTORY + ARCHIVE
# ===========================================================================

def load_history():
    if os.path.exists(HISTORY_PATH):
        with open(HISTORY_PATH) as f:
            return json.load(f)
    return {"headlines": [], "prices": {}}


def save_history(hist, today, snap, headline):
    hist.setdefault("headlines", [])
    hist["headlines"] = [h for h in hist["headlines"] if h["date"] != today]
    hist["headlines"].append({"date": today, "headline": headline})
    hist["headlines"] = hist["headlines"][-400:]
    hist.setdefault("prices", {})
    prices = {s: snap["crypto"].get(s, {}).get("price") for s in ["BTC", "ETH"]}
    prices.update({d: snap["equities"].get(d, {}).get("price") for d, _, _, _ in EQUITIES})
    prices["USD/PEN"] = snap.get("fx", {}).get("price")
    hist["prices"][today] = prices
    os.makedirs("data", exist_ok=True)
    with open(HISTORY_PATH, "w") as f:
        json.dump(hist, f, indent=1)
    return hist


def write_archive_index(hist):
    items = ""
    for h in reversed(hist.get("headlines", [])):
        items += (f'<li><a href="{h["date"]}.html"><span class="d">{h["date"]}</span>'
                  f'<span class="h">{esc(h["headline"])}</span></a></li>')
    html = f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Archive · The Morning Wire</title>
<style>body{{background:#F4F5F3;color:#1B1F24;font:16px/1.6 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;margin:0}}
.wrap{{max-width:760px;margin:0 auto;padding:24px 16px}}
h1{{font:700 24px Georgia,serif;color:#143642;margin-bottom:4px}}
.sub{{color:#616B75;font-size:14px;margin-bottom:20px}}
ul{{list-style:none;padding:0}}
li a{{display:flex;gap:14px;align-items:baseline;padding:12px 14px;background:#fff;
  border:1px solid #E3E5E0;border-radius:10px;margin-bottom:8px;text-decoration:none;color:inherit}}
li a:hover{{border-color:#143642}}
.d{{font-variant-numeric:tabular-nums;font-weight:700;color:#143642;font-size:14px;white-space:nowrap}}
.h{{color:#2A2F35;font-size:14px}}
a.back{{color:#143642;font-weight:600;text-decoration:none}}</style></head>
<body><div class="wrap"><h1>The Morning Wire — Archive</h1>
<div class="sub">Every past edition, newest first. Scroll back and reflect.</div>
<p style="margin-bottom:16px"><a class="back" href="../">← Today</a></p>
<ul>{items}</ul></div></body></html>"""
    with open("docs/archive/index.html", "w") as f:
        f.write(html)


# ===========================================================================
# EMAIL (optional)
# ===========================================================================

def maybe_email(html, today):
    addr, pwd, to = (os.environ.get("GMAIL_ADDRESS", ""),
                     os.environ.get("GMAIL_APP_PASSWORD", ""),
                     os.environ.get("MAIL_TO", ""))
    if not (addr and pwd and to):
        print("[skip] email secrets not set — dashboard only")
        return
    try:
        msg = MIMEText(html, "html")
        msg["Subject"], msg["From"], msg["To"] = f"Morning Wire · {today}", addr, to
        with smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30) as s:
            s.login(addr, pwd)
            s.send_message(msg)
        print(f"[ok] emailed {to}")
    except Exception as e:
        print(f"[warn] email failed (site still updated): {e}")


# ===========================================================================
# MAIN
# ===========================================================================

def main():
    if not ANTHROPIC_API_KEY:
        sys.exit("ERROR: ANTHROPIC_API_KEY not set")
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    print(f"=== The Morning Wire — {today} ===")

    crypto_spot = get_crypto_spot()
    charts_by = {"crypto": {}, "equities": {}, "fx": {}}
    for sym, cid, prod in CRYPTO:
        charts_by["crypto"][sym] = get_crypto_charts(cid, prod)

    equities_spot = {}
    for disp, name, ysym, ssym in EQUITIES:
        ch = get_equity_charts(ysym, ssym)
        charts_by["equities"][disp] = ch
        price = last_price(ch.get("1M") or ch.get("YTD") or [])
        equities_spot[disp] = {"price": price, "change": window_change((ch.get("1W") or [])[-2:]) if len(ch.get("1W", [])) >= 2 else window_change(ch.get("1M", [])[-2:] if len(ch.get("1M", [])) >= 2 else [])}

    charts_by["fx"] = get_fx_charts()
    fx_price = last_price(charts_by["fx"].get("1M") or charts_by["fx"].get("YTD") or [])
    fx_1w = charts_by["fx"].get("1W", [])
    fx_change = window_change(fx_1w[-2:]) if len(fx_1w) >= 2 else None

    snap = {"crypto": crypto_spot, "equities": equities_spot,
            "fx": {"price": fx_price, "change": fx_change}, "fng": get_fear_greed()}

    # ---- Portfolio: values, weights, blended trend, rebased overlay ----
    def price_of(a):
        return (crypto_spot.get(a, {}) if a in ("BTC", "ETH")
                else equities_spot.get(a, {})).get("price")

    def change_of(a):
        return (crypto_spot.get(a, {}) if a in ("BTC", "ETH")
                else equities_spot.get(a, {})).get("change")

    positions, total = {}, 0.0
    for a, sh in HOLDINGS.items():
        pr = price_of(a) or 0
        val = sh * pr
        total += val
        positions[a] = {"shares": sh, "price": round(pr, 2), "value": round(val, 2)}
    for a in positions:
        positions[a]["weight_pct"] = round(positions[a]["value"] / total * 100, 1) if total else 0

    today_num = sum(positions[a]["value"] * (change_of(a) or 0) / 100 for a in HOLDINGS)
    today_pct = round(today_num / total * 100, 2) if total else None

    ytd_by_asset = {a: (charts_by["crypto"][a] if a in ("BTC", "ETH")
                        else charts_by["equities"][a]).get("YTD", []) for a in HOLDINGS}
    combined = portfolio_daily(ytd_by_asset, HOLDINGS)
    value_charts = windows_from_daily(combined)
    port = {
        "total": round(total, 2), "today_pct": today_pct,
        "items": [(a, positions[a]["value"]) for a in HOLDINGS],
        "value_charts": value_charts,
        "overlay": {a: rebase100(ytd_by_asset[a]) for a in HOLDINGS},
    }
    snap["portfolio"] = {
        "total_value": round(total, 2), "today_pct": today_pct,
        "returns_pct": {"1W": window_change(value_charts["1W"]),
                        "1M": window_change(value_charts["1M"]),
                        "YTD": window_change(value_charts["YTD"])},
        "positions": positions,
    }
    print(f"[ok] portfolio total ${total:,.0f}, today {today_pct}%")
    print(f"[ok] snapshot: {json.dumps({k: v for k, v in snap.items() if k != 'portfolio'}, default=str)[:220]}...")

    print("[..] Claude research + synthesis (3-6 min)")
    try:
        digest = run_claude(build_prompt(snap, today))
    except SynthesisError as e:
        print(f"[warn] synthesis failed — rendering a degraded page so the site still updates: {e}")
        digest = fallback_digest(str(e)[:280])
    headline = digest.get("market_read") or digest.get("lede", "")[:90]

    html = render(today, snap, digest, charts_by, port)
    os.makedirs("docs/archive", exist_ok=True)
    open("docs/.nojekyll", "w").close()  # tell Pages not to run Jekyll on /docs
    with open("docs/index.html", "w") as f:
        f.write(html)
    with open(f"docs/archive/{today}.html", "w") as f:
        f.write(html)
    hist = save_history(load_history(), today, snap, headline)
    write_archive_index(hist)
    print("[ok] docs/index.html + archive + history written")

    maybe_email(html, today)
    print(f"\nHeadline: {headline}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        import traceback
        print("\n" + "=" * 64)
        print("MORNING WIRE FAILED — real error below:")
        print(f"  {type(exc).__name__}: {exc}")
        print("Common first-run causes:")
        print("  1. ANTHROPIC_API_KEY secret missing or misnamed "
              "(Settings -> Secrets and variables -> ACTIONS tab, not Codespaces).")
        print("  2. Credit balance too low on the Anthropic account (Console -> Billing). "
              "This returns HTTP 400 even with a valid key + web search enabled.")
        print("  3. Web search not enabled for the org in the Anthropic Console.")
        print("  4. Look for a line above starting with [api-error] — it prints the API's")
        print("     exact message, which names the real problem.")
        print("=" * 64)
        traceback.print_exc()
        sys.exit(1)
