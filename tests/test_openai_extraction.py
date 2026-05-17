from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from typing import Any
from unittest.mock import patch


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))


def load_local_config() -> dict[str, str]:
    key_file = PROJECT_ROOT / "key.md"
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


class OpenAIExtractionTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        config = load_local_config()
        api_key = os.environ.get("AI_API_KEY") or os.environ.get("OPENAI_API_KEY") or config.get("api_key")
        if not api_key:
            raise unittest.SkipTest("Set AI_API_KEY or add key.md to run the live AI test.")
        os.environ["AI_API_KEY"] = api_key
        if "base_url" in config:
            os.environ.setdefault("AI_BASE_URL", config["base_url"])
        if "model" in config:
            os.environ.setdefault("AI_MODEL", config["model"])

    def test_extracts_calendar_event_and_task(self) -> None:
        import app

        try:
            result = app.extract_items_from_message(
                "Schedule a project meeting on 2026-05-20 from 15:00 to 16:00 "
                "Australia/Sydney. Also create a task to submit the report by 2026-05-22."
            )
        except Exception as exc:
            error_name = exc.__class__.__name__
            if error_name == "APIConnectionError":
                raise unittest.SkipTest("AI API is not reachable from this environment.") from exc
            if error_name == "RateLimitError" and "insufficient_quota" in str(exc):
                raise unittest.SkipTest("AI API key has insufficient quota.") from exc
            raise

        self.assertIn("calendar_events", result)
        self.assertIn("google_tasks", result)
        self.assertIsInstance(result["calendar_events"], list)
        self.assertIsInstance(result["google_tasks"], list)
        self.assertGreaterEqual(len(result["calendar_events"]), 1)
        self.assertGreaterEqual(len(result["google_tasks"]), 1)

        event: dict[str, Any] = result["calendar_events"][0]
        self.assertEqual(event["kind"], "calendar#event")
        self.assertTrue(event["summary"])
        self.assertIn("2026-05-20", event["start"]["dateTime"])
        self.assertIn("2026-05-20", event["end"]["dateTime"])
        self.assertEqual(event["start"]["timeZone"], "Australia/Sydney")
        self.assertEqual(event["status"], "confirmed")
        self.assertEqual(event["extendedProperties"]["private"]["created_by"], "deepseek")

        task: dict[str, Any] = result["google_tasks"][0]
        self.assertEqual(task["kind"], "tasks#task")
        self.assertTrue(task["title"])
        self.assertEqual(task["status"], "needsAction")
        self.assertIsNotNone(task["due"])
        self.assertIn("2026-05-22", task["due"])
        self.assertTrue(task["source_text"])

    def test_parses_structured_openai_response(self) -> None:
        import app

        expected = {
            "calendar_events": [],
            "google_tasks": [
                {
                    "kind": "tasks#task",
                    "title": "Submit report",
                    "notes": "Submit report.",
                    "status": "needsAction",
                    "due": "2026-05-22T00:00:00.000Z",
                    "source_text": "Submit the report by 2026-05-22.",
                    "confidence": "0.95",
                }
            ],
        }

        class FakeCompletions:
            def create(self, **kwargs: Any) -> Any:
                self.kwargs = kwargs
                message = type("Message", (), {"content": app.json.dumps(expected)})()
                choice = type("Choice", (), {"message": message})()
                return type("Response", (), {"choices": [choice]})()

        fake_completions = FakeCompletions()
        fake_chat = type("FakeChat", (), {"completions": fake_completions})()
        fake_client = type("FakeClient", (), {"chat": fake_chat})()

        with patch.object(app, "create_ai_client", return_value=fake_client):
            result = app.extract_items_from_message("Submit the report by 2026-05-22.")

        self.assertEqual(result, expected)
        self.assertEqual(fake_completions.kwargs["response_format"]["type"], "json_object")
        self.assertEqual(fake_completions.kwargs["model"], app.AI_MODEL)
        system_prompt = fake_completions.kwargs["messages"][0]["content"]
        self.assertIn("The current date and time is", system_prompt)
        self.assertIn("Resolve relative dates", system_prompt)


if __name__ == "__main__":
    unittest.main()
