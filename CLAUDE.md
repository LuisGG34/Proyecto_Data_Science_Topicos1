# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

CryptoPulse Analytics — a crypto analytics dashboard (BTC/ETH/SOL) built for
a university assignment ("Trabajo recuperativo", Tópicos de Data Science 1).
Business case, KPI definitions, data model and dashboard design rationale
live in `informe/INFORME.md` — read it before changing KPI logic, chart
choices, or the business framing, since those were deliberately specified
to answer the assignment's questions.

There is no test suite, linter, or build step configured in this repo — it's
a data pipeline + Streamlit app, not a packaged library.

## Commands

Install dependencies:
```bash
pip install -r requirements.txt
```

Run the data pipeline, in order (each step writes to `data/`):
```bash
python data_pipeline/ingest_historical.py   # one-time/monthly: downloads BTC/ETH/SOL from Hugging Face, resamples 1min -> daily
python data_pipeline/backfill_recent.py     # fills the gap between the HF cutoff and today via CoinGecko (also extends SOL's short history)
python data_pipeline/ingest_live.py         # appends one near-real-time snapshot from CoinGecko
python data_pipeline/transform.py           # merges everything, recomputes indicators, writes the KPI tables the dashboard reads
```

Run the dashboard locally:
```bash
streamlit run dashboard/app.py
# http://localhost:8501
```
On Windows, if `streamlit` isn't recognized (its Scripts folder often isn't
on PATH for a per-user pip install), run it as a module instead:
`python -m streamlit run dashboard/app.py`.

`ingest_historical.py` and `transform.py` accept `--upload-s3` to push their
outputs to the S3 bucket configured via `CRYPTOPULSE_S3_BUCKET` (see
`data_pipeline/config.py`). In production (EC2) `ingest_live.py`,
`backfill_recent.py`, and `transform.py` run on a 15-minute loop via
`deploy/crypto-live.timer`; `ingest_historical.py` is not part of that loop
(see Architecture below).

## Architecture

**Why ingestion is split into three scripts instead of one.** The full 1-minute
OHLCV history from Hugging Face is multiple GB across ~2.7M rows/asset — too
much for the free-tier EC2 instance (1 GB RAM) to ever load. So:
- `ingest_historical.py` streams the Hugging Face parquet files **month by
  month** (never holding more than one month in memory) and immediately
  resamples each month to daily before moving to the next — this is meant to
  run once, from a machine with normal RAM (a laptop), not from the EC2
  instance.
- `backfill_recent.py` and `ingest_live.py` only touch small CoinGecko API
  responses and are the only ingestion scripts meant to run repeatedly on
  the EC2 instance.
- The EC2 dashboard never re-downloads the Hugging Face history; it only
  ever reads the small `data/processed/*` outputs (from S3 when
  `DATA_SOURCE=s3`).

**Why there's a separate backfill step.** The Hugging Face dataset
(`WinkingFace/CryptoLM-*`) stops around March 2025, and its SOL dataset only
covers ~23 days (added late upstream) versus ~8 years for BTC/ETH.
`backfill_recent.py` pulls up to 365 daily points per asset from CoinGecko's
free `/market_chart` endpoint to close both gaps. Those backfilled rows only
have a single daily price point (no real intraday OHLC), so
`open == high == low == close` for that segment — `transform.py` is written
around this: it does **not** trust Hugging Face's precomputed indicator
columns (MA/RSI/Bollinger/MACD/ATR/ADX), because those don't exist for the
backfilled segment. Instead `transform.py` recomputes MA_20/MA_50, Bollinger
bands, and RSI itself from the merged close-price series
(`compute_technical_indicators`), so indicators are consistent across the
HF and CoinGecko segments. When touching indicator logic, change it in
`transform.py`, not by reaching for the columns already present in the raw
per-symbol daily parquet files.

**Merge precedence.** `merge_hf_and_backfill()` concatenates HF daily rows
with CoinGecko backfill rows and drops duplicate `(symbol, date)` pairs
keeping the HF version — HF's real OHLC wins wherever both sources overlap.

**Data flow / layout on disk:**
```
data/raw/        small raw samples (last month per asset) — evidence only, not consumed downstream
data/processed/  {SYMBOL}_daily.parquet      <- from ingest_historical.py (HF, resampled to daily)
                 {SYMBOL}_recent.parquet     <- from backfill_recent.py (CoinGecko, up to 365d)
                 all_assets_{daily,recent}.parquet  <- concatenated across symbols
                 kpi_daily.parquet           <- from transform.py: merged history + recomputed indicators + derived KPIs (daily_return, cumulative_return, volatility_30d, drawdown, max_drawdown_to_date)
                 kpi_latest.csv              <- from transform.py: one row per symbol, live price joined with the latest historical indicators — this is what feeds the dashboard's KPI cards
data/live/       live_quotes.csv             <- append-only log from ingest_live.py (every CoinGecko poll)
```
`dashboard/app.py` only ever reads `kpi_daily.parquet` and `kpi_latest.csv`.

**Local vs. cloud data source.** `dashboard/app.py` switches between reading
`data/processed/` on disk and `s3://$CRYPTOPULSE_S3_BUCKET/processed/` based
on the `DATA_SOURCE` env var (`local` default, `s3` in the EC2 deployment).
Both paths go through the same `load_data()` / `_path()` helpers — don't
special-case S3 elsewhere.

**Dashboard layout logic.** `dashboard/app.py` branches on how many symbols
are selected in the multiselect filter: exactly one symbol shows a
candlestick + moving averages/Bollinger + volume + RSI subplot stack;
multiple symbols switch to a base-100-indexed comparison line, grouped
volume, and a return-correlation heatmap instead (see `informe/INFORME.md`
§5.2 for why each chart type was chosen — e.g. indexing to base 100 instead
of a dual-axis chart, since BTC/ETH/SOL trade at very different price
scales). Per-asset colors are fixed (`COLORS` dict: BTC blue, ETH orange,
SOL aqua) and must stay consistent with the same palette used in the
architecture/wireframe artifact — don't let a filter change repaint a
symbol's color.

**Light/dark theme colors.** `dashboard/app.py` defines a `THEMES` dict
(light/dark) with its own `text_primary`/`text_secondary`/`muted` grays,
consumed by both the injected CSS and the Plotly chart traces (`MUTED`,
`GRID`, etc.) — never hardcode a text color outside `THEME`, since a value
tuned for one mode reads as illegible in the other. Each gray must be
checked against *that mode's own background* (`page_bg`/`surface_bg`), not
copy-pasted between modes: `muted` used to be the identical hex in both
themes, which worked for dark (~5.9:1 against `#0d0d0d`) but gave only
~3.4:1 in light mode against `#f9f9f7` — below the WCAG AA text minimum
(4.5:1) — making secondary chart lines/labels hard to read on white. When
tweaking `THEMES`, verify contrast against the background for that mode
specifically (aim for >=4.5:1 for text-bearing colors).

**Deployment.** `deploy/AWS_DEPLOY.md` is the authoritative, step-by-step
AWS setup (S3 bucket, IAM role, EC2 launch, security group). The systemd
units in `deploy/` encode the intended split: `streamlit.service` runs the
dashboard continuously; `crypto-live.timer` fires `crypto-live.service`
every 15 minutes, which chains `ingest_live.py` -> `backfill_recent.py` ->
`transform.py`, each with `--upload-s3`. `deploy/bootstrap_ec2.sh` installs
and wires up both. If you change the ingestion chain's order or add a
script to it, update `deploy/crypto-live.service` to match.
