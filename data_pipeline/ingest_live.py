"""
Ingesta en vivo (near-real-time).

Consulta la API pública y gratuita de CoinGecko (sin API key) para BTC, ETH y
SOL, y agrega una fila por activo al archivo acumulado data/live/live_quotes.csv.

Diseñado para ejecutarse periódicamente (cada LIVE_POLL_MINUTES minutos) vía
un systemd timer / cron en la instancia EC2 (ver deploy/). Cada ejecución es
liviana (una llamada HTTP + append de 3 filas), por lo que corre sin problema
en una instancia t2.micro / t3.micro de la capa gratuita.

Uso:
    python data_pipeline/ingest_live.py
    python data_pipeline/ingest_live.py --upload-s3
"""
import argparse
import logging
from datetime import datetime, timezone

import pandas as pd
import requests

from config import ASSETS, COINGECKO_URL, LIVE_DIR, S3_BUCKET, S3_LIVE_PREFIX

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ingest_live")

LIVE_FILE = LIVE_DIR / "live_quotes.csv"


def fetch_live_quotes() -> pd.DataFrame:
    ids = ",".join(meta["coingecko_id"] for meta in ASSETS.values())
    params = {
        "ids": ids,
        "vs_currencies": "usd",
        "include_market_cap": "true",
        "include_24hr_vol": "true",
        "include_24hr_change": "true",
        "include_last_updated_at": "true",
    }
    resp = requests.get(COINGECKO_URL, params=params, timeout=15)
    resp.raise_for_status()
    payload = resp.json()

    fetched_at = datetime.now(timezone.utc).isoformat()
    rows = []
    for symbol, meta in ASSETS.items():
        cg_id = meta["coingecko_id"]
        if cg_id not in payload:
            log.warning("Sin datos de CoinGecko para %s (%s)", symbol, cg_id)
            continue
        d = payload[cg_id]
        rows.append(
            {
                "fetched_at": fetched_at,
                "symbol": symbol,
                "price_usd": d.get("usd"),
                "market_cap_usd": d.get("usd_market_cap"),
                "volume_24h_usd": d.get("usd_24h_vol"),
                "pct_change_24h": d.get("usd_24h_change"),
                "source_updated_at": d.get("last_updated_at"),
                "source": "coingecko",
            }
        )
    return pd.DataFrame(rows)


def append_to_live_file(new_rows: pd.DataFrame):
    if LIVE_FILE.exists():
        existing = pd.read_csv(LIVE_FILE)
        combined = pd.concat([existing, new_rows], ignore_index=True)
    else:
        combined = new_rows
    combined.to_csv(LIVE_FILE, index=False)
    log.info("live_quotes.csv actualizado: %d filas totales (+%d nuevas)", len(combined), len(new_rows))


def upload_to_s3():
    import boto3

    s3 = boto3.client("s3")
    key = f"{S3_LIVE_PREFIX}/{LIVE_FILE.name}"
    s3.upload_file(str(LIVE_FILE), S3_BUCKET, key)
    log.info("Subido a s3://%s/%s", S3_BUCKET, key)


def main(upload_s3: bool = False):
    quotes = fetch_live_quotes()
    if quotes.empty:
        log.error("No se obtuvo ninguna cotización. Abortando.")
        return
    append_to_live_file(quotes)
    if upload_s3:
        upload_to_s3()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingesta en vivo CryptoPulse (CoinGecko)")
    parser.add_argument("--upload-s3", action="store_true", help="Subir live_quotes.csv a S3 tras actualizar")
    args = parser.parse_args()
    main(upload_s3=args.upload_s3)
