# CryptoPulse Analytics

Dashboard de analítica y visualización de datos para monitoreo de BTC, ETH y
SOL — Trabajo recuperativo, Tópicos de Data Science 1 (entrega: 9 de agosto
de 2026).

📄 **Informe completo (contexto de negocio, KPIs, arquitectura, modelo de
datos, diseño del dashboard):** [`informe/INFORME.md`](informe/INFORME.md)

🖼️ **Diagrama de arquitectura + modelo de datos + wireframe:** artifact
publicado (ver enlace compartido en la entrega).

☁️ **Guía de despliegue en AWS paso a paso:** [`deploy/AWS_DEPLOY.md`](deploy/AWS_DEPLOY.md)

## Estructura del proyecto

```
data_pipeline/          Scripts de ingesta y transformación
  config.py                Configuración central (activos, rutas, S3)
  ingest_historical.py     Descarga histórico HF (BTC/ETH/SOL), resamplea a diario
  ingest_live.py           Ingesta en vivo (CoinGecko), corre cada 15 min
  transform.py             Calcula KPIs (retorno, volatilidad, drawdown, dominancia)

dashboard/
  app.py                   Dashboard Streamlit (filtros, KPIs, gráficos)

deploy/
  AWS_DEPLOY.md             Guía paso a paso de despliegue en AWS (EC2 + S3 + IAM)
  bootstrap_ec2.sh          Script de instalación en la instancia EC2
  streamlit.service         Unidad systemd del dashboard
  crypto-live.service/.timer Unidad systemd de la ingesta en vivo (cada 15 min)
  nginx_cryptopulse.conf    Proxy inverso Nginx (puerto 80 → 8501)
  iam_policy_s3.json        Política IAM de mínimo privilegio para S3

informe/
  INFORME.md                Informe completo del proyecto (todas las preguntas del enunciado)

data/                    Datos generados localmente (no versionar en git salvo muestras)
  raw/                      Muestra de datos crudos (último mes, por activo)
  processed/                Histórico diario + KPIs (parquet/csv)
  live/                     Feed acumulado de CoinGecko
```

## Cómo correrlo localmente

```bash
pip install -r requirements.txt

# 1) Ingesta histórica (una vez) — descarga y resamplea BTC/ETH/SOL desde Hugging Face
python data_pipeline/ingest_historical.py

# 2) Ingesta en vivo (repetir cada 15 min, o una vez para probar)
python data_pipeline/ingest_live.py

# 2b) Backfill diario reciente (CoinGecko, 365 días) — cierra la brecha entre
#     el corte del dataset HF (~marzo 2025) y hoy, y extiende SOL (que en HF
#     solo tiene ~23 días de historia) a un año completo
python data_pipeline/backfill_recent.py

# 3) Transformación / cálculo de KPIs (combina HF + backfill + recalcula indicadores)
python data_pipeline/transform.py

# 4) Levantar el dashboard
streamlit run dashboard/app.py
```

Para desplegarlo en AWS (EC2 + S3), sigue [`deploy/AWS_DEPLOY.md`](deploy/AWS_DEPLOY.md).

## Fuentes de datos

- **Histórico:** [`WinkingFace/CryptoLM-Bitcoin-BTC-USDT`](https://huggingface.co/datasets/WinkingFace/CryptoLM-Bitcoin-BTC-USDT), `-Ethereum-ETH-USDT`, `-Solana-SOL-USDT` (Hugging Face, público, MIT, OHLCV 1min 2017–2025 + indicadores técnicos).
- **Vivo:** [CoinGecko API](https://www.coingecko.com/en/api) `/simple/price` (pública, sin API key).
