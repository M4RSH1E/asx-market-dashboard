import json
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import pandas as pd
import requests
import streamlit as st

st.set_page_config(page_title='ASX Market Dashboard', layout='wide')
API_URL = 'https://migizitech.wixsite.com/asxprices/_functions/getasxprices'
MEMBERSHIP_FILE = Path('membership.json')

st.title('ASX Market Dashboard')
st.caption('ASX Equity Stocks API · Personal research dashboard · Prices may be delayed or inaccurate')

try:
    api_key = st.secrets['ASX_API_KEY']
except (KeyError, FileNotFoundError):
    st.error('Add ASX_API_KEY to Streamlit secrets before continuing.')
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
    def show_table(df, key):
        if df.empty:
            st.warning('No valid prices for this table.')
            return
        display = df[['Code', 'Price', 'Chg', 'ChgPc', 'MCm']].rename(columns={
            'Code':'Ticker', 'Price':'Price (AUD)', 'Chg':'Change (AUD)',
            'ChgPc':'Change (%)', 'MCm':'Market cap (AUD m)'})
        st.dataframe(display, hide_index=True, use_container_width=True,
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
        show_table(top25, 'top25')
    with b:
        st.subheader('Top 10 gainers — Top 300 universe')
        show_table(universe[universe['ChgPc'] > 0].nlargest(10, 'ChgPc'), 'gainers')
    with c:
        st.subheader('Top 10 losers — Top 300 universe')
        show_table(universe[universe['ChgPc'] < 0].nsmallest(10, 'ChgPc'), 'losers')
    st.caption(f'{len(prices)} equities received. Data is indicative, not an official ASX feed. Auto-refresh occurs while the app is active.')

market_view()
