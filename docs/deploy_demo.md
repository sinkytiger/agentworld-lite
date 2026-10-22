# Deploying the demos (Streamlit Community Cloud)

Both demos run offline from the committed data: no model, no API key and no secrets. Each is one Streamlit app with
its own URL.

| App | Main file | Language | What it shows |
|---|---|---|---|
| Canada desk | `demo/streamlit_app.py` | English | Big 5 banks: run replay, data, objectives check, cited sector note, valuation snapshot, news digest, tasks |
| Korea desk | `demo/streamlit_app_kr.py` | Korean | 4대 금융지주: 실행 재생, 데이터, 밸류업 점검, 섹터 노트, 과제 |

## Steps (about five minutes per app)

1. Sign in at <https://share.streamlit.io> with the GitHub account that owns the repository.
2. **Create app** → **Deploy a public app from GitHub**.
3. Repository `sinkytiger/agentworld-lite`, branch `main`, main file path `demo/streamlit_app.py` (or
   `demo/streamlit_app_kr.py` for the Korean app). Pick a custom subdomain if you like.
4. **Advanced settings**: Python 3.12. Leave secrets empty.
5. **Deploy**. Streamlit installs `demo/requirements.txt` (the file next to the main file: `pyyaml`, `streamlit`) and
   starts the app. The apps add the repository root to the import path themselves, so the package needs no install.

Repeat for the second app. Free apps sleep after a period without visitors and wake on the next visit (a short wait).

## What to expect

- Every view works without network access, except the Canada app's **News digest**, which fetches public RSS feeds and
  Bank of Canada data only when its button is pressed.
- The valuation views use the closing prices stored with the Canadian dataset (with their sources). The Korean desk
  stores no prices, so its valuation view stays hidden by design.
- Query parameters open a given state, for example `?view=밸류업 점검` or `?task=c03_big5_q3_sector_shared&round=9`.

## Checked

The package imports `anthropic`, `pypdf` and `PIL` only for model runs, local PDF sources and GIF recording, none of
which the demos use. Both apps were rendered view by view with Streamlit's `AppTest` while those three were blocked
from importing, so `demo/requirements.txt` (the set Streamlit Community Cloud installs) is enough. Run the same check
before a deploy if the demos change.
