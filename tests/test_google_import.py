from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class ExecuteRecorder:
    def __init__(self, response: dict | None = None) -> None:
        self.response = response or {}

    def execute(self) -> dict:
        return self.response


class FakeCalendarEvents:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    def insert(self, calendarId: str, body: dict) -> ExecuteRecorder:
        self.inserted.append({"calendarId": calendarId, "body": body})
        return ExecuteRecorder({"id": "google-event-1"})


class FakeCalendarService:
    def __init__(self) -> None:
        self.fake_events = FakeCalendarEvents()

    def events(self) -> FakeCalendarEvents:
        return self.fake_events


class FakeTaskLists:
    def list(self, maxResults: int) -> ExecuteRecorder:
        return ExecuteRecorder({"items": [{"id": "default-list"}]})


class FakeTasks:
    def __init__(self) -> None:
        self.inserted: list[dict] = []

    def insert(self, tasklist: str, body: dict) -> ExecuteRecorder:
        self.inserted.append({"tasklist": tasklist, "body": body})
        return ExecuteRecorder({"id": "google-task-1"})


class FakeTasksService:
    def __init__(self) -> None:
        self.fake_tasks = FakeTasks()

    def tasklists(self) -> FakeTaskLists:
        return FakeTaskLists()

    def tasks(self) -> FakeTasks:
        return self.fake_tasks


class GoogleImportTest(unittest.TestCase):
    def setUp(self) -> None:
        import app

        self.app_module = app
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        app.app.config["DATABASE"] = self.db_path
        app.app.config["TESTING"] = True

        with app.app.app_context():
            app.init_db()
            db = app.get_db()
            db.execute(
                """
                INSERT INTO calendar_events (
                    summary, description, location, start_datetime, end_datetime,
                    timezone, status, source_text, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "Project meeting",
                    "Discuss timeline",
                    "Library",
                    "2026-05-20T15:00:00+10:00",
                    "2026-05-20T16:00:00+10:00",
                    "Australia/Sydney",
                    "confirmed",
                    "project meeting",
                    0.9,
                ),
            )
            db.execute(
                """
                INSERT INTO google_tasks (
                    title, notes, status, due, source_text, confidence
                )
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    "Submit report",
                    "Upload final report",
                    "needsAction",
                    "2026-05-22T00:00:00.000Z",
                    "submit report",
                    0.8,
                ),
            )
            db.commit()

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_imports_saved_items_to_google_services(self) -> None:
        app = self.app_module
        calendar_service = FakeCalendarService()
        tasks_service = FakeTasksService()

        with app.app.app_context():
            counts = app.import_saved_items_to_google(calendar_service, tasks_service)

        self.assertEqual(counts, {"calendar_events": 1, "google_tasks": 1})
        self.assertEqual(calendar_service.fake_events.inserted[0]["calendarId"], "primary")
        self.assertEqual(calendar_service.fake_events.inserted[0]["body"]["summary"], "Project meeting")
        self.assertEqual(tasks_service.fake_tasks.inserted[0]["tasklist"], "default-list")
        self.assertEqual(tasks_service.fake_tasks.inserted[0]["body"]["title"], "Submit report")

        with app.app.app_context():
            db = app.get_db()
            event = db.execute("SELECT * FROM calendar_events").fetchone()
            task = db.execute("SELECT * FROM google_tasks").fetchone()
            self.assertEqual(event["google_event_id"], "google-event-1")
            self.assertEqual(task["google_task_id"], "google-task-1")

            second_counts = app.import_saved_items_to_google(calendar_service, tasks_service)
            self.assertEqual(second_counts, {"calendar_events": 0, "google_tasks": 0})

    def test_deletes_saved_items(self) -> None:
        app = self.app_module
        client = app.app.test_client()

        response = client.post("/items/calendar/1/delete")
        self.assertEqual(response.status_code, 302)

        response = client.post("/items/tasks/1/delete")
        self.assertEqual(response.status_code, 302)

        with app.app.app_context():
            db = app.get_db()
            event_count = db.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0]
            task_count = db.execute("SELECT COUNT(*) FROM google_tasks").fetchone()[0]
        self.assertEqual(event_count, 0)
        self.assertEqual(task_count, 0)

    def test_single_item_import_routes(self) -> None:
        app = self.app_module
        calendar_service = FakeCalendarService()
        tasks_service = FakeTasksService()
        client = app.app.test_client()

        with patch.object(app, "get_google_services", return_value=(calendar_service, tasks_service)):
            event_response = client.post("/google/import/calendar/1")
            task_response = client.post("/google/import/tasks/1")

        self.assertEqual(event_response.status_code, 302)
        self.assertEqual(task_response.status_code, 302)
        self.assertEqual(len(calendar_service.fake_events.inserted), 1)
        self.assertEqual(len(tasks_service.fake_tasks.inserted), 1)

        with app.app.app_context():
            db = app.get_db()
            event = db.execute("SELECT * FROM calendar_events").fetchone()
            task = db.execute("SELECT * FROM google_tasks").fetchone()
            self.assertEqual(event["google_event_id"], "google-event-1")
            self.assertEqual(task["google_task_id"], "google-task-1")

    def test_items_page_hides_completed_by_default(self) -> None:
        app = self.app_module
        client = app.app.test_client()

        with app.app.app_context():
            db = app.get_db()
            db.execute("UPDATE calendar_events SET google_event_id = 'google-event-1'")
            db.execute("UPDATE google_tasks SET google_task_id = 'google-task-1'")
            db.commit()

        response = client.get("/items")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("No pending calendar events.", body)
        self.assertIn("No pending tasks.", body)
        self.assertNotIn("Project meeting", body)
        self.assertNotIn("Submit report", body)

        response = client.get("/items?show_completed=1")
        body = response.get_data(as_text=True)
        self.assertEqual(response.status_code, 200)
        self.assertIn("Project meeting", body)
        self.assertIn("Submit report", body)


if __name__ == "__main__":
    unittest.main()
