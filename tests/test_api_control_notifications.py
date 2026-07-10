import unittest
from pathlib import Path


class ApiControlNotificationContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1] / "services" / "api.py"
        ).read_text(encoding="utf-8")

    def section(self, route_marker, next_marker):
        start = self.source.index(route_marker)
        end = self.source.index(next_marker, start)
        return self.source[start:end]

    def test_background_tasks_imported(self):
        self.assertIn(
            "from fastapi import BackgroundTasks, FastAPI",
            self.source,
        )

    def test_pause_returns_without_synchronous_telegram_call(self):
        section = self.section('@app.post("/pause")', '@app.post("/resume")')
        self.assertIn("def pause(background_tasks: BackgroundTasks):", section)
        self.assertIn("background_tasks.add_task(", section)
        self.assertNotIn("\n    send_telegram_alert(", section)

    def test_resume_returns_without_synchronous_telegram_call(self):
        section = self.section('@app.post("/resume")', '@app.post("/kill-switch")')
        self.assertIn("def resume(background_tasks: BackgroundTasks):", section)
        self.assertIn("background_tasks.add_task(", section)
        self.assertNotIn("\n    send_telegram_alert(", section)

    def test_kill_switch_returns_without_synchronous_telegram_call(self):
        section = self.section('@app.post("/kill-switch")', '@app.get("/dashboard"')
        self.assertIn(
            "def kill_switch(background_tasks: BackgroundTasks):",
            section,
        )
        self.assertIn("background_tasks.add_task(", section)
        self.assertNotIn("\n    send_telegram_alert(", section)


if __name__ == "__main__":
    unittest.main()
