# Contributing

Thanks for considering a contribution.

## Development

Install dependencies:

```bash
conda env create -f environment.yml
conda activate vocation-project
```

Or:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Run the app:

```bash
python app.py
```

Run tests:

```bash
python -m unittest tests.test_google_import tests.test_exports
```

The live AI extraction test requires an API key and may call the configured AI provider:

```bash
python -m unittest tests.test_openai_extraction
```

## Pull requests

Before opening a pull request:

- Keep changes focused.
- Avoid committing local data or secrets.
- Add or update tests for behavior changes.
- Confirm templates render without errors.

## Security

Never commit:

- API keys
- Google OAuth credentials
- Google tokens
- SQLite databases containing user data

If you find a security issue, please avoid opening a public exploit issue. Open a minimal report with reproduction details and no real secrets.
