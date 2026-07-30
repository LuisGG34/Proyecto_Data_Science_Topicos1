"""
Ingesta histórica (batch, ejecución única o mensual).

Descarga el dataset OHLCV a 1 minuto de Hugging Face para cada activo
(WinkingFace/CryptoLM-*), lo resamplea a frecuencia diaria y lo guarda
en data/processed/<symbol>_daily.parquet.

Uso:
    python data_pipeline/ingest_historical.py
    python data_pipeline/ingest_historical.py --upload-s3   # además sube a S3

Nota de diseño: este script se ejecuta UNA VEZ (o mensualmente al
resincronizar con Hugging Face) desde una máquina con RAM suficiente
(laptop del equipo o una instancia EC2 temporal). El resultado, ya
reducido a granularidad diaria (miles de filas en vez de millones),
es lo único que viaje a S3 / a la instancia EC2 t2.micro que sirve el
dashboard, evitando que la instancia gratuita tenga que cargar los
datasets crudos de minuto a minuto en memoria.
"""
import argparse
import logging
import sys

import fsspec
import pandas as pd

from config import ASSETS, HF_COLUMNS, PROCESSED_DIR, RAW_DIR, S3_BUCKET, S3_RAW_PREFIX, S3_PROCESSED_PREFIX

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
log = logging.getLogger("ingest_historical")

AGG_RULES = {
    "open": "first",
    "high": "max",
    "low": "min",
    "close": "last",
    "volume": "sum",
    "MA_20": "last",
    "MA_50": "last",
    "MA_200": "last",
    "RSI": "last",
    "MACD": "last",
    "Signal": "last",
    "ATR": "last",
    "ADX": "last",
    "BL_Upper": "last",
    "BL_Lower": "last",
}


def _clean_month(df: pd.DataFrame) -> pd.DataFrame:
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df["timestamp"] = pd.to_datetime(df["timestamp"])
    return df.dropna(subset=["timestamp", "close"]).sort_values("timestamp")


def resample_daily(df: pd.DataFrame) -> pd.DataFrame:
    return (
        df.set_index("timestamp")
        .resample("1D")
        .agg(AGG_RULES)
        .dropna(subset=["close"])
        .reset_index()
        .rename(columns={"timestamp": "date"})
    )


def fetch_and_resample(symbol: str, hf_dataset: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Recorre los parquet mensuales publicados en HF mes a mes (streaming), resampleando
    a diario sobre la marcha para no cargar en memoria los ~2M+ filas minuto a minuto de
    una sola vez. Devuelve (daily_df, raw_sample_ultimo_mes)."""
    fs = fsspec.filesystem("hf")
    base = f"datasets/{hf_dataset}/data"
    files = sorted(f["name"] for f in fs.ls(base) if f["name"].endswith(".parquet"))
    log.info("%s: %d archivos mensuales encontrados en %s", symbol, len(files), base)

    daily_chunks = []
    last_month_raw = None
    for f in files:
        month_df = pd.read_parquet(f"hf://{f}", columns=HF_COLUMNS)
        month_df = _clean_month(month_df)
        if month_df.empty:
            continue
        daily_chunks.append(resample_daily(month_df))
        last_month_raw = month_df  # se sobrescribe; al final queda el mes más reciente

    if not daily_chunks:
        raise ValueError(f"Sin datos válidos para {symbol}")

    daily = pd.concat(daily_chunks, ignore_index=True)
    daily.insert(0, "symbol", symbol)
    return daily, last_month_raw


def main(upload_s3: bool = False, sample_raw: bool = True):
    all_daily = []

    for symbol, meta in ASSETS.items():
        try:
            daily, raw_sample = fetch_and_resample(symbol, meta["hf_dataset"])
        except Exception as exc:  # noqa: BLE001
            log.error("Fallo al leer %s: %s", symbol, exc)
            continue

        if sample_raw and raw_sample is not None:
            raw_sample_path = RAW_DIR / f"{symbol}_raw_sample.parquet"
            raw_sample.tail(50_000).to_parquet(raw_sample_path, index=False)
            log.info("Muestra cruda guardada en %s (%d filas)", raw_sample_path, min(len(raw_sample), 50_000))

        out_path = PROCESSED_DIR / f"{symbol}_daily.parquet"
        daily.to_parquet(out_path, index=False)
        daily.to_csv(out_path.with_suffix(".csv"), index=False)
        log.info("%s: %d filas diarias -> %s", symbol, len(daily), out_path)

        all_daily.append(daily)

    if not all_daily:
        log.error("No se pudo procesar ningún activo. Abortando.")
        sys.exit(1)

    combined = pd.concat(all_daily, ignore_index=True)
    combined_path = PROCESSED_DIR / "all_assets_daily.parquet"
    combined.to_parquet(combined_path, index=False)
    combined.to_csv(combined_path.with_suffix(".csv"), index=False)
    log.info("Combinado guardado en %s (%d filas totales)", combined_path, len(combined))

    if upload_s3:
        upload_to_s3()


def upload_to_s3():
    import boto3

    s3 = boto3.client("s3")
    for path in list(PROCESSED_DIR.glob("*")):
        key = f"{S3_PROCESSED_PREFIX}/{path.name}"
        s3.upload_file(str(path), S3_BUCKET, key)
        log.info("Subido a s3://%s/%s", S3_BUCKET, key)
    for path in list(RAW_DIR.glob("*")):
        key = f"{S3_RAW_PREFIX}/{path.name}"
        s3.upload_file(str(path), S3_BUCKET, key)
        log.info("Subido a s3://%s/%s", S3_BUCKET, key)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingesta histórica CryptoPulse")
    parser.add_argument("--upload-s3", action="store_true", help="Subir resultados a S3 tras procesar")
    parser.add_argument("--no-raw-sample", action="store_true", help="No guardar muestra de datos crudos")
    args = parser.parse_args()
    main(upload_s3=args.upload_s3, sample_raw=not args.no_raw_sample)
