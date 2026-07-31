"""
Backfill de datos diarios recientes vía CoinGecko (complementa a Hugging Face).

Motivo: el dataset histórico de Hugging Face (WinkingFace/CryptoLM-*) llega
hasta marzo de 2025 y, para SOL en particular, solo cubre ~23 días (fue
agregado tarde a esa fuente). CoinGecko expone gratis, sin API key, hasta
365 días de historia diaria por activo vía /market_chart, lo que permite:

  1) Extender SOL con un histórico diario razonable (1 año) en vez de 23 días.
  2) Cerrar la brecha entre el corte del dataset HF y la fecha actual para
     los tres activos.

Limitación documentada: CoinGecko /market_chart entrega un precio por día
(no OHLC real), por lo que aquí open = high = low = close = precio de cierre
del día y volume = volumen 24h reportado. Es una aproximación aceptable para
KPIs basados en cierre (retorno, volatilidad, drawdown, RSI); no se debe usar
para análisis de rango intradía en este tramo.

Uso:
    python data_pipeline/backfill_recent.py
"""
import argparse
import logging

import pandas as pd
import requests

from config import ASSETS, PROCESSED_DIR, S3_BUCKET, S3_PROCESSED_PREFIX

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("backfill_recent")

MARKET_CHART_URL = "https://api.coingecko.com/api/v3/coins/{id}/market_chart"


def fetch_recent_daily(coingecko_id: str, days: int = 365) -> pd.DataFrame:
    params = {"vs_currency": "usd", "days": days, "interval": "daily"}
    resp = requests.get(MARKET_CHART_URL.format(id=coingecko_id), params=params, timeout=30)
    resp.raise_for_status()
    payload = resp.json()

    prices = pd.DataFrame(payload["prices"], columns=["ts", "close"])
    volumes = pd.DataFrame(payload["total_volumes"], columns=["ts", "volume"])
    df = prices.merge(volumes, on="ts")
    df["date"] = pd.to_datetime(df["ts"], unit="ms").dt.normalize()
    df = df.groupby("date", as_index=False).last()  # último punto de cada día calendario
    df["open"] = df["close"]
    df["high"] = df["close"]
    df["low"] = df["close"]
    df["source"] = "coingecko_daily"
    return df[["date", "open", "high", "low", "close", "volume", "source"]]


def detect_uncovered_gap(symbol: str, recent_start) -> None:
    """Avisa si queda un tramo sin cubrir entre el corte del histórico de
    Hugging Face (data/processed/<symbol>_daily.parquet) y el inicio de este
    backfill. CoinGecko free tier rechaza pedir historia más allá de 365 días
    hacia atrás (confirmado: /market_chart y /history responden 401
    "exceeds the allowed time range" para fechas más antiguas), así que ese
    hueco no se puede cerrar pidiendo más días aquí -- sólo detectarlo. Al no
    quedar cubierto por ninguna fuente, transform.py debe dejarlo como NaN
    explícito al fusionar, en vez de conectar los precios de antes/después
    del hueco con una línea recta."""
    hf_path = PROCESSED_DIR / f"{symbol}_daily.parquet"
    if not hf_path.exists():
        return
    hf_max = pd.read_parquet(hf_path, columns=["date"])["date"].max()
    gap_days = (recent_start - hf_max).days - 1
    if gap_days > 0:
        log.warning(
            "%s: hueco de %d día(s) sin dato real entre el corte de Hugging Face (%s) "
            "y el inicio del backfill de CoinGecko (%s). El plan gratuito de CoinGecko "
            "no permite pedir historia más allá de 365 días hacia atrás, así que este "
            "tramo no se puede cerrar desde aquí -- verifica que transform.py lo esté "
            "marcando como NaN explícito en vez de interpolarlo.",
            symbol, gap_days, hf_max.date(), recent_start.date(),
        )


def upload_to_s3(paths):
    import boto3

    s3 = boto3.client("s3")
    for path in paths:
        key = f"{S3_PROCESSED_PREFIX}/{path.name}"
        s3.upload_file(str(path), S3_BUCKET, key)
        log.info("Subido a s3://%s/%s", S3_BUCKET, key)


def main(upload_s3: bool = False):
    frames = []
    written = []
    for symbol, meta in ASSETS.items():
        try:
            recent = fetch_recent_daily(meta["coingecko_id"])
        except Exception as exc:  # noqa: BLE001
            log.error("Fallo al obtener backfill de %s: %s", symbol, exc)
            continue
        detect_uncovered_gap(symbol, recent["date"].min())
        recent.insert(0, "symbol", symbol)
        out_path = PROCESSED_DIR / f"{symbol}_recent.parquet"
        recent.to_parquet(out_path, index=False)
        log.info("%s: %d días recientes (CoinGecko) -> %s [%s .. %s]",
                  symbol, len(recent), out_path, recent["date"].min().date(), recent["date"].max().date())
        frames.append(recent)
        written.append(out_path)

    if frames:
        combined = pd.concat(frames, ignore_index=True)
        combined_path = PROCESSED_DIR / "all_assets_recent.parquet"
        combined.to_parquet(combined_path, index=False)
        written.append(combined_path)
        log.info("Backfill combinado guardado en %s (%d filas)", combined_path, len(combined))

    if upload_s3 and written:
        upload_to_s3(written)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backfill diario reciente CryptoPulse (CoinGecko)")
    parser.add_argument("--upload-s3", action="store_true")
    args = parser.parse_args()
    main(upload_s3=args.upload_s3)
