import json
import hmac
import html
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='ASX Market Dashboard', layout='wide')
API_URL = 'https://migizitech.wixsite.com/asxprices/_functions/getasxprices'
MEMBERSHIP_FILE = Path('membership.json')
NAMES_FILE = Path(__file__).with_name('company_names.csv')

def load_company_names():
    if not NAMES_FILE.exists():
        return {}
    names = pd.read_csv(NAMES_FILE, dtype=str).fillna('')
    if not {'Code', 'CompanyName'}.issubset(names.columns):
        return {}
    return dict(zip(names['Code'].str.strip().str.upper(), names['CompanyName'].str.strip()))

# Authentication must run before any market-data API requests.
try:
    dashboard_password = st.secrets['DASHBOARD_PASSWORD']
except (KeyError, FileNotFoundError):
    st.error('Set DASHBOARD_PASSWORD in Streamlit Secrets before deploying.')
    st.stop()

if not isinstance(dashboard_password, str) or len(dashboard_password) < 12:
    st.error('DASHBOARD_PASSWORD must be a string with at least 12 characters.')
    st.stop()

if not st.session_state.get('authenticated', False):
    st.title('ASX Market Dashboard')
    st.caption('Private dashboard access')
    with st.form('login_form'):
        entered_password = st.text_input('Password', type='password')
        submitted = st.form_submit_button('Sign in')
    if submitted:
        if hmac.compare_digest(entered_password, dashboard_password):
            st.session_state.authenticated = True
            st.rerun()
        else:
            st.error('Incorrect password.')
    st.stop()

header, logout = st.columns([5, 1])
with header:
    st.title('ASX Market Dashboard')
with logout:
    if st.button('Log out'):
        st.session_state.authenticated = False
        st.rerun()
st.caption('ASX Equity Stocks API · Personal research dashboard · Prices may be delayed or inaccurate')

try:
    api_key = st.secrets['ASX_API_KEY']
except (KeyError, FileNotFoundError):
    st.error('Add ASX_API_KEY to Streamlit Secrets before continuing.')
    st.stop()

@st.cache_data(ttl=300, show_spinner='Retrieving ASX prices…')
def get_prices(key):
    response = requests.get(API_URL, params={'apikey': key, 'group': 'core','info'}, timeout=45)
    response.raise_for_status()
    raw = response.json()
    if isinstance(raw, dict):
        raw = raw.get('data', raw.get('results', raw))
    if not isinstance(raw, list):
        raise ValueError('Unexpected API response format; expected a list of equities.')
    df = pd.DataFrame(raw)
    needed = ['Code', 'MCm', 'Price', 'Chg', 'ChgPc']
    missing = [x for x in needed if x not in df.columns]
    if missing:
        raise ValueError('API columns missing: ' + ', '.join(missing))
    # API company-name field can vary by endpoint/version.
    name_fields = ['Name', 'CompanyName', 'Company', 'SecurityName', 'Description', 'name', 'company_name']
    name_field = next((col for col in name_fields if col in df.columns), None)
    if name_field:
        df['CompanyName'] = df[name_field].fillna('').astype(str).str.strip()
    else:
        df['CompanyName'] = ''
    df['Code'] = df['Code'].astype(str).str.strip().str.upper()
    # Company names are not supplied by this API. Join the local CSV lookup.
    lookup = load_company_names()
    df['CompanyName'] = df['Code'].map(lookup).fillna('')
    df.loc[df['CompanyName'].isin(['', 'nan', 'None']), 'CompanyName'] = df['Code']
    for col in ['MCm', 'Price', 'Chg', 'ChgPc']:
        df[col] = pd.to_numeric(df[col].astype(str).str.replace(',', '', regex=False).str.replace('%', '', regex=False), errors='coerce')
    df = df.dropna(subset=['Code', 'MCm']).drop_duplicates('Code')
    return df

def save_membership(df):
    data = {'updated_at': datetime.now(ZoneInfo('Australia/Sydney')).isoformat(),
            'top25': df.nlargest(25, 'MCm')['Code'].tolist(),
            'top300': df.nlargest(300, 'MCm')['Code'].tolist()}
    MEMBERSHIP_FILE.write_text(json.dumps(data, indent=2), encoding='utf-8')
    return data

def load_membership():
    if MEMBERSHIP_FILE.exists():
        try:
            data = json.loads(MEMBERSHIP_FILE.read_text(encoding='utf-8'))
            if len(data.get('top25', [])) == 25 and len(data.get('top300', [])) == 300:
                return data
        except (OSError, ValueError):
            pass
    return None

@st.fragment(run_every='300s')
def market_view():
    if st.button('Refresh prices now'):
        get_prices.clear()
    try:
        prices = get_prices(api_key)
    except (requests.RequestException, ValueError) as exc:
        st.error(f'Unable to retrieve prices: {exc}')
        return
    membership = load_membership()
    missing_names = sum(code not in load_company_names() for code in prices['Code'].head(300))
    if missing_names:
        st.caption(f'Company-name lookup has {len(load_company_names())} entries; tickers without a matching name will show the ticker until the CSV is expanded.')
    if membership is None:
        st.info('No saved company list yet. Select **Refresh Top 25 / Top 300 membership** below to initialise.')
    if st.button('Refresh Top 25 / Top 300 membership', type='primary'):
        if len(prices) < 300:
            st.error(f'Only {len(prices)} companies returned. Membership was not changed.')
        else:
            membership = save_membership(prices)
            st.success('Saved new Top 25 and Top 300 membership lists.')
    if membership is None:
        return
    st.caption('Membership last recalculated: ' + membership['updated_at'] + ' · Prices cache up to 5 minutes. Membership is saved on this server.')
    def colour_change(value):
        """Muted red/green diverging scale, saturating at +/- 5%."""
        if pd.isna(value):
            return ''
        strength = min(abs(float(value)) / 5.0, 1.0)
        neutral = (242, 244, 247)
        target = (22, 101, 52) if value > 0 else (153, 27, 27)
        rgb = tuple(round(a + (b - a) * strength) for a, b in zip(neutral, target))
        foreground = '#ffffff' if strength >= 0.58 else '#182230'
        return f'background-color: rgb{rgb}; color: {foreground}; font-weight: 650;'

    def show_table(df, key):
        if df.empty:
            st.warning('No valid prices for this table.')
            return
        display = df[['CompanyName', 'Code', 'Price', 'Chg', 'ChgPc', 'MCm']].rename(columns={
            'CompanyName': 'Company', 'Code': 'Ticker', 'Price': 'Price (AUD)',
            'Chg': 'Change (AUD)', 'ChgPc': 'Change (%)', 'MCm': 'Market cap (AUD m)'})
        sort_labels = {
            'Market cap (largest first)': ('Market cap (AUD m)', False),
            'Daily gain (highest first)': ('Change (%)', False),
            'Daily loss (lowest first)': ('Change (%)', True),
            'Company name (A–Z)': ('Company', True),
        }
        sort_choice = st.selectbox('Sort companies', list(sort_labels), key=f'sort_{key}',
                                   index=0 if key == 'top25' else (1 if key == 'gainers' else 2))
        sort_col, ascending = sort_labels[sort_choice]
        display = display.sort_values(sort_col, ascending=ascending, kind='stable', na_position='last')
        # Streamlit dataframe cells do not reliably render a second line, so use a
        # compact HTML table with escaped text for the company/ticker presentation.
        styles = """<style>
        .asx-wrap{overflow-x:auto}.asx-table{width:100%;border-collapse:collapse;font-size:0.92rem}
        .asx-table th{text-align:right;color:var(--text-color);opacity:.7;font-size:.79rem;
          font-weight:600;padding:10px 12px;border-bottom:1px solid rgba(128,128,128,.3)}
        .asx-table th:first-child,.asx-table td:first-child{text-align:left}
        .asx-table td{padding:9px 12px;text-align:right;border-bottom:1px solid rgba(128,128,128,.18)}
        .asx-name{font-weight:650;white-space:normal}.asx-code{font-size:.77rem;opacity:.65;margin-top:2px}
        .asx-change{display:inline-block;border-radius:6px;padding:6px 9px;min-width:70px;text-align:center}
        </style>"""
        def money(v, digits=2):
            return '—' if pd.isna(v) else f'${v:,.{digits}f}'
        def percent(v):
            return '—' if pd.isna(v) else f'{v:+.2f}%'
        rows = []
        for _, r in display.iterrows():
            company = html.escape(str(r['Company']))
            ticker = html.escape(str(r['Ticker']))
            pct = r['Change (%)']
            style = colour_change(pct) if key == 'top25' else (
                'color:#2baf70;font-weight:650' if pd.notna(pct) and pct > 0 else
                'color:#d85b5b;font-weight:650' if pd.notna(pct) and pct < 0 else '')
            cap = '—' if pd.isna(r['Market cap (AUD m)']) else f"{r['Market cap (AUD m)']:,.0f}"
            rows.append(f'<tr><td><div class="asx-name">{company}</div>'
                        f'<div class="asx-code">{ticker}</div></td>'
                        f'<td>{money(r["Price (AUD)"], 3)}</td>'
                        f'<td>{money(r["Change (AUD)"], 3)}</td>'
                        f'<td><span class="asx-change" style="{style}">{percent(pct)}</span></td>'
                        f'<td>{cap}</td></tr>')
        headers = '<tr><th>Company</th><th>Price (AUD)</th><th>Change (AUD)</th><th>Today</th><th>Market cap (AUD m)</th></tr>'
        st.html(styles + '<div class="asx-wrap"><table class="asx-table"><thead>' + headers +
                '</thead><tbody>' + ''.join(rows) + '</tbody></table></div>')
        st.download_button('Download CSV', display.to_csv(index=False),
                           file_name=f'{key}.csv', mime='text/csv', key=key)
    top25 = prices[prices['Code'].isin(membership['top25'])].copy()
    top25['order'] = pd.Categorical(top25['Code'], categories=membership['top25'], ordered=True)
    top25 = top25.sort_values('order')
    universe = prices[prices['Code'].isin(membership['top300'])].dropna(subset=['Price', 'ChgPc'])
    a, b, c = st.tabs(['Top 25', 'Top 10 gainers', 'Top 10 losers'])
    with a:
        st.subheader('Top 25 by market capitalisation')
        st.caption('Colour intensity reflects daily percentage movement, capped at ±5%.')
        show_table(top25, 'top25')
    with b:
        st.subheader('Top 10 gainers — Top 300 universe')
        show_table(universe[universe['ChgPc'] > 0].nlargest(10, 'ChgPc'), 'gainers')
    with c:
        st.subheader('Top 10 losers — Top 300 universe')
        show_table(universe[universe['ChgPc'] < 0].nsmallest(10, 'ChgPc'), 'losers')
    st.caption(f'{len(prices)} equities received. Data is indicative, not an official ASX feed. Auto-refresh occurs while the app is active.')

market_view()
