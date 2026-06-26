# Streamlit Community Cloud deployment

AeroOps is prepared for deployment from:

- **Repository:** `gbkeku/aeroops-ai`
- **Repository URL:** https://github.com/gbkeku/aeroops-ai
- **Branch:** `main`
- **Entrypoint:** `src/aeroops/app.py`
- **Python:** 3.11
- **Dependency source:** `uv.lock`
- **Public application URL:** Pending deployment and verification

Do not add a public-demo link to the README until the assigned URL has been tested in a private browser session.

## Recommended public offline deployment

Add these root-level Streamlit secrets:

```toml
AEROOPS_OFFLINE_DEMO = "1"
AEROOPS_MODEL = "gemini-2.5-flash"
AEROOPS_DB_PATH = "data/aeroops.db"
```

No API key is required. Offline mode displays its banner and uses deterministic fixtures without starting Gemini, ADK, MCP, or SQLite.

## Optional credential-backed deployment

```toml
AEROOPS_OFFLINE_DEMO = "0"
AEROOPS_MODEL = "gemini-2.5-flash"
AEROOPS_DB_PATH = "data/aeroops.db"
GOOGLE_API_KEY = "set-this-only-in-streamlit-cloud-secrets"
```

Never commit `.streamlit/secrets.toml`, `.env`, or a screenshot of the secret field.

## Deployment steps

1. Push the reviewed project to `https://github.com/gbkeku/aeroops-ai`.
2. Create a Streamlit Community Cloud app.
3. Choose repository `gbkeku/aeroops-ai`, branch `main`, and entrypoint `src/aeroops/app.py`.
4. Select Python 3.11 where available.
5. Add the offline or live secret configuration.
6. Deploy and record the URL only after verification.

## Public verification

Confirm no login is required, synthetic-data and decision-support notices are visible, offline mode shows its banner, the fleet contains exactly four aircraft, AC-009 renders the six-day result and exact evidence, graph and tables render, unsafe requests are rejected, no internal detail is disclosed, and the narrow layout remains usable.

After verification, add the actual URL and Streamlit badge to the README and Kaggle submission package.
