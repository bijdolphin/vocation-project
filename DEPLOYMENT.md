# Deployment

This app is ready to deploy to a Python web host such as Render, Railway, Fly.io, or Heroku-style platforms.

The production entrypoint is:

```bash
gunicorn app:app --bind 0.0.0.0:$PORT
```

## Required environment variables

```text
SECRET_KEY=<random-long-secret>
AI_API_KEY=<your AI gateway key>
AI_BASE_URL=https://tb.api.mkeai.com
AI_MODEL=deepseek-chat
GOOGLE_CLIENT_ID=<Google OAuth web client id>
GOOGLE_CLIENT_SECRET=<Google OAuth web client secret>
OAUTH_SCHEME=https
```

For local development, `credentials.json` is still supported. For deployment, prefer `GOOGLE_CLIENT_ID` and `GOOGLE_CLIENT_SECRET`.

## Google OAuth redirect URL

After deployment, add this redirect URI to your Google OAuth web client:

```text
https://YOUR_DEPLOYED_DOMAIN/google/callback
```

Examples:

```text
https://vocation-project.onrender.com/google/callback
https://your-app.up.railway.app/google/callback
```

The app also supports local development redirect URIs:

```text
http://127.0.0.1:5000/google/callback
http://localhost:5000/google/callback
```

## Render

This repository includes `render.yaml`.

1. Push the project to GitHub.
2. In Render, create a new Blueprint from the repo.
3. Fill these secret environment variables:
   - `AI_API_KEY`
   - `GOOGLE_CLIENT_ID`
   - `GOOGLE_CLIENT_SECRET`
4. Deploy.
5. Copy the Render URL and add `https://.../google/callback` to Google OAuth redirect URIs.

## Data storage note

The app currently uses SQLite at `tasks.db`. On many free hosting platforms, local filesystem storage can be ephemeral. For reliable long-term production use, move saved items and Google tokens to a managed database.
