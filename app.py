import json
import hmac
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='ASX Market Dashboard', layout='wide')
API_URL = 'https://migizitech.wixsite.com/asxprices/_functions/getasxprices'
MEMBERSHIP_FILE = Path('membership.json')

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
    response = requests.get(API_URL, params={'apikey': key, 'group': 'core'}, timeout=45)
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
    df['Code'] = df['Code'].astype(str).str.strip().str.upper()
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
        display = df[['Code', 'Price', 'Chg', 'ChgPc', 'MCm']].rename(columns={
            'Code':'Ticker', 'Price':'Price (AUD)', 'Chg':'Change (AUD)',
            'ChgPc':'Change (%)', 'MCm':'Market cap (AUD m)'})
        table_data = (display.style.map(colour_change, subset=['Change (%)'])
                      if key == 'top25' else display)
        st.dataframe(table_data, hide_index=True, use_container_width=True,
                     column_config={'Price (AUD)':st.column_config.NumberColumn(format='$%.3f'),
                                    'Change (AUD)':st.column_config.NumberColumn(format='$%.3f'),
                                    'Change (%)':st.column_config.NumberColumn(format='%.2f%%'),
                                    'Market cap (AUD m)':st.column_config.NumberColumn(format='%.0f')})
        st.download_button('Download CSV', display.to_csv(index=False), file_name=f'{key}.csv', mime='text/csv', key=key)
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
