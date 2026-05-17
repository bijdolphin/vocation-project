# Google Calendar and Tasks setup

This app uses Google OAuth, then calls Google Calendar API and Google Tasks API with the saved token.

Flow:

1. User clicks `Connect Google Calendar & Tasks`.
2. Google OAuth opens.
3. User selects an account and clicks Allow.
4. Google redirects back to `/google/callback`.
5. The app exchanges the authorization code for tokens.
6. The app saves `google_token.json`.
7. User clicks `Add Saved Items to Google`.
8. The app creates Calendar events and Tasks through Google APIs.

## Google Cloud setup

1. Create or select a Google Cloud project.
2. Enable these APIs:
   - Google Calendar API
   - Google Tasks API
3. Configure OAuth consent screen.
4. Create OAuth client credentials:
   - Application type: Web application
   - Authorized redirect URI for local development:

```text
http://127.0.0.1:5000/google/callback
```

5. Download the OAuth client JSON and save it as:

```text
credentials.json
```

in this project directory.

`credentials.json` and `google_token.json` are ignored by git.

## Run

```bash
conda activate /home/dministrator/vocation-project/.conda-env
python app.py
```

Open:

```text
http://127.0.0.1:5000/items
```
