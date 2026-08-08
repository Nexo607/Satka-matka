# NEXO Historical Analytics

A free-hostable Flask dashboard for fetching and analyzing publicly accessible historical result pages.

## Run locally

```bash
python -m venv .venv
# Linux/macOS:
source .venv/bin/activate
# Windows:
# .venv\Scripts\activate

pip install -r requirements.txt
python app.py
```

Open http://127.0.0.1:10000

## Free hosting: Render

1. Put this folder in a GitHub repository.
2. Open Render and create a **Web Service** from the repository.
3. Choose the Free instance.
4. Render can use `render.yaml`, or enter:
   - Build: `pip install -r requirements.txt`
   - Start: `gunicorn app:app`
5. Deploy.

Free Render web services can spin down after inactivity, so the first request after idle can take about a minute.

## Important

The parser is intentionally conservative and extracts historical 3-digit tokens from the fetched page. If the source changes its HTML structure, the parser must be updated.

This project provides historical/descriptive statistics only. It does not claim to predict or guarantee a future gambling outcome.
