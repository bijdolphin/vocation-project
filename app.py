from __future__ import annotations

import csv
import io
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from flask import Flask, Response, g, redirect, render_template, request, session, url_for
from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from openai import APITimeoutError, OpenAI, OpenAIError
from werkzeug.middleware.proxy_fix import ProxyFix

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "tasks.db"
DEFAULT_TIMEZONE = "Australia/Sydney"
GOOGLE_CREDENTIALS_FILE = BASE_DIR / "credentials.json"
GOOGLE_TOKEN_FILE = Path(os.environ.get("GOOGLE_TOKEN_FILE", str(BASE_DIR / "google_token.json")))
GOOGLE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.events",
    "https://www.googleapis.com/auth/tasks",
]


app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev-only-secret-key")
app.config["DATABASE"] = DATABASE
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_port=1)


def load_local_ai_config() -> dict[str, str]:
    key_file = BASE_DIR / "key.md"
    if not key_file.exists():
        return {}

    config: dict[str, str] = {}
    for line in key_file.read_text(encoding="utf-8").splitlines():
        value = line.strip().strip("`").strip()
        if not value or value.startswith("#"):
            continue
        separator = "=" if "=" in value else "：" if "：" in value else ":" if ":" in value else None
        if separator is None:
            if value.startswith("sk-"):
                config["api_key"] = value
            continue
        name, raw_value = value.split(separator, 1)
        normalized_name = name.strip().lower()
        raw_value = raw_value.strip().strip("\"'")
        if normalized_name in {"api_key", "openai_api_key", "deepseek_api_key", "key"}:
            config["api_key"] = raw_value
        elif normalized_name in {"base_url", "openai_base_url", "deepseek_base_url"}:
            config["base_url"] = raw_value
        elif normalized_name in {"model", "ai_model", "openai_model", "deepseek_model"}:
            config["model"] = raw_value
    return config


LOCAL_AI_CONFIG = load_local_ai_config()
AI_MODEL = os.environ.get("AI_MODEL") or os.environ.get("OPENAI_MODEL") or LOCAL_AI_CONFIG.get("model", "deepseek-chat")


def normalize_base_url(base_url: str | None) -> str | None:
    if not base_url:
        return None
    cleaned = base_url.rstrip("/")
    if cleaned.endswith("/v1"):
        return cleaned
    return f"{cleaned}/v1"


AI_BASE_URL = normalize_base_url(
    os.environ.get("AI_BASE_URL")
    or os.environ.get("OPENAI_BASE_URL")
    or LOCAL_AI_CONFIG.get("base_url")
    or "https://tb.api.mkeai.com"
)
AI_API_KEY = os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY") or LOCAL_AI_CONFIG.get("api_key")


def get_db() -> sqlite3.Connection:
    if "db" not in g:
        connection = sqlite3.connect(app.config["DATABASE"])
        connection.row_factory = sqlite3.Row
        g.db = connection
    return g.db


@app.teardown_appcontext
def close_db(_: BaseException | None) -> None:
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db() -> None:
    db = get_db()
    db.executescript(
        """
        CREATE TABLE IF NOT EXISTS calendar_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            summary TEXT NOT NULL,
            description TEXT NOT NULL,
            location TEXT NOT NULL DEFAULT '',
            start_datetime TEXT NOT NULL,
            end_datetime TEXT NOT NULL,
            timezone TEXT NOT NULL,
            status TEXT NOT NULL,
            source_text TEXT NOT NULL,
            confidence REAL NOT NULL,
            google_event_id TEXT,
            imported_at TEXT,
            import_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS google_tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            notes TEXT NOT NULL,
            status TEXT NOT NULL,
            due TEXT,
            source_text TEXT NOT NULL,
            confidence REAL NOT NULL,
            google_task_id TEXT,
            imported_at TEXT,
            import_error TEXT,
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        );
        """
    )
    for table_name, column_name, column_type in (
        ("calendar_events", "google_event_id", "TEXT"),
        ("calendar_events", "imported_at", "TEXT"),
        ("calendar_events", "import_error", "TEXT"),
        ("google_tasks", "google_task_id", "TEXT"),
        ("google_tasks", "imported_at", "TEXT"),
        ("google_tasks", "import_error", "TEXT"),
    ):
        if column_name not in {row["name"] for row in db.execute(f"PRAGMA table_info({table_name})").fetchall()}:
            db.execute(f"ALTER TABLE {table_name} ADD COLUMN {column_name} {column_type}")
    db.commit()


@app.cli.command("init-db")
def init_db_command() -> None:
    init_db()
    print("Initialized the database.")


@app.before_request
def ensure_database() -> None:
    init_db()


def create_ai_client() -> OpenAI:
    if AI_API_KEY and AI_BASE_URL:
        return OpenAI(api_key=AI_API_KEY, base_url=AI_BASE_URL, timeout=20.0)
    if AI_API_KEY:
        return OpenAI(api_key=AI_API_KEY, timeout=20.0)
    if AI_BASE_URL:
        return OpenAI(base_url=AI_BASE_URL, timeout=20.0)
    return OpenAI(timeout=20.0)


def normalize_extracted_items(items: dict[str, Any]) -> dict[str, list[dict[str, Any]]]:
    calendar_events = items.get("calendar_events", [])
    google_tasks = items.get("google_tasks", [])
    if not isinstance(calendar_events, list):
        calendar_events = []
    if not isinstance(google_tasks, list):
        google_tasks = []

    normalized_events: list[dict[str, Any]] = []
    for event in calendar_events:
        if not isinstance(event, dict):
            continue
        start = event.get("start") if isinstance(event.get("start"), dict) else {}
        end = event.get("end") if isinstance(event.get("end"), dict) else {}
        private = {}
        extended = event.get("extendedProperties")
        if isinstance(extended, dict):
            candidate_private = extended.get("private")
            if isinstance(candidate_private, dict):
                private = candidate_private
        normalized_events.append(
            {
                "kind": "calendar#event",
                "summary": str(event.get("summary", "")),
                "description": str(event.get("description", "")),
                "location": str(event.get("location", "")),
                "start": {
                    "dateTime": str(start.get("dateTime", "")),
                    "timeZone": str(start.get("timeZone", DEFAULT_TIMEZONE)),
                },
                "end": {
                    "dateTime": str(end.get("dateTime", "")),
                    "timeZone": str(end.get("timeZone", DEFAULT_TIMEZONE)),
                },
                "status": "confirmed",
                "extendedProperties": {
                    "private": {
                        "source_text": str(private.get("source_text", "")),
                        "confidence": str(private.get("confidence", "0")),
                        "created_by": "deepseek",
                    }
                },
            }
        )

    normalized_tasks: list[dict[str, Any]] = []
    for task in google_tasks:
        if not isinstance(task, dict):
            continue
        due = task.get("due")
        normalized_tasks.append(
            {
                "kind": "tasks#task",
                "title": str(task.get("title", "")),
                "notes": str(task.get("notes", "")),
                "status": "needsAction",
                "due": str(due) if due is not None else None,
                "source_text": str(task.get("source_text", "")),
                "confidence": str(task.get("confidence", "0")),
            }
        )

    return {"calendar_events": normalized_events, "google_tasks": normalized_tasks}


def get_ai_response_content(response: Any) -> str:
    if isinstance(response, str):
        return response
    if isinstance(response, dict):
        choices = response.get("choices", [])
        if choices:
            message = choices[0].get("message", {})
            content = message.get("content")
            if isinstance(content, str):
                return content
    content = response.choices[0].message.content
    if content is None:
        raise ValueError("AI response was empty.")
    return content


def fold_ics_line(line: str) -> str:
    chunks = [line[index : index + 75] for index in range(0, len(line), 75)]
    return "\r\n ".join(chunks)


def escape_ics_text(value: str) -> str:
    return value.replace("\\", "\\\\").replace("\n", "\\n").replace(",", "\\,").replace(";", "\\;")


def parse_event_datetime(value: str, timezone_name: str) -> datetime:
    if not value:
        return datetime.now(timezone.utc)
    normalized = value.replace("Z", "+00:00")
    parsed = datetime.fromisoformat(normalized)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=ZoneInfo(timezone_name or DEFAULT_TIMEZONE))
    return parsed.astimezone(timezone.utc)


def format_ics_datetime(value: str, timezone_name: str) -> str:
    return parse_event_datetime(value, timezone_name).strftime("%Y%m%dT%H%M%SZ")


def build_calendar_ics(events: list[sqlite3.Row]) -> str:
    now_stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    lines = [
        "BEGIN:VCALENDAR",
        "VERSION:2.0",
        "PRODID:-//Vocation Project//AI Message-to-Task//EN",
        "CALSCALE:GREGORIAN",
        "METHOD:PUBLISH",
    ]

    for event in events:
        uid = f"calendar-event-{event['id']}@vocation-project.local"
        description = event["description"] or event["source_text"] or ""
        lines.extend(
            [
                "BEGIN:VEVENT",
                f"UID:{uid}",
                f"DTSTAMP:{now_stamp}",
                f"DTSTART:{format_ics_datetime(event['start_datetime'], event['timezone'])}",
                f"DTEND:{format_ics_datetime(event['end_datetime'], event['timezone'])}",
                f"SUMMARY:{escape_ics_text(event['summary'])}",
                f"DESCRIPTION:{escape_ics_text(description)}",
                f"LOCATION:{escape_ics_text(event['location'])}",
                f"STATUS:{event['status'].upper()}",
                "END:VEVENT",
            ]
        )

    lines.append("END:VCALENDAR")
    return "\r\n".join(fold_ics_line(line) for line in lines) + "\r\n"


def build_tasks_csv(tasks: list[sqlite3.Row]) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Title", "Notes", "Status", "Due", "Source Text", "Confidence"])
    for task in tasks:
        writer.writerow(
            [
                task["title"],
                task["notes"],
                task["status"],
                task["due"] or "",
                task["source_text"],
                task["confidence"],
            ]
        )
    return output.getvalue()


def load_google_credentials() -> Credentials | None:
    if not GOOGLE_TOKEN_FILE.exists():
        return None
    credentials = Credentials.from_authorized_user_file(str(GOOGLE_TOKEN_FILE), GOOGLE_SCOPES)
    if credentials.expired and credentials.refresh_token:
        credentials.refresh(GoogleAuthRequest())
        GOOGLE_TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    if not credentials.valid:
        return None
    return credentials


def load_google_client_config() -> dict[str, Any] | None:
    raw_config = os.environ.get("GOOGLE_CLIENT_CONFIG")
    if raw_config:
        return json.loads(raw_config)

    client_id = os.environ.get("GOOGLE_CLIENT_ID")
    client_secret = os.environ.get("GOOGLE_CLIENT_SECRET")
    if client_id and client_secret:
        return {
            "web": {
                "client_id": client_id,
                "client_secret": client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
                "redirect_uris": [],
            }
        }

    return None


def oauth_callback_scheme() -> str:
    configured_scheme = os.environ.get("OAUTH_SCHEME")
    if configured_scheme:
        return configured_scheme
    if request.host.startswith("127.0.0.1") or request.host.startswith("localhost"):
        return "http"
    return "https"


def create_google_flow() -> Flow:
    redirect_uri = url_for("google_callback", _external=True, _scheme=oauth_callback_scheme())
    client_config = load_google_client_config()
    if client_config is not None:
        return Flow.from_client_config(
            client_config,
            scopes=GOOGLE_SCOPES,
            redirect_uri=redirect_uri,
            autogenerate_code_verifier=False,
        )
    if GOOGLE_CREDENTIALS_FILE.exists():
        return Flow.from_client_secrets_file(
            str(GOOGLE_CREDENTIALS_FILE),
            scopes=GOOGLE_SCOPES,
            redirect_uri=redirect_uri,
            autogenerate_code_verifier=False,
        )
    raise FileNotFoundError("Missing Google OAuth config. Set GOOGLE_CLIENT_CONFIG or add credentials.json.")


def get_google_services() -> tuple[Any, Any] | None:
    credentials = load_google_credentials()
    if credentials is None:
        return None
    calendar_service = build("calendar", "v3", credentials=credentials)
    tasks_service = build("tasks", "v1", credentials=credentials)
    return calendar_service, tasks_service


def google_event_body(event: sqlite3.Row) -> dict[str, Any]:
    return {
        "summary": event["summary"],
        "description": event["description"],
        "location": event["location"],
        "start": {
            "dateTime": event["start_datetime"],
            "timeZone": event["timezone"] or DEFAULT_TIMEZONE,
        },
        "end": {
            "dateTime": event["end_datetime"],
            "timeZone": event["timezone"] or DEFAULT_TIMEZONE,
        },
        "status": event["status"],
        "extendedProperties": {
            "private": {
                "source_text": event["source_text"],
                "confidence": str(event["confidence"]),
                "created_by": "vocation-project",
            }
        },
    }


def google_task_body(task: sqlite3.Row) -> dict[str, Any]:
    body = {
        "title": task["title"],
        "notes": task["notes"],
        "status": task["status"],
    }
    if task["due"]:
        body["due"] = task["due"]
    return body


def get_default_tasklist_id(tasks_service: Any) -> str:
    response = tasks_service.tasklists().list(maxResults=1).execute()
    tasklists = response.get("items", [])
    if not tasklists:
        created = tasks_service.tasklists().insert(body={"title": "AI Message-to-Task"}).execute()
        return created["id"]
    return tasklists[0]["id"]


def mark_calendar_event_imported(event_id: int, google_event_id: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE calendar_events SET google_event_id = ?, imported_at = ?, import_error = NULL WHERE id = ?",
        (google_event_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), event_id),
    )
    db.commit()


def mark_calendar_event_failed(event_id: int, error: str) -> None:
    db = get_db()
    db.execute("UPDATE calendar_events SET import_error = ? WHERE id = ?", (error[:500], event_id))
    db.commit()


def mark_google_task_imported(task_id: int, google_task_id: str) -> None:
    db = get_db()
    db.execute(
        "UPDATE google_tasks SET google_task_id = ?, imported_at = ?, import_error = NULL WHERE id = ?",
        (google_task_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), task_id),
    )
    db.commit()


def mark_google_task_failed(task_id: int, error: str) -> None:
    db = get_db()
    db.execute("UPDATE google_tasks SET import_error = ? WHERE id = ?", (error[:500], task_id))
    db.commit()


def import_calendar_event_to_google(calendar_service: Any, event_id: int) -> bool:
    db = get_db()
    event = db.execute("SELECT * FROM calendar_events WHERE id = ?", (event_id,)).fetchone()
    if event is None or event["google_event_id"]:
        return False
    try:
        created = calendar_service.events().insert(calendarId="primary", body=google_event_body(event)).execute()
    except Exception as exc:
        mark_calendar_event_failed(event_id, str(exc))
        raise
    mark_calendar_event_imported(event_id, created.get("id", ""))
    return True


def import_google_task_to_google(tasks_service: Any, task_id: int) -> bool:
    db = get_db()
    task = db.execute("SELECT * FROM google_tasks WHERE id = ?", (task_id,)).fetchone()
    if task is None or task["google_task_id"]:
        return False
    try:
        tasklist_id = get_default_tasklist_id(tasks_service)
        created = tasks_service.tasks().insert(tasklist=tasklist_id, body=google_task_body(task)).execute()
    except Exception as exc:
        mark_google_task_failed(task_id, str(exc))
        raise
    mark_google_task_imported(task_id, created.get("id", ""))
    return True


def import_saved_items_to_google(calendar_service: Any, tasks_service: Any) -> dict[str, int]:
    db = get_db()
    events = db.execute(
        "SELECT * FROM calendar_events WHERE google_event_id IS NULL ORDER BY start_datetime ASC, id ASC"
    ).fetchall()
    tasks = db.execute(
        "SELECT * FROM google_tasks WHERE google_task_id IS NULL ORDER BY created_at ASC, id ASC"
    ).fetchall()

    imported_events = 0
    for event in events:
        if import_calendar_event_to_google(calendar_service, event["id"]):
            imported_events += 1

    imported_tasks = 0
    for task in tasks:
        if import_google_task_to_google(tasks_service, task["id"]):
            imported_tasks += 1

    return {"calendar_events": imported_events, "google_tasks": imported_tasks}


def extract_items_from_message(message: str) -> dict[str, list[dict[str, Any]]]:
    """Extract calendar events and Google Tasks from free-form text using an OpenAI-compatible API."""
    text = message.strip()
    if not text:
        return {"calendar_events": [], "google_tasks": []}

    now = datetime.now(ZoneInfo(DEFAULT_TIMEZONE))
    client = create_ai_client()
    response = client.chat.completions.create(
        model=AI_MODEL,
        messages=[
            {
                "role": "system",
                "content": (
                    "Extract actionable calendar events and Google Tasks from the user's message. "
                    "Return only items that are clearly implied by the message. "
                    "Return valid JSON only, with no markdown and no explanatory text. "
                    "The JSON object must have exactly these top-level keys: calendar_events and google_tasks. "
                    f"Use {DEFAULT_TIMEZONE} for event time zones unless the user explicitly gives another time zone. "
                    f"The current date and time is {now.isoformat(timespec='seconds')}. "
                    "Resolve relative dates such as today, tomorrow, tonight, and next week from that current date. "
                    "For calendar events, use ISO 8601 date-time strings with timezone offsets when possible. "
                    "For tasks, use a due value like YYYY-MM-DDT00:00:00.000Z when a due date is clearly implied; "
                    "otherwise use null. Put the original message in source_text fields. "
                    "Use confidence as a decimal string between 0 and 1. "
                    "Calendar events must use this shape: "
                    '{"kind":"calendar#event","summary":"","description":"","location":"","start":{"dateTime":"","timeZone":"Australia/Sydney"},"end":{"dateTime":"","timeZone":"Australia/Sydney"},"status":"confirmed","extendedProperties":{"private":{"source_text":"","confidence":"","created_by":"deepseek"}}}. '
                    "Tasks must use this shape: "
                    '{"kind":"tasks#task","title":"","notes":"","status":"needsAction","due":null,"source_text":"","confidence":""}.'
                ),
            },
            {
                "role": "user",
                "content": text,
            },
        ],
        response_format={"type": "json_object"},
    )

    content = get_ai_response_content(response)
    return normalize_extracted_items(json.loads(content))


@app.route("/")
def index():
    return render_template("paste.html")


@app.post("/extract")
def extract():
    message = request.form.get("message", "")
    error = None
    try:
        extracted = extract_items_from_message(message)
    except APITimeoutError:
        extracted = {"calendar_events": [], "google_tasks": []}
        error = "AI extraction timed out. Please try again."
    except OpenAIError as exc:
        extracted = {"calendar_events": [], "google_tasks": []}
        error = f"OpenAI API error: {exc}"
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        extracted = {"calendar_events": [], "google_tasks": []}
        error = f"Could not parse the AI response: {exc}"

    return render_template(
        "review.html",
        message=message,
        calendar_events=extracted["calendar_events"],
        google_tasks=extracted["google_tasks"],
        payload=json.dumps(extracted),
        error=error,
    )


@app.post("/confirm")
def confirm():
    payload = json.loads(request.form.get("payload", "{}"))
    calendar_events = payload.get("calendar_events", [])
    google_tasks = payload.get("google_tasks", [])
    db = get_db()

    for event in calendar_events:
        private = event.get("extendedProperties", {}).get("private", {})
        db.execute(
            """
            INSERT INTO calendar_events (
                summary, description, location, start_datetime, end_datetime,
                timezone, status, source_text, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                event.get("summary", ""),
                event.get("description", ""),
                event.get("location", ""),
                event.get("start", {}).get("dateTime", ""),
                event.get("end", {}).get("dateTime", ""),
                event.get("start", {}).get("timeZone", DEFAULT_TIMEZONE),
                event.get("status", "confirmed"),
                private.get("source_text", ""),
                float(private.get("confidence", 0.0)),
            ),
        )

    for task in google_tasks:
        db.execute(
            """
            INSERT INTO google_tasks (
                title, notes, status, due, source_text, confidence
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                task.get("title", ""),
                task.get("notes", ""),
                task.get("status", "needsAction"),
                task.get("due"),
                task.get("source_text", ""),
                float(task.get("confidence", 0.0)),
            ),
        )

    db.commit()
    return redirect(url_for("items"))


@app.post("/items/calendar/<int:event_id>/delete")
def delete_calendar_event(event_id: int):
    db = get_db()
    db.execute("DELETE FROM calendar_events WHERE id = ?", (event_id,))
    db.commit()
    return redirect(url_for("items", google_message="Deleted saved calendar event."))


@app.post("/items/tasks/<int:task_id>/delete")
def delete_google_task(task_id: int):
    db = get_db()
    db.execute("DELETE FROM google_tasks WHERE id = ?", (task_id,))
    db.commit()
    return redirect(url_for("items", google_message="Deleted saved task."))


@app.post("/items/clear")
def clear_saved_items():
    db = get_db()
    db.execute("DELETE FROM calendar_events")
    db.execute("DELETE FROM google_tasks")
    db.commit()
    return redirect(url_for("items", google_message="Cleared saved items."))


@app.get("/items")
def items():
    db = get_db()
    show_completed = request.args.get("show_completed") == "1"
    if show_completed:
        events = db.execute("SELECT * FROM calendar_events ORDER BY created_at DESC, id DESC").fetchall()
        tasks = db.execute("SELECT * FROM google_tasks ORDER BY created_at DESC, id DESC").fetchall()
    else:
        events = db.execute(
            """
            SELECT * FROM calendar_events
            WHERE google_event_id IS NULL
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
        tasks = db.execute(
            """
            SELECT * FROM google_tasks
            WHERE google_task_id IS NULL
            ORDER BY created_at DESC, id DESC
            """
        ).fetchall()
    google_connected = load_google_credentials() is not None
    return render_template(
        "items.html",
        events=events,
        tasks=tasks,
        show_completed=show_completed,
        google_connected=google_connected,
        google_message=request.args.get("google_message"),
        google_error=request.args.get("google_error"),
    )


@app.get("/google/connect")
def google_connect():
    try:
        flow = create_google_flow()
    except FileNotFoundError as exc:
        return redirect(url_for("items", google_error=str(exc)))
    authorization_url, state = flow.authorization_url(
        access_type="offline",
        include_granted_scopes="true",
        prompt="consent",
    )
    session["google_oauth_state"] = state
    return redirect(authorization_url)


@app.get("/google/callback")
def google_callback():
    state = session.get("google_oauth_state")
    try:
        flow = create_google_flow()
        flow.fetch_token(authorization_response=request.url, state=state)
    except Exception as exc:
        return redirect(url_for("items", google_error=f"Google authorization failed: {exc}"))

    credentials = flow.credentials
    GOOGLE_TOKEN_FILE.write_text(credentials.to_json(), encoding="utf-8")
    return redirect(url_for("items", google_message="Google account connected."))


@app.post("/google/import")
def google_import():
    services = get_google_services()
    if services is None:
        return redirect(url_for("items", google_error="Connect Google before importing."))

    try:
        counts = import_saved_items_to_google(*services)
    except Exception as exc:
        return redirect(url_for("items", google_error=f"Google import failed: {exc}"))

    return redirect(
        url_for(
            "items",
            google_message=(
                f"Imported {counts['calendar_events']} calendar event(s) "
                f"and {counts['google_tasks']} task(s) to Google."
            ),
        )
    )


@app.post("/google/import/calendar/<int:event_id>")
def google_import_calendar_event(event_id: int):
    services = get_google_services()
    if services is None:
        return redirect(url_for("items", google_error="Connect Google before importing."))

    calendar_service, _ = services
    try:
        imported = import_calendar_event_to_google(calendar_service, event_id)
    except Exception as exc:
        return redirect(url_for("items", google_error=f"Calendar event import failed: {exc}"))

    message = "Calendar event added to Google." if imported else "Calendar event was already added or no longer exists."
    return redirect(url_for("items", google_message=message))


@app.post("/google/import/tasks/<int:task_id>")
def google_import_task(task_id: int):
    services = get_google_services()
    if services is None:
        return redirect(url_for("items", google_error="Connect Google before importing."))

    _, tasks_service = services
    try:
        imported = import_google_task_to_google(tasks_service, task_id)
    except Exception as exc:
        return redirect(url_for("items", google_error=f"Task import failed: {exc}"))

    message = "Task added to Google." if imported else "Task was already added or no longer exists."
    return redirect(url_for("items", google_message=message))


@app.get("/export/calendar.ics")
def export_calendar():
    db = get_db()
    events = db.execute("SELECT * FROM calendar_events ORDER BY start_datetime ASC, id ASC").fetchall()
    return Response(
        build_calendar_ics(events),
        mimetype="text/calendar",
        headers={"Content-Disposition": "attachment; filename=calendar-events.ics"},
    )


@app.get("/export/tasks.csv")
def export_tasks():
    db = get_db()
    tasks = db.execute("SELECT * FROM google_tasks ORDER BY created_at DESC, id DESC").fetchall()
    return Response(
        build_tasks_csv(tasks),
        mimetype="text/csv",
        headers={"Content-Disposition": "attachment; filename=google-tasks.csv"},
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", "5000")), debug=os.environ.get("FLASK_DEBUG") == "1")
