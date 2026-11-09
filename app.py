import hmac
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st
from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

st.set_page_config(page_title="ASX Market Dashboard", layout="wide")
API_URL = "https://migizitech.wixsite.com/asxprices/_functions/getasxprices"
MEMBERSHIP_FILE = Path(__file__).with_name("membership.json")
NAMES_FILE = Path(__file__).with_name("company_names.csv")

st.markdown("""<style>
.block-container {padding-top: 2rem; max-width: 1550px}
[data-testid="stMetric"] {border-radius: 10px}
</style>""", unsafe_allow_html=True)


def load_company_names():
    if not NAMES_FILE.exists():
        return {}
    try:
        names = pd.read_csv(NAMES_FILE, dtype=str).fillna("")
        if not {"Code", "CompanyName"}.issubset(names.columns):
            return {}
        return dict(zip(names["Code"].str.strip().str.upper(),
                        names["CompanyName"].str.strip()))
    except (OSError, pd.errors.ParserError):
        return {}


# Gate all API requests behind the login screen.
try:
    dashboard_password = st.secrets["DASHBOARD_PASSWORD"]
except (KeyError, FileNotFoundError):
    st.error("Set DASHBOARD_PASSWORD in Streamlit Secrets.")
    st.stop()
if not isinstance(dashboard_password, str) or len(dashboard_password) < 12:
    st.error("DASHBOARD_PASSWORD must contain at least 12 characters.")
    st.stop()
if not st.session_state.get("authenticated", False):
    st.title("ASX Market Dashboard")
    st.caption("Private dashboard access")
    with st.form("login_form"):
        password = st.text_input("Password", type="password")
        submitted = st.form_submit_button("Sign in")
    if submitted:
        if hmac.compare_digest(password, dashboard_password):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error("Incorrect password.")
    st.stop()

heading, logout = st.columns([6, 1])
with heading:
    st.title("ASX Market Dashboard")
with logout:
    if st.button("Log out"):
        st.session_state.authenticated = False
        st.rerun()
st.caption("ASX Equity Stocks API · Personal research dashboard · Prices may be delayed or inaccurate")

try:
    api_key = st.secrets["ASX_API_KEY"]
except (KeyError, FileNotFoundError):
    st.error("Add ASX_API_KEY to Streamlit Secrets.")
    st.stop()


@st.cache_data(ttl=300, show_spinner="Retrieving ASX prices…")
def get_prices(key):
    # Explicit columns are necessary: the core,info group does not always return MCm.
    columns = "Code,MCm,Price,Chg,ChgPc,Name,Ind,TrdVol,PriceTS"
    response = requests.get(API_URL, params={"apikey": key, "col": columns}, timeout=45)
    response.raise_for_status()
    raw = response.json()
    if isinstance(raw, dict):
        raw = raw.get("data", raw.get("results", raw))
    if not isinstance(raw, list):
        raise ValueError("Unexpected API response format (expected an array of stocks).")
    df = pd.DataFrame(raw)
    required = ["Code", "MCm", "Price", "Chg", "ChgPc"]
    missing = [col for col in required if col not in df.columns]
    if missing:
        raise ValueError("API columns missing: " + ", ".join(missing))
    df["Code"] = df["Code"].fillna("").astype(str).str.strip().str.upper()
    # Use API-provided company name; local CSV only as fallback.
    api_names = df["Name"].fillna("").astype(str).str.strip() if "Name" in df else pd.Series("", index=df.index)
    csv_names = df["Code"].map(load_company_names()).fillna("")
    df["CompanyName"] = api_names.where(~api_names.isin(["", "nan", "None"]), csv_names)
    df["CompanyName"] = df["CompanyName"].where(df["CompanyName"].ne(""), df["Code"])
    df["Industry"] = df["Ind"].fillna("").astype(str) if "Ind" in df else ""
    for col in ["MCm", "Price", "Chg", "ChgPc", "TrdVol"]:
        if col not in df:
            df[col] = float("nan")
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(",", "", regex=False).str.replace("%", "", regex=False), errors="coerce")
    df = df[(df["Code"] != "")].dropna(subset=["MCm"]).drop_duplicates("Code")
    return df


def save_membership(df):
    data = {
        "updated_at": datetime.now(ZoneInfo("Australia/Brisbane")).isoformat(),
        "top25": df.nlargest(25, "MCm")["Code"].tolist(),
        "top300": df.nlargest(300, "MCm")["Code"].tolist(),
    }
    MEMBERSHIP_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    return data


def load_membership():
    if MEMBERSHIP_FILE.exists():
        try:
            data = json.loads(MEMBERSHIP_FILE.read_text(encoding="utf-8"))
            if len(data.get("top25", [])) == 25 and len(data.get("top300", [])) == 300:
                return data
        except (OSError, ValueError, TypeError):
            pass
    return None


# AG Grid displays rich two-line company cells while preserving real numeric sorting.
company_renderer = JsCode("""
class CompanyRenderer {
  init(params) {
    this.eGui = document.createElement('div');
    this.eGui.style.cssText = 'display:flex;flex-direction:column;justify-content:center;height:100%;line-height:1.35;';
    const ticker = document.createElement('div');
    ticker.style.cssText = 'font-weight:700;color:#4a90e2;font-size:14px;';
    ticker.textContent = params.data.Code || '';
    const name = document.createElement('div');
    name.style.cssText = 'font-size:12px;opacity:.82;white-space:normal;';
    name.textContent = params.data.CompanyName || '';
    this.eGui.appendChild(ticker);
    if (name.textContent && name.textContent !== ticker.textContent) this.eGui.appendChild(name);
  }
  getGui() { return this.eGui; }
  refresh() { return false; }
}
""")

change_style = JsCode("""
function(params) {
  if (params.value == null || isNaN(params.value)) return {};
  const v = Number(params.value);
  return {color: v > 0 ? '#20b77b' : v < 0 ? '#f16b77' : 'inherit', fontWeight: '650'};
}
""")

heat_style = JsCode("""
function(params) {
  if (params.value == null || isNaN(params.value)) return {};
  const v = Number(params.value);
  const intensity = Math.min(Math.abs(v) / 5, 1);
  const neutral = [242,244,247];
  const target = v > 0 ? [22,101,52] : v < 0 ? [153,27,27] : neutral;
  const rgb = neutral.map((n,i) => Math.round(n + (target[i]-n)*intensity));
  return {backgroundColor:`rgb(${rgb.join(',')})`, color:intensity >= .58 ? '#fff':'#182230', fontWeight:'700'};
}
""")


def show_table(data, key, heatmap=False):
    if data.empty:
        st.warning("No valid stocks to display.")
        return
    view = data[["Code", "CompanyName", "Industry", "Price", "Chg", "ChgPc", "TrdVol", "MCm"]].copy()
    gb = GridOptionsBuilder.from_dataframe(view)
    gb.configure_default_column(sortable=True, resizable=True, filter=False, suppressMovable=True)
    gb.configure_column("Code", header_name="CODE / COMPANY NAME", cellRenderer=company_renderer,
                        width=295, minWidth=220, pinned="left", sort=None)
    gb.configure_column("CompanyName", hide=True)
    gb.configure_column("Industry", header_name="INDUSTRY", width=200, minWidth=135, wrapText=True)
    gb.configure_column("Price", header_name="LAST PRICE", width=120, type=["numericColumn"],
                        valueFormatter="value == null ? '—' : Number(value).toFixed(3)")
    gb.configure_column("Chg", header_name="$ CHANGE", width=125, type=["numericColumn"],
                        valueFormatter="value == null ? '—' : (value > 0 ? '+' : '') + Number(value).toFixed(3)", cellStyle=change_style)
    gb.configure_column("ChgPc", header_name="% CHANGE", width=125, type=["numericColumn"],
                        valueFormatter="value == null ? '—' : (value > 0 ? '+' : '') + Number(value).toFixed(2) + '%'",
                        cellStyle=heat_style if heatmap else change_style)
    gb.configure_column("TrdVol", header_name="VOLUME", width=120, type=["numericColumn"],
                        valueFormatter="value == null ? '—' : (Math.abs(value) >= 1e6 ? (value/1e6).toFixed(2)+'M' : Math.abs(value) >= 1e3 ? (value/1e3).toFixed(1)+'K' : Number(value).toLocaleString())")
    gb.configure_column("MCm", header_name="MARKET CAP", width=140, type=["numericColumn"],
                        valueFormatter="value == null ? '—' : (Math.abs(value) >= 1000 ? (value/1000).toFixed(2)+'B' : Number(value).toFixed(0)+'M')")
    opts = gb.build()
    opts["rowHeight"] = 64
    opts["headerHeight"] = 46
    opts["domLayout"] = "normal"
    opts["suppressCellFocus"] = True
    opts["ensureDomOrder"] = True
    # Start with market-cap order for the Top 25 and daily performance for gainers/losers.
    sort_field = "MCm" if key == "top25" else "ChgPc"
    opts["columnDefs"] = [dict(c, sort=("asc" if key == "losers" else "desc") if c.get("field") == sort_field else None)
                          for c in opts["columnDefs"]]
    height = min(750, 46 + len(view) * 64 + 12)
    AgGrid(view, gridOptions=opts, height=height, theme="streamlit", allow_unsafe_jscode=True,
           fit_columns_on_grid_load=False, key=f"grid_{key}")
    st.download_button("Download CSV", view.to_csv(index=False), file_name=f"{key}.csv",
                       mime="text/csv", key=f"download_{key}")


@st.fragment(run_every="300s")
def market_view():
    if st.button("Refresh prices now"):
        get_prices.clear()
    try:
        prices = get_prices(api_key)
    except (requests.RequestException, ValueError) as exc:
        st.error(f"Unable to retrieve prices: {exc}")
        return
    membership = load_membership()
    if membership is None:
        st.info("No saved company list yet. Use the membership refresh button below to initialise.")
    if st.button("Refresh Top 25 / Top 300 membership", type="primary"):
        if len(prices) < 300:
            st.error(f"Only {len(prices)} companies returned. Membership not changed.")
        else:
            try:
                membership = save_membership(prices)
                st.success("Saved Top 25 and Top 300 membership lists.")
            except OSError as exc:
                st.error(f"Unable to save membership: {exc}")
    if membership is None:
        return
    st.caption("Membership last recalculated: " + membership["updated_at"] +
               " · Price cache: 5 minutes · Click column headings to sort")
    top25 = prices[prices["Code"].isin(membership["top25"])].copy()
    universe = prices[prices["Code"].isin(membership["top300"])].dropna(subset=["Price", "ChgPc"])
    tabs = st.tabs(["Top 25", "Top 10 gainers", "Top 10 losers"])
    with tabs[0]:
        st.subheader("Top 25 by market capitalisation")
        st.caption("Daily % change uses a red-to-green heatmap, capped at ±5%.")
        show_table(top25, "top25", heatmap=True)
    with tabs[1]:
        st.subheader("Top 10 gainers — Top 300 universe")
        show_table(universe[universe["ChgPc"] > 0].nlargest(10, "ChgPc"), "gainers")
    with tabs[2]:
        st.subheader("Top 10 losers — Top 300 universe")
        show_table(universe[universe["ChgPc"] < 0].nsmallest(10, "ChgPc"), "losers")
    st.caption(f"{len(prices)} equities received · Data is indicative, not an official ASX feed · Auto-refresh while active")


market_view()
