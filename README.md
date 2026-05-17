# AI Message-to-Task

Turn messy messages into Google-ready calendar events and tasks.

AI Message-to-Task is a small Flask app that extracts actionable items from free-form text, lets you review them, and then sends selected items to Google Calendar or Google Tasks through Google OAuth.

Live app:

```text
https://vocation-project.onrender.com
```

## What it does

- Extracts calendar events and tasks from chat messages, emails, course notices, or meeting notes.
- Resolves relative dates like today, tomorrow, and next week using the app timezone.
- Lets users review extracted items before saving them.
- Connects to Google Calendar and Google Tasks with OAuth.
- Supports per-item Google import.
- Shows each item's status:
  - `Pending`
  - `Added to Google`
  - `Failed`
- Hides completed imports from the default pending list.
- Supports deleting local saved items.

## Example

Input:

```text
i have a meeting at 3pm tomorrow and need to submit the report Friday
```

The app can extract:

- a Google Calendar event for the meeting
- a Google Task for the report

You can then add each item to Google individually.

## Tech stack

- Flask
- SQLite
- OpenAI-compatible chat completions API
- Google Calendar API
- Google Tasks API
- Google OAuth
- Gunicorn for production deployment

## Local setup

Create the conda environment:

```bash
conda env create -f environment.yml
conda activate vocation-project
```

Or use pip:

```bash
python -m venv .venv
. .venv/bin/activate
pip install -r requirements.txt
```

Create a local config file or export environment variables.

Using environment variables:

```bash
export SECRET_KEY="change-me"
export AI_API_KEY="your-ai-api-key"
export AI_BASE_URL="https://tb.api.mkeai.com"
export AI_MODEL="deepseek-chat"
```

Run:

```bash
python app.py
```

Open:

```text
http://127.0.0.1:5000
```

## Google OAuth setup

To import items into Google Calendar or Google Tasks, create a Google OAuth web client.

Enable these APIs in Google Cloud:

- Google Calendar API
- Google Tasks API

For local development, add these authorized redirect URIs:

```text
http://127.0.0.1:5000/google/callback
http://localhost:5000/google/callback
```

For deployment, add:

```text
https://YOUR_DEPLOYED_DOMAIN/google/callback
```

For local development, you can download the OAuth client JSON from Google and save it as:

```text
credentials.json
```

For deployment, prefer environment variables:

```text
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
```

See [GOOGLE_SETUP.md](GOOGLE_SETUP.md) for more detail.

## Deployment

This repo includes:

- `Procfile`
- `render.yaml`
- `DEPLOYMENT.md`

Production command:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT
```

Required production environment variables:

```text
SECRET_KEY=
AI_API_KEY=
AI_BASE_URL=https://tb.api.mkeai.com
AI_MODEL=deepseek-chat
GOOGLE_CLIENT_ID=
GOOGLE_CLIENT_SECRET=
OAUTH_SCHEME=https
```

See [DEPLOYMENT.md](DEPLOYMENT.md).

## Privacy and security

This app processes user-provided messages and sends them to the configured AI API provider for extraction.

The app stores extracted local items in SQLite. When Google is connected, OAuth credentials are stored so the app can call Google Calendar and Google Tasks APIs.

Do not commit secrets or local data:

- `key.md`
- `.env`
- `tasks.db`
- `credentials.json`
- `google_token.json`

If you publicly deploy this app for multiple users, move token storage from local files to a user-scoped database model.

## Roadmap

- Edit extracted items before saving.
- Better multi-user account support.
- Store Google tokens per user.
- Add recurring events.
- Add duplicate detection against existing Google Calendar events and Tasks.
- Add privacy and terms pages for Google verification.

## Contributing

Issues and pull requests are welcome. See [CONTRIBUTING.md](CONTRIBUTING.md).

## License

MIT. See [LICENSE](LICENSE).
