"""
Configuración central del pipeline de datos - CryptoPulse Analytics.
"""
import os
from pathlib import Path

# --- Rutas locales ---
BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = BASE_DIR / "data"
RAW_DIR = DATA_DIR / "raw"
PROCESSED_DIR = DATA_DIR / "processed"
LIVE_DIR = DATA_DIR / "live"

for d in (RAW_DIR, PROCESSED_DIR, LIVE_DIR):
    d.mkdir(parents=True, exist_ok=True)

# --- Activos monitoreados ---
# symbol: (dataset HF, nombre completo, id en CoinGecko)
ASSETS = {
    "BTC": {
        "hf_dataset": "WinkingFace/CryptoLM-Bitcoin-BTC-USDT",
        "name": "Bitcoin",
        "coingecko_id": "bitcoin",
    },
    "ETH": {
        "hf_dataset": "WinkingFace/CryptoLM-Ethereum-ETH-USDT",
        "name": "Ethereum",
        "coingecko_id": "ethereum",
    },
    "SOL": {
        "hf_dataset": "WinkingFace/CryptoLM-Solana-SOL-USDT",
        "name": "Solana",
        "coingecko_id": "solana",
    },
}

# --- Columnas que se leen del parquet fuente (lectura columnar, ahorra RAM) ---
HF_COLUMNS = [
    "timestamp", "open", "high", "low", "close", "volume",
    "MA_20", "MA_50", "MA_200", "RSI", "MACD", "Signal",
    "ATR", "ADX", "BL_Upper", "BL_Lower",
]

# --- Almacenamiento en la nube ---
S3_BUCKET = os.environ.get("CRYPTOPULSE_S3_BUCKET", "cryptopulse-analytics-<tu-alias>")
S3_RAW_PREFIX = "raw"
S3_PROCESSED_PREFIX = "processed"
S3_LIVE_PREFIX = "live"

# --- API en vivo (CoinGecko, free tier, sin API key) ---
COINGECKO_URL = "https://api.coingecko.com/api/v3/simple/price"
LIVE_POLL_MINUTES = 15  # frecuencia de actualización del feed en vivo
