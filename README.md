# Sentinel QA

A browser-driven QA runner. Give it a URL and a single acceptance criterion; it
explores the application, exercises the relevant flows, and returns a verdict
with screenshots and a downloadable report.

## How it works

- A request turns into a bounded exploration session backed by a headless
  Chromium instance.
- The session reports the actions it took, evidence screenshots, and a final
  verdict against the criterion you gave it.
- Reports are rendered as Markdown, HTML, and PDF.

## Layout

- `qa_agent.py` - the verification loop and its browser tools
- `report.py` - report rendering (markdown / html / pdf)
- `gui/` - FastAPI service plus a React dashboard
- `eval_app/` - a seeded storefront fixture and scoring harness used to measure
  the runner's precision and recall

## Run locally

```bash
pip install -r requirements.txt
python -m playwright install chromium
python gui/server.py            # http://127.0.0.1:8000
```

Configuration lives in `.env` (see `.env.example`). The service picks up a
provider key, base URL, and model from the environment when running elsewhere.

## Deploy to Render (free plan)

1. Push this repository to GitHub.
2. Create a web service from the repo, or use the included `render.yaml`
   blueprint (Docker runtime, free plan, health check on `/api/health`).
3. Add the environment variables:
   - `OPENAI_API_KEY` - provider token (set as a secret)
   - `OPENAI_API_BASE` - provider chat-completions endpoint
   - `OPENAI_MODEL` - model identifier
   - Optional tuning: `QA_PACING`, `QA_MAX_STEPS` (see `.env.example`)

The free instance spins down after 15 minutes of inactivity and wakes on the
next request.

## Evaluation harness

```bash
python eval_app/evaluate.py            # graded run against the seeded app
python eval_app/evaluate.py --mock     # score a canned report, no browser
python eval_app/verify_bugs.py         # confirm seeded defects reproduce
```