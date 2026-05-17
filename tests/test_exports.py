from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


class ExportRoutesTest(unittest.TestCase):
    def setUp(self) -> None:
        import app

        self.app_module = app
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test.db"
        app.app.config["DATABASE"] = self.db_path
        app.app.config["TESTING"] = True
        self.client = app.app.test_client()

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
                    "Discuss project timeline",
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

    def test_exports_calendar_ics(self) -> None:
        response = self.client.get("/export/calendar.ics")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/calendar")
        body = response.get_data(as_text=True)
        self.assertIn("BEGIN:VCALENDAR", body)
        self.assertIn("BEGIN:VEVENT", body)
        self.assertIn("SUMMARY:Project meeting", body)
        self.assertIn("DTSTART:20260520T050000Z", body)
        self.assertIn("DTEND:20260520T060000Z", body)

    def test_exports_tasks_csv(self) -> None:
        response = self.client.get("/export/tasks.csv")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.mimetype, "text/csv")
        body = response.get_data(as_text=True)
        self.assertIn("Title,Notes,Status,Due,Source Text,Confidence", body)
        self.assertIn("Submit report,Upload final report,needsAction,2026-05-22T00:00:00.000Z", body)


if __name__ == "__main__":
    unittest.main()
