"""
Transformación: une el histórico diario (Hugging Face) con el feed en vivo
(CoinGecko) y calcula los KPIs de negocio que consume el dashboard.

Genera:
  - data/processed/kpi_daily.parquet   -> serie diaria por activo con KPIs
  - data/processed/kpi_latest.csv      -> snapshot más reciente por activo
                                           (para las tarjetas KPI del dashboard)

Uso:
    python data_pipeline/transform.py
    python data_pipeline/transform.py --upload-s3
"""
import argparse
import logging

import numpy as np
import pandas as pd

from config import LIVE_DIR, PROCESSED_DIR, S3_BUCKET, S3_PROCESSED_PREFIX

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("transform")

DAILY_PATH = PROCESSED_DIR / "all_assets_daily.parquet"
RECENT_PATH = PROCESSED_DIR / "all_assets_recent.parquet"
LIVE_PATH = LIVE_DIR / "live_quotes.csv"
KPI_DAILY_PATH = PROCESSED_DIR / "kpi_daily.parquet"
KPI_LATEST_PATH = PROCESSED_DIR / "kpi_latest.csv"

OHLCV_COLS = ["symbol", "date", "open", "high", "low", "close", "volume"]


def reindex_full_calendar(combined: pd.DataFrame) -> pd.DataFrame:
    """Reindexa cada symbol a calendario diario completo (entre su primera y
    última fecha), insertando filas NaN explícitas en los días sin dato de
    ninguna fuente -- p. ej. el tramo entre el corte de Hugging Face y el
    límite de 365 días del plan gratuito de CoinGecko (ver
    backfill_recent.py:detect_uncovered_gap). Sin esto ese hueco queda
    invisible: el dashboard dibuja una línea recta entre el último precio
    antes del hueco y el primero después, y los indicadores rolling
    (MA/RSI/volatility) mezclan ambos lados como si fueran días consecutivos."""
    other_cols = [c for c in combined.columns if c not in ("symbol", "date")]
    out = []
    for symbol, g in combined.groupby("symbol"):
        g = g.set_index("date").sort_index()
        full_idx = pd.date_range(g.index.min(), g.index.max(), freq="D")
        g = g.reindex(full_idx)
        g.index.name = "date"
        g["symbol"] = symbol
        out.append(g.reset_index())
    return pd.concat(out, ignore_index=True)[["symbol", "date"] + other_cols]


def merge_hf_and_backfill(hf_daily: pd.DataFrame) -> pd.DataFrame:
    """Combina el histórico de Hugging Face (OHLCV real, hasta ~marzo 2025)
    con el backfill diario de CoinGecko (último año, ver backfill_recent.py).
    Ante fechas duplicadas por activo, se prioriza HF por tener OHLC real."""
    hf = hf_daily[OHLCV_COLS].copy()
    if not RECENT_PATH.exists():
        log.warning("No existe %s; corre backfill_recent.py para extender el histórico reciente", RECENT_PATH)
        combined = hf.sort_values(["symbol", "date"]).reset_index(drop=True)
    else:
        recent = pd.read_parquet(RECENT_PATH)[OHLCV_COLS]
        combined = pd.concat([hf, recent], ignore_index=True)
        combined = combined.drop_duplicates(subset=["symbol", "date"], keep="first")
        combined = combined.sort_values(["symbol", "date"]).reset_index(drop=True)

    return reindex_full_calendar(combined)


def compute_technical_indicators(g: pd.DataFrame) -> pd.DataFrame:
    """Recalcula MA/RSI/Bollinger sobre el cierre de la serie ya combinada
    (HF + backfill), en vez de depender de los indicadores precalculados de
    HF, que no cubren el tramo de backfill."""
    close = g["close"]
    g["MA_20"] = close.rolling(20, min_periods=5).mean()
    g["MA_50"] = close.rolling(50, min_periods=10).mean()
    std_20 = close.rolling(20, min_periods=5).std()
    g["BL_Upper"] = g["MA_20"] + 2 * std_20
    g["BL_Lower"] = g["MA_20"] - 2 * std_20

    delta = close.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    avg_loss = loss.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    g["RSI"] = 100 - (100 / (1 + rs))
    # A diferencia de .rolling(), .ewm() no propaga NaN cuando el input es
    # NaN: "congela" el último valor calculado. Sin esto, el RSI se queda
    # pegado en su última lectura real durante todo un hueco de datos (ver
    # reindex_full_calendar) -- incluyendo el primer día real al volver del
    # hueco, donde delta ya es NaN porque el cierre anterior no existe.
    g.loc[delta.isna(), "RSI"] = np.nan
    # Sólo rellena el arranque en frío (antes del primer valor real, sin
    # suficiente historia todavía): neutral. Un NaN a mitad de serie es un
    # hueco de datos real y debe seguir NaN, no taparse con una línea plana
    # en 50.
    first_valid = g["RSI"].first_valid_index()
    if first_valid is not None:
        g.loc[:first_valid, "RSI"] = g.loc[:first_valid, "RSI"].fillna(50)

    # MACD (12/26/9 estándar). Igual que RSI, .ewm() congela el EMA durante
    # un hueco en vez de propagar NaN, así que se enmascara con la misma
    # condición (delta.isna() cubre todo el hueco + el primer día real al
    # volver, que es donde el EMA congelado se mezclaría con el precio nuevo).
    ema_12 = close.ewm(span=12, adjust=False, min_periods=12).mean()
    ema_26 = close.ewm(span=26, adjust=False, min_periods=26).mean()
    g["MACD"] = ema_12 - ema_26
    g["Signal"] = g["MACD"].ewm(span=9, adjust=False, min_periods=9).mean()
    g.loc[delta.isna(), ["MACD", "Signal"]] = np.nan

    # ATR (14, suavizado de Wilder) sobre el rango verdadero (True Range).
    prev_close = close.shift(1)
    true_range = pd.concat(
        [g["high"] - g["low"], (g["high"] - prev_close).abs(), (g["low"] - prev_close).abs()], axis=1
    ).max(axis=1)
    g["ATR"] = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    g.loc[delta.isna(), "ATR"] = np.nan

    # ADX (14, Wilder) -- fuerza de tendencia a partir de +DI/-DI.
    high_diff = g["high"].diff()
    low_diff = g["low"].diff()
    invalid_dm = high_diff.isna() | low_diff.isna()
    up_move, down_move = high_diff, -low_diff
    plus_dm = pd.Series(np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=g.index)
    minus_dm = pd.Series(np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=g.index)
    # np.where con NaN evalúa la comparación como False y devuelve 0.0 en vez
    # de NaN -- sin esto, el hueco de datos se leería como "134 días de
    # movimiento direccional cero" real en vez de "sin dato", contaminando el
    # EMA de ADX mucho más que un simple congelamiento.
    plus_dm[invalid_dm] = np.nan
    minus_dm[invalid_dm] = np.nan
    atr_adx = true_range.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    plus_di = 100 * plus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_adx
    minus_di = 100 * minus_dm.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean() / atr_adx
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    g["ADX"] = dx.ewm(alpha=1 / 14, adjust=False, min_periods=14).mean()
    g.loc[delta.isna(), "ADX"] = np.nan

    return g


def compute_daily_kpis(daily: pd.DataFrame) -> pd.DataFrame:
    daily = daily.sort_values(["symbol", "date"]).copy()
    out = []
    for symbol, g in daily.groupby("symbol"):
        g = g.sort_values("date").reset_index(drop=True)
        g = compute_technical_indicators(g)
        # fill_method=None: pandas por defecto rellena (pad) los NaN antes de
        # calcular el % de cambio, lo que reintroduciría el salto del hueco
        # de datos (ver reindex_full_calendar) como si fuera el retorno de
        # un solo día real.
        g["daily_return"] = g["close"].pct_change(fill_method=None)
        g["cumulative_return"] = (1 + g["daily_return"].fillna(0)).cumprod() - 1
        g["volatility_30d"] = g["daily_return"].rolling(30, min_periods=5).std() * np.sqrt(365)
        running_max = g["close"].cummax()
        g["drawdown"] = g["close"] / running_max - 1
        g["max_drawdown_to_date"] = g["drawdown"].cummin()
        out.append(g)
    return pd.concat(out, ignore_index=True)


def compute_dominance(live: pd.DataFrame) -> pd.DataFrame:
    """Dominancia de market cap = market_cap del activo / suma de market caps
    de todos los activos monitoreados en ese mismo instante de captura."""
    live = live.copy()
    totals = live.groupby("fetched_at")["market_cap_usd"].transform("sum")
    live["market_cap_dominance_pct"] = (live["market_cap_usd"] / totals) * 100
    return live


def build_latest_snapshot(kpi_daily: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    latest_hist = kpi_daily.sort_values("date").groupby("symbol").tail(1).set_index("symbol")
    rows = []
    for symbol, g in live.sort_values("fetched_at").groupby("symbol"):
        last_live = g.iloc[-1]
        hist = latest_hist.loc[symbol] if symbol in latest_hist.index else None
        rows.append(
            {
                "symbol": symbol,
                "price_usd": last_live["price_usd"],
                "pct_change_24h": last_live["pct_change_24h"],
                "market_cap_usd": last_live["market_cap_usd"],
                "market_cap_dominance_pct": last_live.get("market_cap_dominance_pct"),
                "volume_24h_usd": last_live["volume_24h_usd"],
                "fetched_at": last_live["fetched_at"],
                "rsi_last_hist": hist["RSI"] if hist is not None else None,
                "volatility_30d_hist": hist["volatility_30d"] if hist is not None else None,
                "max_drawdown_to_date_hist": hist["max_drawdown_to_date"] if hist is not None else None,
                "last_hist_date": hist["date"] if hist is not None else None,
            }
        )
    return pd.DataFrame(rows)


def upload_to_s3():
    import boto3

    s3 = boto3.client("s3")
    for path in (KPI_DAILY_PATH, KPI_LATEST_PATH):
        key = f"{S3_PROCESSED_PREFIX}/{path.name}"
        s3.upload_file(str(path), S3_BUCKET, key)
        log.info("Subido a s3://%s/%s", S3_BUCKET, key)


def main(upload_s3: bool = False):
    if not DAILY_PATH.exists():
        raise FileNotFoundError(f"Falta {DAILY_PATH}. Ejecuta primero ingest_historical.py")

    hf_daily = pd.read_parquet(DAILY_PATH)
    daily = merge_hf_and_backfill(hf_daily)
    kpi_daily = compute_daily_kpis(daily)
    kpi_daily.to_parquet(KPI_DAILY_PATH, index=False)
    kpi_daily.to_csv(KPI_DAILY_PATH.with_suffix(".csv"), index=False)
    log.info("KPIs diarios guardados en %s (%d filas)", KPI_DAILY_PATH, len(kpi_daily))

    if LIVE_PATH.exists():
        live = pd.read_csv(LIVE_PATH)
        live = compute_dominance(live)
        latest = build_latest_snapshot(kpi_daily, live)
        latest.to_csv(KPI_LATEST_PATH, index=False)
        log.info("Snapshot en vivo guardado en %s (%d activos)", KPI_LATEST_PATH, len(latest))
    else:
        log.warning("No existe %s todavía; corre ingest_live.py primero para el snapshot en vivo", LIVE_PATH)

    if upload_s3:
        upload_to_s3()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Transformación / KPIs CryptoPulse")
    parser.add_argument("--upload-s3", action="store_true")
    args = parser.parse_args()
    main(upload_s3=args.upload_s3)
