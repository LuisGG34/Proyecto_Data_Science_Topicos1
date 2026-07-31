# Backlog del dashboard — mirada de un analista de inversión

Spec de mejoras priorizadas para CryptoPulse Analytics, para implementar de a poco.
No implementado todavía; este documento es la referencia de qué construir y por qué.

## P0 — Integridad de datos (bloqueante para confiar en el dashboard)

### 1. Corregir el hueco de 134 días entre HF y CoinGecko — RESUELTO
**Hallazgo (EDA, confirmado en `data/processed/kpi_daily.parquet`):** existe un gap real
de datos entre **2025-03-19** (corte de Hugging Face) y **2025-07-31** (inicio del backfill
de CoinGecko) para BTC, ETH y SOL. En el gráfico de precio esto se veía como una línea recta
que conecta ambos extremos (+39% BTC, +86% ETH, +35% SOL de un día a otro), y no como una
ausencia de datos.

**Causa raíz:** `data_pipeline/backfill_recent.py`, `fetch_recent_daily(..., days=365)`
siempre trae los últimos 365 días **desde la fecha de ejecución**, no desde el corte fijo de
Hugging Face (~marzo 2025). El hueco crece un día por cada día que pasa sin volver a correr
`ingest_historical.py`.

**El fix propuesto originalmente ("anclar `days` al corte de HF") no es viable**: se probó
en vivo contra la API de CoinGecko y el plan gratuito devuelve 401
`"Public API users are limited to querying historical data within the past 365 days"` para
`/market_chart` y también para `/history` con fecha específica — es un tope duro del plan
gratuito, no un parámetro que el código pueda pedir de más. Ese tramo de 134 días no se
puede recuperar con datos reales desde ninguna fuente gratuita.

**Fix implementado:**
- `backfill_recent.py`: `detect_uncovered_gap()` compara el corte de
  `{symbol}_daily.parquet` (HF) contra el inicio del backfill y deja un `WARNING` explícito
  en el log con el tamaño exacto del hueco cada vez que corre el pipeline.
- `transform.py`: `merge_hf_and_backfill()` ahora reindexa cada activo a calendario diario
  completo (`reindex_full_calendar`), insertando filas `NaN` explícitas donde no hay dato de
  ninguna fuente. `compute_daily_kpis`/`compute_technical_indicators` se ajustaron para que
  ese `NaN` se propague correctamente: `pct_change(fill_method=None)` (el default de pandas
  rellena antes de calcular el % de cambio, lo que reintroducía el salto falso) y una máscara
  explícita en RSI (`.ewm()` no propaga `NaN` como sí lo hace `.rolling()`; sin la máscara,
  el RSI quedaba "congelado" en su última lectura real durante todo el hueco).
- Verificado en los datos: `close`, `MA_20`, `RSI`, `daily_return` y `volatility_30d` quedan
  en `NaN` durante todo el tramo 2025-03-20 a 2025-07-31 y en la fila de transición, en vez
  de mostrar un salto o un valor congelado. El gráfico de precio ahora corta la línea en vez
  de dibujarla recta.
- **Pendiente relacionado (no resuelto):** `cumulative_return` en `transform.py` sigue
  usando `daily_return.fillna(0)`, por lo que ese tramo se muestra como 0% en vez de romperse
  — el salto real de precio queda "absorbido" silenciosamente. No se tocó en este fix; ver
  también el ítem #2 (marcar visualmente el hueco) para decidir el tratamiento correcto ahí.

### 2. Marcar visualmente los huecos y el tramo backfill en el gráfico de precio — RESUELTO
**Fix implementado** (`dashboard/app.py`):
- `_contiguous_ranges()` agrupa cualquier máscara booleana en bloques de fechas contiguas.
- `add_gap_shading()` sombrea en gris cada tramo sin dato de ninguna fuente (`close` NaN) y
  lo anota "Sin datos (fuente no disponible)" — aplicado al gráfico de velas (1 activo) y al
  de comparación indexada (varios activos, usando el hueco compartido entre los símbolos
  seleccionados).
- `add_backfill_shading()` sombrea en ámbar el tramo donde `open == high == low == close`
  (backfill de CoinGecko, sin OHLC real) y lo anota "CoinGecko backfill: sin OHLC real
  (precio de cierre diario)" — sólo en la vista de un activo, donde tiene sentido mostrar la
  forma de la vela.
- Corregido de paso: la base del índice 100 en la comparación multi-activo tomaba
  `sdf["close"].iloc[0]` sin filtrar NaN; si el rango de fechas filtrado empezaba dentro de un
  hueco, el índice completo salía `NaN`. Ahora usa el primer cierre real.

**Verificado con un smoke test** que ejecuta `dashboard/app.py` completo con un stub de
`streamlit` (no hay navegador disponible en este entorno) para ambos modos:
- 1 activo (BTC): detectó y sombreó por separado el backfill (~365 días) y **dos** huecos
  reales — el de 134 días (2025) y uno de 2 días preexistente en el histórico de Hugging Face
  (2018-02-08), sin que se le pidiera buscarlo específicamente.
- Varios activos: sombreó el hueco compartido en el gráfico de comparación indexada.
- Sin excepciones en ningún caso.

**Pendiente relacionado (no resuelto):** el gráfico de retorno acumulado no tiene esta
sombra — sigue mostrando el tramo del hueco como 0% plano por el `fillna(0)` mencionado en
el ítem #1.

### 3. Indicador de calidad/frescura de datos — RESUELTO
**Fix implementado** (`dashboard/app.py`):
- `format_minutes_ago()` formatea el `fetched_at` del feed en vivo como "hace X min" / "hace
  X h" / "sin dato".
- `pct_real_ohlc()` calcula, por activo y sobre todo el historial (no el rango filtrado), qué
  % de los días con dato son OHLC real de Hugging Face vs. backfill de CoinGecko (mismo
  criterio `open==high==low==close` que ya usa el shading del ítem #2).
- Cada tarjeta KPI ahora muestra una línea adicional: "🕒 Feed en vivo: hace X min · 📊
  Historial con OHLC real: XX%".
- `LIVE_STALE_MINUTES = 30` (2x `LIVE_POLL_MINUTES` de `config.py`): si el último `fetched_at`
  supera ese umbral, se dispara `st.warning` avisando que puede que `ingest_live.py` haya
  dejado de correr en EC2. Si no hay ninguna fila en `kpi_latest.csv` para el activo, avisa por
  separado que se está mostrando el último cierre histórico en vez de un precio en vivo.
- Corregido de paso: las tarjetas KPI tomaban la última fila del rango filtrado sin chequear
  si era un hueco (`close` NaN) — con el fix del ítem #1 eso podía mostrar "RSI: nan". Ahora
  se filtra a la última fila con dato real antes de leer RSI/volatilidad/drawdown.

**Verificado:** valores calculados sobre los datos reales coinciden con el EDA original —
BTC/ETH 88.4% OHLC real, SOL 5.9% (backfill 94.1%, tal como se había encontrado). Probado el
umbral de staleness con timestamps sintéticos (5 min, 30 min, 90 min, 5h, sin dato) — formatea
y dispara la alerta correctamente en cada caso.

## P1 — Indicadores que un analista espera y hoy no están

### 4. MACD, ATR y ADX
El modelo de datos conceptual (`informe/INFORME.md` §4.4) ya los contempla, pero
`transform.py` no los recalcula — hoy simplemente no existen en `kpi_daily.parquet`
(columnas actuales: `MA_20, MA_50, BL_Upper, BL_Lower, RSI`, sin `MACD`, `Signal`, `ATR`,
`ADX`, `MA_200`). Un analista técnico usa MACD para cruces de señal, ADX para fuerza de
tendencia y ATR para dimensionar stops en unidades de precio (no en %). Recalcularlos sobre
la serie combinada, igual que ya se hace con RSI/MA/Bollinger.

### 5. Retorno ajustado por riesgo (Sharpe / Sortino)
Hoy el dashboard muestra retorno acumulado y volatilidad por separado. Un analista compara
activos por retorno **por unidad de riesgo**. Agregar Sharpe ratio (o Sortino, que sólo
penaliza volatilidad a la baja) como KPI adicional por activo y periodo filtrado.

### 6. Correlación rolling, no solo estática
El heatmap de correlación usa una sola ventana fija (todo el rango filtrado). Un analista
quiere ver **cómo cambia la correlación en el tiempo** (ej. correlación 90 días rolling
BTC-ETH) para detectar cambios de régimen (desacople/acople entre activos).

## P2 — Soporte a la toma de decisión

### 7. Umbrales de riesgo visibles y configurables
El umbral de volatilidad "> 60% anualizado" está documentado en el informe pero no aparece
como línea de referencia en el gráfico de `volatility_30d`, a diferencia del RSI que sí
tiene las bandas 30/70 dibujadas. Agregar la misma referencia visual, y permitir que el
analista ajuste el umbral desde la UI.

### 8. Anotaciones de eventos de mercado
Permitir marcar eventos relevantes sobre la línea de tiempo (ej. "aprobación ETF",
"halving", "hackeo de exchange") para dar contexto a movimientos bruscos de precio —
especialmente útil justo en tramos como el del hallazgo #1, donde sin anotación un salto de
+39% se puede confundir con un evento de mercado real.

### 9. Benchmark externo
Comparar el retorno indexado de BTC/ETH/SOL contra un benchmark tradicional (ej. S&P 500,
oro) para evaluar el mercado cripto en un contexto macro más amplio — relevante dado que
ProDev también sigue metales.

## P3 — Usabilidad

### 10. Exportar snapshot (PDF/Excel) además del CSV actual
Ya está anotado como línea de hoja de ruta en el informe general; se detalla aquí porque es
lo que un analista pide primero para armar un reporte a gerencia sin copiar datos a mano.

### 11. Períodos configurables para MA/RSI
Hoy MA_20/MA_50 y RSI(14) están hardcodeados en `transform.py`. Exponer los períodos como
parámetro en la UI (ej. RSI(9) para trading de corto plazo vs. RSI(21) para swing).

---

**Sugerencia de orden de implementación:** #1 y #2 primero (son integridad de datos, no
features — mientras no estén, cualquier lectura del gráfico de precio en el tramo
mar-jul 2025 es engañosa), luego #4 y #5 (los indicadores que más se usan en un análisis
técnico/de riesgo estándar), y el resto según lo que priorice ProDev.
