# ASX Market Dashboard

Streamlit personal dashboard powered by ASX Equity Stocks API.

## GitHub setup
1. Create a new private GitHub repository.
2. Upload `app.py` and `requirements.txt` (and optionally this README).
3. Deploy the repo at https://share.streamlit.io using `app.py` as the main file.
4. In the Streamlit app settings, open Secrets and enter:

```toml
ASX_API_KEY = "YOUR_PERSONAL_API_KEY"
```

5. Save and restart the app, then click **Refresh Top 25 / Top 300 membership**.

## Dashboard behavior
- All ASX equities fetched from the documented batch endpoint, with a 5-minute cache.
- Top 25 membership and top 300 membership are recalculated only when you click the membership refresh button.
- Top 10 gainers and losers are ranked by daily percentage change within the saved top 300.
- Market-cap ranking uses `MCm`; this is **not** official S&P/ASX 300 index membership.
- Automatic refresh runs while the page is active; a sleeping free app won't collect data in the background.

## Important caveats
- **Membership is stored in `membership.json` on the Streamlit server.** Free Streamlit hosting storage is ephemeral: after a restart or redeploy you may need to click the membership refresh button again. Permanent storage can be added later.
- The provider says the API can occasionally fail and its data is indicative.
- The endpoint and JSON shape are based on provider documentation and have **not yet been tested with your paid key**. If the first request fails, inspect the error without exposing your API key.
- A private GitHub repository does not by itself make the deployed Streamlit app private; check Streamlit sharing settings.
- Keep your API key in Streamlit Secrets, never in GitHub.
