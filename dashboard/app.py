"""
CryptoPulse Analytics — Dashboard de indicadores clave para BTC / ETH / SOL.

Lee las tablas ya transformadas por data_pipeline/ (kpi_daily.parquet y
kpi_latest.csv), localmente o desde S3 según la variable de entorno
DATA_SOURCE. Se ejecuta con:

    streamlit run dashboard/app.py --server.port 8501
"""
import os
from pathlib import Path

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import streamlit as st
from plotly.subplots import make_subplots

# ---------------------------------------------------------------------------
# Config / paleta (ver skill dataviz — colores categóricos fijos por activo)
# ---------------------------------------------------------------------------
st.set_page_config(page_title="CryptoPulse Analytics", page_icon="📊", layout="wide")

COLORS = {
    "BTC": "#2a78d6",   # slot 1 - blue
    "ETH": "#eb6834",   # slot 2 - orange
    "SOL": "#1baf7a",   # slot 3 - aqua
}
GOOD = "#0ca30c"
BAD = "#d03b3b"
MUTED = "#898781"
GRID = "#e1e0d9"
DIVERGING = ["#1c5cab", "#f0efec", "#d03b3b"]  # blue -> gray -> red (correlación)

DATA_SOURCE = os.environ.get("DATA_SOURCE", "local")  # "local" | "s3"
S3_BUCKET = os.environ.get("CRYPTOPULSE_S3_BUCKET", "")
LOCAL_PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

REFRESH_SECONDS = 300  # refresco del feed en vivo (coherente con LIVE_POLL_MINUTES=15 -> cache 5 min)
LIVE_STALE_MINUTES = 30  # 2x LIVE_POLL_MINUTES (config.py): margen antes de avisar que
# ingest_live.py dejó de correr en EC2, en vez de mostrar un precio desactualizado como si
# fuera fresco


def _path(name: str) -> str:
    if DATA_SOURCE == "s3":
        return f"s3://{S3_BUCKET}/processed/{name}"
    return str(LOCAL_PROCESSED / name)


@st.cache_data(ttl=REFRESH_SECONDS)
def load_data():
    kpi_daily = pd.read_parquet(_path("kpi_daily.parquet"))
    kpi_daily["date"] = pd.to_datetime(kpi_daily["date"])
    try:
        kpi_latest = pd.read_csv(_path("kpi_latest.csv"))
    except FileNotFoundError:
        kpi_latest = pd.DataFrame()
    return kpi_daily, kpi_latest


GAP_COLOR = "rgba(120,120,120,0.18)"
BACKFILL_COLOR = "rgba(230,170,40,0.14)"


def _contiguous_ranges(mask: pd.Series, dates: pd.Series):
    """Devuelve [(inicio, fin), ...] para cada bloque contiguo de True en
    `mask`, alineado a `dates` (ambas ya ordenadas por fecha)."""
    ranges = []
    in_block = False
    start = prev_date = None
    for is_true, d in zip(mask, dates):
        if is_true and not in_block:
            start, in_block = d, True
        elif not is_true and in_block:
            ranges.append((start, prev_date))
            in_block = False
        prev_date = d
    if in_block:
        ranges.append((start, prev_date))
    return ranges


def add_gap_shading(fig, dates: pd.Series, is_gap: pd.Series, row=1):
    """Sombrea tramos sin dato de ninguna fuente (ver reindex_full_calendar
    en transform.py) para que una línea/vela cortada se lea como "sin datos"
    y no como un movimiento de mercado real."""
    for start, end in _contiguous_ranges(is_gap, dates):
        fig.add_vrect(
            x0=start, x1=end, fillcolor=GAP_COLOR, line_width=0,
            annotation_text="Sin datos (fuente no disponible)", annotation_font_size=10,
            annotation_position="top left", row=row, col=1,
        )


def add_backfill_shading(fig, sdf: pd.DataFrame, row=1):
    """Sombrea el tramo de backfill de CoinGecko, donde open == high == low
    == close porque la fuente sólo entrega un precio de cierre por día (no
    OHLC real) -- ver backfill_recent.py."""
    is_backfill = (
        sdf["close"].notna()
        & (sdf["open"] == sdf["high"]) & (sdf["high"] == sdf["low"]) & (sdf["low"] == sdf["close"])
    )
    for start, end in _contiguous_ranges(is_backfill, sdf["date"]):
        fig.add_vrect(
            x0=start, x1=end, fillcolor=BACKFILL_COLOR, line_width=0,
            annotation_text="CoinGecko backfill: sin OHLC real (precio de cierre diario)",
            annotation_font_size=10, annotation_position="top left", row=row, col=1,
        )


def format_minutes_ago(ts) -> str:
    if pd.isna(ts):
        return "sin dato"
    minutes = (pd.Timestamp.now(tz="UTC") - ts).total_seconds() / 60
    if minutes < 1:
        return "hace instantes"
    if minutes < 60:
        return f"hace {minutes:.0f} min"
    return f"hace {minutes / 60:.1f} h"


def pct_real_ohlc(kpi_daily: pd.DataFrame, symbol: str) -> float:
    """% del historial del activo con OHLC real (Hugging Face), sobre los
    días en los que existe algún dato -- excluye del cálculo los huecos sin
    ninguna fuente (ver reindex_full_calendar en transform.py). El resto es
    backfill de CoinGecko (open == high == low == close, un solo precio de
    cierre por día)."""
    sym_hist = kpi_daily[kpi_daily["symbol"] == symbol]
    has_data = sym_hist["close"].notna()
    n_data = has_data.sum()
    if n_data == 0:
        return float("nan")
    is_backfill = (
        has_data
        & (sym_hist["open"] == sym_hist["high"])
        & (sym_hist["high"] == sym_hist["low"])
        & (sym_hist["low"] == sym_hist["close"])
    )
    return (n_data - is_backfill.sum()) / n_data * 100


PLOTLY_LAYOUT = dict(
    template="plotly_white",
    font=dict(family="system-ui, -apple-system, Segoe UI, sans-serif", color="#0b0b0b"),
    plot_bgcolor="#fcfcfb",
    paper_bgcolor="#fcfcfb",
    margin=dict(l=10, r=10, t=40, b=10),
    legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="left", x=0),
    hovermode="x unified",
)

# ---------------------------------------------------------------------------
# Carga de datos
# ---------------------------------------------------------------------------
try:
    kpi_daily, kpi_latest = load_data()
except FileNotFoundError:
    st.error(
        "No se encontraron los datos procesados. Ejecuta primero:\n\n"
        "```\npython data_pipeline/ingest_historical.py\n"
        "python data_pipeline/ingest_live.py\n"
        "python data_pipeline/transform.py\n```"
    )
    st.stop()

ALL_SYMBOLS = sorted(kpi_daily["symbol"].unique().tolist())

# ---------------------------------------------------------------------------
# Encabezado + filtros (una fila, arriba de los gráficos)
# ---------------------------------------------------------------------------
st.title("📊 CryptoPulse Analytics")
st.caption(
    "Monitoreo de precio, riesgo y momentum de BTC, ETH y SOL para el comité de inversión — "
    "datos históricos (Hugging Face) + feed en vivo (CoinGecko)."
)

f1, f2, f3 = st.columns([2, 3, 2])
with f1:
    selected = st.multiselect("Criptomonedas", ALL_SYMBOLS, default=ALL_SYMBOLS)
with f2:
    min_d, max_d = kpi_daily["date"].min().date(), kpi_daily["date"].max().date()
    date_range = st.slider(
        "Rango de fechas", min_value=min_d, max_value=max_d, value=(min_d, max_d), format="YYYY-MM-DD"
    )
with f3:
    show_indicators = st.checkbox("Mostrar medias móviles / Bollinger", value=True)

if not selected:
    st.warning("Selecciona al menos una criptomoneda.")
    st.stop()

mask = (
    kpi_daily["symbol"].isin(selected)
    & (kpi_daily["date"].dt.date >= date_range[0])
    & (kpi_daily["date"].dt.date <= date_range[1])
)
df = kpi_daily.loc[mask].copy()

# ---------------------------------------------------------------------------
# Tarjetas KPI
# ---------------------------------------------------------------------------
st.subheader("Indicadores clave")
kpi_cols = st.columns(len(selected))
for col, symbol in zip(kpi_cols, selected):
    sym_df = df[(df["symbol"] == symbol) & df["close"].notna()].sort_values("date")
    if sym_df.empty:
        with col:
            st.warning(f"{symbol}: sin datos reales en el rango de fechas filtrado.")
        continue
    hist_last = sym_df.iloc[-1]
    live_row = kpi_latest[kpi_latest["symbol"] == symbol] if not kpi_latest.empty else pd.DataFrame()

    if not live_row.empty:
        price = live_row["price_usd"].iloc[0]
        chg = live_row["pct_change_24h"].iloc[0]
        dom = live_row["market_cap_dominance_pct"].iloc[0]
        fetched_at = pd.to_datetime(live_row["fetched_at"].iloc[0], utc=True, errors="coerce")
    else:
        price = hist_last["close"]
        chg = hist_last["daily_return"] * 100
        dom = np.nan
        fetched_at = pd.NaT

    minutes_since_fetch = (
        (pd.Timestamp.now(tz="UTC") - fetched_at).total_seconds() / 60 if pd.notna(fetched_at) else None
    )
    is_stale = minutes_since_fetch is not None and minutes_since_fetch > LIVE_STALE_MINUTES

    with col:
        st.metric(
            label=f"{symbol} — Precio (USD)",
            value=f"${price:,.2f}",
            delta=f"{chg:+.2f}% 24h",
        )
        st.caption(
            f"RSI(14): **{hist_last['RSI']:.1f}**  ·  "
            f"Volatilidad 30d (anualizada): **{hist_last['volatility_30d']*100:.1f}%**  ·  "
            f"Máx. drawdown: **{hist_last['max_drawdown_to_date']*100:.1f}%**"
            + (f"  ·  Dominancia mkt cap: **{dom:.1f}%**" if pd.notna(dom) else "")
        )
        st.caption(
            f"ATR(14): **${hist_last['ATR']:,.2f}** (referencia para dimensionar stops) · "
            f"ADX(14): **{hist_last['ADX']:.1f}** ({'tendencia fuerte' if hist_last['ADX'] >= 25 else 'sin tendencia clara'})"
        )
        st.caption(
            f"🕒 Feed en vivo: {format_minutes_ago(fetched_at)}  ·  "
            f"📊 Historial con OHLC real: {pct_real_ohlc(kpi_daily, symbol):.0f}%"
        )
        if fetched_at is pd.NaT or pd.isna(fetched_at):
            st.warning(f"{symbol}: sin feed en vivo (mostrando último cierre histórico).")
        elif is_stale:
            st.warning(
                f"{symbol}: feed en vivo desactualizado — última lectura {format_minutes_ago(fetched_at)} "
                f"(umbral: {LIVE_STALE_MINUTES} min). Revisa si ingest_live.py sigue corriendo."
            )

st.divider()

# ---------------------------------------------------------------------------
# Vista de precio: candlestick + indicadores (un activo) o comparación normalizada (varios)
# ---------------------------------------------------------------------------
if len(selected) == 1:
    symbol = selected[0]
    sdf = df[df["symbol"] == symbol].sort_values("date")
    color = COLORS.get(symbol, "#2a78d6")

    st.subheader(f"{symbol} — Precio, volumen y momentum")
    fig = make_subplots(
        rows=4, cols=1, shared_xaxes=True, row_heights=[0.45, 0.15, 0.2, 0.2], vertical_spacing=0.035,
        subplot_titles=("Precio (velas) + medias móviles", "Volumen", "RSI (14) + ADX (14)", "MACD (12/26/9)"),
    )
    fig.add_trace(
        go.Candlestick(
            x=sdf["date"], open=sdf["open"], high=sdf["high"], low=sdf["low"], close=sdf["close"],
            increasing_line_color=GOOD, decreasing_line_color=BAD, name=symbol, showlegend=False,
        ),
        row=1, col=1,
    )
    if show_indicators:
        fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["MA_20"], line=dict(color=color, width=1.5),
                                  name="MA 20"), row=1, col=1)
        fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["MA_50"], line=dict(color=MUTED, width=1.5, dash="dash"),
                                  name="MA 50"), row=1, col=1)
        fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["BL_Upper"], line=dict(color=GRID, width=1),
                                  name="Bollinger sup.", showlegend=False), row=1, col=1)
        fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["BL_Lower"], line=dict(color=GRID, width=1),
                                  fill="tonexty", fillcolor="rgba(137,135,129,0.08)",
                                  name="Bollinger inf.", showlegend=False), row=1, col=1)

    fig.add_trace(go.Bar(x=sdf["date"], y=sdf["volume"], marker_color=color, opacity=0.6,
                          name="Volumen", showlegend=False), row=2, col=1)

    fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["RSI"], line=dict(color=color, width=2),
                              name="RSI", showlegend=False), row=3, col=1)
    fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["ADX"], line=dict(color=MUTED, width=1.5, dash="dot"),
                              name="ADX", showlegend=False), row=3, col=1)
    fig.add_hline(y=70, line=dict(color=BAD, width=1, dash="dot"), row=3, col=1)
    fig.add_hline(y=30, line=dict(color=GOOD, width=1, dash="dot"), row=3, col=1)
    fig.add_hline(y=25, line=dict(color=MUTED, width=1, dash="dot"), row=3, col=1)  # ADX >= 25: tendencia fuerte

    macd_hist = sdf["MACD"] - sdf["Signal"]
    fig.add_trace(go.Bar(x=sdf["date"], y=macd_hist,
                          marker_color=np.where(macd_hist >= 0, GOOD, BAD), opacity=0.5,
                          name="MACD - Señal", showlegend=False), row=4, col=1)
    fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["MACD"], line=dict(color=color, width=1.5),
                              name="MACD", showlegend=False), row=4, col=1)
    fig.add_trace(go.Scatter(x=sdf["date"], y=sdf["Signal"], line=dict(color=MUTED, width=1.5, dash="dash"),
                              name="Señal", showlegend=False), row=4, col=1)

    add_backfill_shading(fig, sdf, row=1)
    add_gap_shading(fig, sdf["date"], sdf["close"].isna(), row=1)

    fig.update_layout(height=940, xaxis4_rangeslider_visible=False, xaxis_rangeslider_visible=False, **PLOTLY_LAYOUT)
    fig.update_yaxes(gridcolor=GRID, row=1, col=1)
    fig.update_yaxes(gridcolor=GRID, row=2, col=1)
    fig.update_yaxes(gridcolor=GRID, range=[0, 100], row=3, col=1)
    fig.update_yaxes(gridcolor=GRID, row=4, col=1)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "ADX ≥ 25 (línea punteada gris en el panel RSI) se lee como tendencia con fuerza; "
        "por debajo, mercado sin tendencia clara. ATR (referencia de stops) está en la tarjeta KPI de arriba."
    )

else:
    st.subheader("Comparación de desempeño (base 100) y volumen")
    fig = go.Figure()
    for symbol in selected:
        sdf = df[df["symbol"] == symbol].sort_values("date")
        base = sdf.loc[sdf["close"].notna(), "close"].iloc[0]
        indexed = sdf["close"] / base * 100
        fig.add_trace(go.Scatter(x=sdf["date"], y=indexed, name=symbol, line=dict(color=COLORS.get(symbol), width=2)))

    # Hueco compartido: fechas sin dato para NINGUNO de los activos seleccionados
    # (ver reindex_full_calendar en transform.py) -- una sola línea puede
    # cortarse por su propio hueco, pero esto marca cuándo el pipeline entero
    # se queda sin fuente.
    close_pivot = df.pivot(index="date", columns="symbol", values="close")[selected]
    shared_gap = close_pivot.isna().all(axis=1).reset_index(drop=True)
    add_gap_shading(fig, close_pivot.index.to_series().reset_index(drop=True), shared_gap, row=1)

    fig.update_layout(height=420, yaxis_title="Índice (inicio del rango = 100)", **PLOTLY_LAYOUT)
    fig.update_yaxes(gridcolor=GRID)
    st.plotly_chart(fig, use_container_width=True)

    c1, c2 = st.columns(2)
    with c1:
        st.markdown("**Volumen diario por activo**")
        vol_fig = go.Figure()
        for symbol in selected:
            sdf = df[df["symbol"] == symbol].sort_values("date")
            vol_fig.add_trace(go.Bar(x=sdf["date"], y=sdf["volume"], name=symbol, marker_color=COLORS.get(symbol)))
        vol_fig.update_layout(height=360, barmode="group", **PLOTLY_LAYOUT)
        vol_fig.update_yaxes(gridcolor=GRID)
        st.plotly_chart(vol_fig, use_container_width=True)

    with c2:
        st.markdown("**Correlación de retornos diarios**")
        pivot = df.pivot(index="date", columns="symbol", values="daily_return")[selected]
        corr = pivot.corr()
        heat = go.Figure(
            data=go.Heatmap(
                z=corr.values, x=corr.columns, y=corr.columns, zmin=-1, zmax=1,
                colorscale=[[0, DIVERGING[0]], [0.5, DIVERGING[1]], [1, DIVERGING[2]]],
                text=np.round(corr.values, 2), texttemplate="%{text}",
                colorbar=dict(title="ρ"),
            )
        )
        heat.update_layout(height=360, **PLOTLY_LAYOUT)
        st.plotly_chart(heat, use_container_width=True)

st.divider()

# ---------------------------------------------------------------------------
# Retorno acumulado y tabla de datos
# ---------------------------------------------------------------------------
st.subheader("Retorno acumulado en el período filtrado")
ret_fig = go.Figure()
for symbol in selected:
    sdf = df[df["symbol"] == symbol].sort_values("date")
    cum = (1 + sdf["daily_return"].fillna(0)).cumprod() - 1
    ret_fig.add_trace(go.Scatter(x=sdf["date"], y=cum * 100, name=symbol, line=dict(color=COLORS.get(symbol), width=2)))
ret_fig.add_hline(y=0, line=dict(color=MUTED, width=1))
ret_fig.update_layout(height=320, yaxis_title="Retorno acumulado (%)", **PLOTLY_LAYOUT)
ret_fig.update_yaxes(gridcolor=GRID)
st.plotly_chart(ret_fig, use_container_width=True)

with st.expander("Ver / descargar datos filtrados"):
    st.dataframe(df.sort_values(["symbol", "date"], ascending=[True, False]), use_container_width=True)
    st.download_button(
        "Descargar CSV",
        df.to_csv(index=False).encode("utf-8"),
        file_name="cryptopulse_filtered.csv",
        mime="text/csv",
    )

st.caption(
    f"Fuente histórica: Hugging Face (WinkingFace/CryptoLM-*, hasta {kpi_daily['date'].max().date()}) · "
    f"Fuente en vivo: CoinGecko API (actualización cada ~{REFRESH_SECONDS//60} min de caché)."
)
