from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


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
        return ExecuteRecorder()


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
        return ExecuteRecorder()


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


if __name__ == "__main__":
    unittest.main()
