# The Morning Wire

A daily, phone-readable markets brief for the assets you actually hold — built to
help you read flows and think about positioning, not to feel like a toy.

**Covers:** BTC · ETH · KO · BRK.B · PLTR · CRCL · USD/PEN

## What each edition gives you

**Your portfolio — the cockpit view, up top.**
Total value and today's weighted move, a blended portfolio-value trend (1W / 1M /
YTD), a donut allocation with each position's dollar value and weight, an overlay of
all holdings rebased to 100 so you can see who's carrying the book, and a written
portfolio read that accounts for your actual weights. Position sizes live in the
`HOLDINGS` dict at the top of `flow_agent.py` — edit them when you rebalance.

**Crypto (BTC, ETH) — a plain up/down flow read for your book.**
A single direction badge (`▲ inflows / longs building`, `▼ outflows / longs
unwinding`, `shorts building`, or `mixed`) sitting on top of the numbers that
actually drive your trades: perp funding + annualized, cash-and-carry basis, spot
ETF net flow, open-interest direction, and long/short liquidations — then a
5-7 sentence "what this means for your book" written for your cash-and-carry basis
trade and your long/short options positioning, referencing the venues you can use
(Coinbase Advanced, Kraken, IBKR/tastytrade CME micros).

**Equities (KO, BRK.B, PLTR, CRCL) — three charts each, then real depth.**
Every stock gets **1W / 1M / YTD** charts (each with its own % change), followed by:
what moved it and the *mechanism* (why/how), the long-term outlook, what large
holders / insiders / options flow suggest big players are doing, options strategies
a long-term holder uses to harvest upside (covered calls, cash-secured puts,
collars, LEAPS / poor-man's covered call — with tradeoffs), and a bull vs bear read.

**FX (USD/PEN) — rate, three charts, and corridor context** tied to your USD→Peru
economics.

**Confidence tags** on every causal claim: `STRONG` (quantified link) ·
`LIKELY` (plausible, timing fits) · `SPECULATIVE` (narrative, weak evidence).

## History you can reflect on

Every edition is frozen to `docs/archive/YYYY-MM-DD.html` with that day's charts
baked in, and listed on a scrollable **archive page** with each day's headline.
Scroll back weeks later and see how the tape actually played out.

## Setup (about 8 minutes, once)

1. Create a **new empty repo** (public = simplest for free Pages).
2. Add these files at these exact paths:
   - `flow_agent.py`
   - `.github/workflows/wire.yml`
   - `README.md`
3. Repo → **Settings → Pages** → Source: **GitHub Actions**.
4. Repo → **Settings → Secrets and variables → Actions** → add secret
   `ANTHROPIC_API_KEY`.
5. **Actions** tab → "Morning Wire" → **Run workflow** for the first edition.

Live at `https://<username>.github.io/<repo>/`. Bookmark it on your phone. It
refreshes daily at 7:30 AM Miami time (before US open).

### Want it emailed too?

Add three more secrets and the workflow picks them up — no code change:
`GMAIL_ADDRESS`, `GMAIL_APP_PASSWORD` (a Google
[App Password](https://myaccount.google.com/apppasswords), needs 2FA), and
`MAIL_TO`. Leave them unset to run dashboard-only.

### Prefer Cloudflare Pages?

Point Cloudflare Pages at the repo, output directory `docs`, no build command. It
serves the same `docs/index.html` the Action commits. GitHub Pages is fewer moving
parts, so it's the default.

## Data sources (all free, no paid feeds)

- Prices & charts: Yahoo Finance (equities + USD/PEN), CoinGecko (crypto), with
  Stooq / Coinbase as fallbacks.
- Flows, funding, basis, OI, ETF flows, news, filings: researched live each morning
  via the Claude API with web search, and synthesized into the brief.

## The honest data model

On-chain and ETF-custody data is public; **intra-exchange crypto trades and most
equity order flow are not.** So attribution is inference, not proof — that's why
every causal claim is confidence-tagged. Options content is **educational**
(how a strategy works and its tradeoffs), **not a recommendation** — sizing and
risk are yours. Not financial advice.
