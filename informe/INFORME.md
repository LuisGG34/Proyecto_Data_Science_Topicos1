# CryptoPulse Analytics — Informe del Trabajo Recuperativo

**Curso:** Tópicos de Data Science 1
**Entrega:** hasta el 9 de agosto de 2026
**Solución:** Dashboard de analítica de criptoactivos (BTC, ETH, SOL) desplegado en AWS

---

## 1. Contexto y problema de negocio

### 1.1 Organización analizada

**CryptoPulse Capital**, una consultora/gestora boutique de activos digitales
(caso de negocio ficticio, original) que asesora a inversionistas retail
sofisticados y a pequeños *family offices* en Latinoamérica en su exposición a
criptomonedas. A diferencia de un fondo tradicional, CryptoPulse no tiene
presupuesto para terminales profesionales de pago (ej. Bloomberg Terminal,
Kaiko, Glassnode Enterprise, que cuestan miles de USD al mes), por lo que
necesita una solución propia, económica (capa gratuita en la nube) y
mantenible por un equipo de datos pequeño.

### 1.2 Problema / necesidad de negocio

El comité de inversión de CryptoPulse debe decidir semanalmente cómo ajustar
la exposición del portafolio entre BTC, ETH y SOL. Hoy esa decisión se toma
revisando manualmente 3–4 exchanges y hojas de cálculo desactualizadas, sin
una vista unificada de **precio, riesgo (volatilidad/drawdown) y momentum
(RSI)**, lo que genera decisiones lentas y poco trazables, además de
dificultar el reporting a clientes.

### 1.3 Usuarios / stakeholders del dashboard

| Stakeholder | Necesidad |
|---|---|
| Comité de inversión / Portfolio Managers | Ver precio, tendencia y momentum para decidir asignación de cartera |
| Analista de riesgo | Monitorear volatilidad y máximo drawdown por activo |
| Equipo de datos / Data Engineer | Mantener el pipeline de ingesta y la infraestructura |
| Clientes / inversionistas (reporting) | Entender el desempeño reciente de los activos en los que están expuestos |

### 1.4 Preguntas de negocio que el dashboard permite responder

1. ¿Cuál es el precio actual de BTC/ETH/SOL y cómo varió en las últimas 24 h?
2. ¿Qué tan riesgoso (volátil) está cada activo en la ventana reciente (30 días)?
3. ¿Está el activo en zona de sobrecompra o sobreventa (RSI) que sugiera ajustar exposición?
4. ¿Cuál ha sido la caída máxima (drawdown) desde su máximo histórico hasta hoy?
5. ¿Cómo se compara el retorno acumulado de BTC, ETH y SOL en un período dado?
6. ¿Qué tan correlacionados están los movimientos diarios entre los tres activos (para decisiones de diversificación)?
7. ¿Qué porcentaje de la capitalización de mercado conjunta concentra cada activo?

---

## 2. KPIs y métricas

| # | KPI | Definición | Fuente de datos | Frecuencia de actualización | Valor objetivo / benchmark |
|---|---|---|---|---|---|
| 1 | **Precio spot (USD)** | Último precio de mercado | CoinGecko API (vivo) + histórico Hugging Face | ~15 min (vivo) / diario (histórico) | Referencia de mercado, sin objetivo fijo |
| 2 | **Variación 24 h (%)** | Cambio porcentual del precio respecto a hace 24 h | CoinGecko API | ~15 min | Codificado visualmente: verde si > 0, rojo si < 0 |
| 3 | **Volatilidad 30 días (anualizada)** | Desv. estándar de retornos diarios (ventana móvil de 30 días) × √365 | Calculado sobre histórico HF resampleado a diario | Diario | Umbral de riesgo elevado definido por el comité: **> 60% anualizado** |
| 4 | **RSI (14 períodos)** | Índice de Fuerza Relativa, oscilador de momentum 0–100 | Precalculado en el dataset de Hugging Face | Diario | Bandas estándar de la industria: **< 30 sobreventa, > 70 sobrecompra** |
| 5 | **Máximo drawdown** | Caída % máxima desde el punto más alto histórico hasta la fecha | Calculado sobre histórico HF | Diario | Sin objetivo fijo; se usa comparativamente entre los 3 activos |
| 6 | **Retorno acumulado del período** | Retorno compuesto entre el inicio y el fin del rango de fechas filtrado | Calculado sobre histórico HF | Diario, recalculado según el filtro del usuario | Se compara contra el retorno de BTC como referencia de mercado |
| 7 | **Dominancia de capitalización de mercado (%)** | Market cap del activo / suma de market caps de BTC+ETH+SOL | CoinGecko API (vivo) | ~15 min | Referencia de concentración de exposición |

Se cumple el mínimo de 3 KPIs solicitado; se incluyen 7 para cubrir precio,
riesgo, momentum y concentración — las cuatro dimensiones que el comité de
inversión evalúa en cada sesión.

---

## 3. Pipeline de datos

| Etapa | Implementación | Detalle |
|---|---|---|
| **Ingesta** | `ingest_historical.py` + `backfill_recent.py` + `ingest_live.py` | Batch histórico (Hugging Face, una vez / mensual) + backfill diario (CoinGecko, 365 días) + streaming ligero programado (CoinGecko cada 15 min vía `systemd timer` en EC2) |
| **Almacenamiento** | Amazon S3, bucket con prefijos `raw/`, `processed/`, `live/` | Data lake simple: capa cruda (muestra), capa procesada (parquet diario + KPIs) y capa de snapshots en vivo (CSV acumulado) |
| **Transformación** | `data_pipeline/transform.py` (pandas) | Resampleo de 1 min → 1 día, cálculo de retornos diarios/acumulados, volatilidad rolling 30d, drawdown, dominancia de mercado; combina histórico + vivo en un snapshot "latest" |
| **Consumo** | `dashboard/app.py` (Streamlit + Plotly, servido en EC2 tras Nginx) | Lee `kpi_daily.parquet` y `kpi_latest.csv` desde S3 con caché de 5 min (`st.cache_data(ttl=300)`) |

### 3.1 Arquitectura de alto nivel

```
Hugging Face (histórico 1 min, 2017–2025)      CoinGecko API (precio/volumen/mkt cap en vivo)
                │  batch, 1 vez/mensual                       │  cada 15 min
                ▼                                             ▼
        ┌───────────────────────── EC2 t3.micro (Free Tier) ─────────────────────────┐
        │  data_pipeline/  → resamplea, calcula KPIs → sube a S3                     │
        │  dashboard/ (Streamlit :8501) ← lee de S3 ← Nginx :80 (proxy) ← usuario     │
        └──────────────────────────────────────────────────────────────────────────┘
                                        ▲
                                S3 (raw / processed / live)
                                        ▲
                          IAM Role (permisos mínimos S3, sin llaves embebidas)
```

Ver diagrama interactivo y wireframe en el artifact adjunto, y el detalle
completo de despliegue en [`deploy/AWS_DEPLOY.md`](../deploy/AWS_DEPLOY.md),
donde además se justifica la elección de cada servicio AWS (EC2, S3, IAM,
Nginx) bajo capa gratuita.

---

## 4. Modelo de datos

### 4.1 Fuentes de información

- **Hugging Face** — `WinkingFace/CryptoLM-Bitcoin-BTC-USDT`, `-Ethereum-ETH-USDT`, `-Solana-SOL-USDT`. Dataset público, licencia MIT, sin autenticación. OHLCV real a 1 minuto desde agosto 2017 hasta marzo 2025 para BTC/ETH; para SOL el dataset solo cubre ~23 días (fue incorporado tarde a esa fuente).
- **CoinGecko API — `/simple/price`** — API REST pública y gratuita, sin API key, usada para el feed en vivo (precio, market cap, volumen 24h, variación 24h), cada 15 min.
- **CoinGecko API — `/coins/{id}/market_chart`** — misma API, usada por `backfill_recent.py` para traer hasta 365 días de precio/volumen diario por activo (límite del plan gratuito). Se usa para (a) cerrar la brecha entre el corte de Hugging Face (~marzo 2025) y la fecha actual en los tres activos, y (b) extender el histórico de SOL de 23 días a ~1 año. **Limitación documentada:** este endpoint entrega un precio por día, no un OHLC real, por lo que en ese tramo `open = high = low = close`; es una aproximación aceptable para KPIs basados en cierre (retorno, volatilidad, drawdown, RSI, medias móviles) pero no para análisis de rango intradía. Por esta razón, `transform.py` recalcula MA/RSI/Bandas de Bollinger sobre la serie combinada en vez de usar los indicadores precalculados de Hugging Face, que no cubren el tramo de backfill.

### 4.2 Formato de los datos

| Capa | Formato | Motivo |
|---|---|---|
| Histórico crudo (Hugging Face) | Parquet particionado por mes | Columnar, comprimido, permite lectura selectiva de columnas |
| Procesado (diario + KPIs) | Parquet + CSV | Parquet para el pipeline/dashboard (eficiente); CSV como copia legible/portable |
| Live feed | CSV acumulado + JSON crudo de la API | CSV facilita el *append* incremental; JSON es la respuesta nativa de CoinGecko |

### 4.3 Frecuencia de actualización

- Histórico (Hugging Face): recarga manual/mensual (`ingest_historical.py`), ya que el dataset fuente se actualiza con ese ritmo.
- Backfill reciente (CoinGecko): se re-ejecuta junto con el feed en vivo, cada 15 minutos, para mantener el día actual siempre al día.
- Live: cada 15 minutos (`LIVE_POLL_MINUTES` en `config.py`), vía `systemd timer` en la instancia EC2.
- Dashboard: caché de 5 minutos (`st.cache_data(ttl=300)`), balance entre "casi en vivo" y no saturar S3/CoinGecko.

### 4.4 Modelo conceptual (entidades, relaciones, campos principales)

Esquema en estrella: **Asset** como dimensión, el resto como tablas de hechos
particionadas por `symbol` + fecha/timestamp.

```
Asset (symbol PK, name, coingecko_id)
   │ 1—N
   ├── PriceObservationDaily (symbol FK, date, open, high, low, close, volume)
   ├── TechnicalIndicatorDaily (symbol FK, date, MA_20, MA_50, MA_200, RSI, MACD, Signal, ATR, ADX, BL_Upper, BL_Lower)
   ├── DerivedKPIDaily (symbol FK, date, daily_return, cumulative_return, volatility_30d, drawdown, max_drawdown_to_date)
   └── LiveQuote (symbol FK, fetched_at, price_usd, market_cap_usd, volume_24h_usd, pct_change_24h, market_cap_dominance_pct)
```

En la implementación física, `PriceObservationDaily`, `TechnicalIndicatorDaily`
y `DerivedKPIDaily` viven en una sola tabla ancha (`kpi_daily.parquet`, mismo
grano symbol+date) por simplicidad; `LiveQuote` es `live_quotes.csv` /
`kpi_latest.csv`. El diagrama entidad-relación completo está en el artifact
adjunto.

---

## 5. Diseño del dashboard

### 5.1 Filtros e interacción

- **Selector múltiple de criptomonedas** (BTC / ETH / SOL) — cambia qué activos se grafican.
- **Rango de fechas** (slider) — acota histórico y recalcula KPIs/retornos en vivo.
- **Toggle de indicadores** — muestra/oculta medias móviles y Bandas de Bollinger sobre el precio.
- Tooltips unificados (`hovermode="x unified"`), zoom/pan nativo de Plotly, tabla de datos filtrable y botón de descarga CSV.

### 5.2 Justificación de los tipos de gráfico

| Gráfico | Tipo | Por qué |
|---|---|---|
| Precio (1 activo seleccionado) | **Velas (candlestick)** + medias móviles | Estándar financiero: muestra apertura/máximo/mínimo/cierre en un solo vistazo, más medias móviles como referencia de tendencia |
| Volumen | **Barras** | Magnitud discreta por período, se lee mejor en barras que en línea |
| RSI | **Línea con bandas de referencia 30/70** | Oscilador de rango fijo (0–100); las líneas horizontales marcan sobrecompra/sobreventa de forma inmediata |
| Comparación multi-activo | **Línea indexada a base 100** | BTC, ETH y SOL tienen escalas de precio muy distintas (~$85.000 vs ~$1.900 vs ~$74); indexar permite comparar *desempeño relativo* en un solo eje, evitando un gráfico de doble eje (antipatrón) |
| Correlación de retornos | **Heatmap divergente (azul↔rojo, centro gris)** | La correlación es una magnitud con polaridad (-1 a +1); una paleta divergente comunica signo e intensidad de un vistazo |
| Retorno acumulado | **Línea con línea base en 0** | Serie temporal continua; el 0 marca el punto de equilibrio (ganancia/pérdida) |
| Indicadores clave | **Tarjetas KPI (`st.metric`)** | Lectura inmediata para stakeholders no técnicos (comité de inversión), con delta codificado por color |

Un solo eje por gráfico en todos los casos (nunca doble eje); los colores de
cada criptomoneda son fijos y consistentes en todo el dashboard (BTC azul,
ETH naranja, SOL aqua), siguiendo una paleta categórica validada para
daltonismo.

### 5.3 Wireframe

Ver el artifact adjunto (`Wireframe y arquitectura — CryptoPulse Analytics`)
para el mockup visual. Estructura de la página (de arriba hacia abajo):

1. Título + fila de filtros (criptomonedas, rango de fechas, toggle indicadores)
2. Fila de tarjetas KPI (una por activo seleccionado)
3. Gráfico principal: velas + indicadores (1 activo) o comparación normalizada + volumen + correlación (varios activos)
4. Gráfico de retorno acumulado del período
5. Tabla de datos filtrable + descarga CSV

### 5.4 Visualizaciones funcionales

El dashboard está implementado y funcional en `dashboard/app.py` (Streamlit +
Plotly), y corre tanto localmente (`streamlit run dashboard/app.py`) como
desplegado en la instancia EC2 (ver [`deploy/AWS_DEPLOY.md`](../deploy/AWS_DEPLOY.md)).

### 5.5 Accesibilidad: legibilidad, tema claro/oscuro y daltonismo

`dashboard/app.py` fija el tema base de Streamlit en `.streamlit/config.toml`
(`base = "light"`) y define su propio par de temas en `THEMES` (claro/oscuro),
seleccionable con el toggle "🌙 Oscuro" junto al título. Antes de esto, el CSS
inyectado fijaba colores de texto claros sin depender del tema de Streamlit;
si el navegador del usuario forzaba modo oscuro (heredado del SO), ese texto
quedaba oscuro sobre fondo oscuro — de ahí el reporte de baja legibilidad.
Fijar el tema base y mover todos los colores (fondo, texto, grilla, series,
sombreado de huecos/backfill) a `THEMES` deja el toggle in-app como única
fuente de verdad sobre claro/oscuro, en vez de heredar una preferencia del
sistema que el resto de la app no seguía.

Ambos temas reutilizan la misma paleta categórica ya validada para daltonismo
(BTC azul, ETH naranja, SOL aqua — ver §5.2), en su paso claro y su paso
oscuro; los colores de estado (`good`/`bad`, verde/rojo) son fijos entre
temas por diseño. El caso rojo/verde de las velas (candlestick) es el peor
escenario para daltonismo rojo-verde (protanopia/deuteranopia): además del
color, las velas alcistas quedan huecas (relleno = color de fondo) y las
bajistas sólidas, así la dirección se lee por forma incluso si el color no se
distingue. Las líneas de referencia de sobrecompra/sobreventa del RSI (70/30)
suman una etiqueta de texto, por la misma razón.

---

## 6. Consideraciones del enunciado — cómo se cumplen

- **Evidencia de implementación:** capturas de pantalla del dashboard corriendo
  en `http://<IP-pública-EC2>`, de `systemctl status` de los servicios, y del
  bucket S3 poblado (ver checklist al final de `AWS_DEPLOY.md`).
- **Datos:** públicos, de Hugging Face (histórico) y CoinGecko (vivo); no fue
  necesario generar datos sintéticos.
- **Arquitectura en la nube, capa gratuita:** EC2 t3.micro (750 h/mes) + S3
  (5 GB) + IAM (sin costo); cada servicio justificado en `AWS_DEPLOY.md`.
- **Caso de negocio original:** CryptoPulse Capital, consultora boutique
  LatAm de asesoría en criptoactivos — no es un caso genérico de "dashboard
  de precios de cripto", sino uno con stakeholders, KPIs de riesgo/momentum y
  un problema de negocio concretos.
