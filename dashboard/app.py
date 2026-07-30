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
    hist_last = df[df["symbol"] == symbol].sort_values("date").iloc[-1]
    live_row = kpi_latest[kpi_latest["symbol"] == symbol] if not kpi_latest.empty else pd.DataFrame()

    if not live_row.empty:
        price = live_row["price_usd"].iloc[0]
        chg = live_row["pct_change_24h"].iloc[0]
        dom = live_row["market_cap_dominance_pct"].iloc[0]
    else:
        price = hist_last["close"]
        chg = hist_last["daily_return"] * 100
        dom = np.nan

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
        rows=3, cols=1, shared_xaxes=True, row_heights=[0.55, 0.2, 0.25], vertical_spacing=0.04,
        subplot_titles=("Precio (velas) + medias móviles", "Volumen", "RSI (14)"),
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
    fig.add_hline(y=70, line=dict(color=BAD, width=1, dash="dot"), row=3, col=1)
    fig.add_hline(y=30, line=dict(color=GOOD, width=1, dash="dot"), row=3, col=1)

    fig.update_layout(height=760, xaxis3_rangeslider_visible=False, xaxis_rangeslider_visible=False, **PLOTLY_LAYOUT)
    fig.update_yaxes(gridcolor=GRID, row=1, col=1)
    fig.update_yaxes(gridcolor=GRID, row=2, col=1)
    fig.update_yaxes(gridcolor=GRID, range=[0, 100], row=3, col=1)
    st.plotly_chart(fig, use_container_width=True)

else:
    st.subheader("Comparación de desempeño (base 100) y volumen")
    fig = go.Figure()
    for symbol in selected:
        sdf = df[df["symbol"] == symbol].sort_values("date")
        base = sdf["close"].iloc[0]
        indexed = sdf["close"] / base * 100
        fig.add_trace(go.Scatter(x=sdf["date"], y=indexed, name=symbol, line=dict(color=COLORS.get(symbol), width=2)))
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
