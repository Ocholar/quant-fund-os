import unittest
from pathlib import Path


class ApiControlContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1] / "services" / "api.py"
        ).read_text(encoding="utf-8")

    def _section(self, start_marker, end_marker):
        start = self.source.index(start_marker)
        end = self.source.index(end_marker, start)
        return self.source[start:end]

    def test_status_route_is_read_only(self):
        status = self._section('@app.get("/status")', '@app.post("/pause")')
        self.assertNotIn("resume_bot(", status)
        self.assertNotIn("pause_bot(", status)
        self.assertIn("GET /status is a projection only", status)

    def test_pause_route_uses_adapter_and_emits_event(self):
        section = self._section('@app.post("/pause")', '@app.post("/resume")')
        self.assertIn('qfos_control_plane.pause("manual_pause")', section)
        self.assertIn('qfos_control_event(', section)

    def test_resume_route_uses_adapter_and_emits_event(self):
        section = self._section('@app.post("/resume")', '@app.post("/kill-switch")')
        self.assertIn("qfos_control_plane.resume()", section)
        self.assertIn('qfos_control_event(', section)

    def test_kill_switch_uses_adapter_and_emits_event(self):
        start = self.source.index('@app.post("/kill-switch")')
        section = self.source[start:]
        self.assertIn('qfos_control_plane.pause("manual_kill_switch")', section)
        self.assertIn('qfos_control_event(', section)

    def test_no_direct_control_writers_remain(self):
        self.assertNotIn("pause_bot(", self.source)
        self.assertNotIn("resume_bot(", self.source)


if __name__ == "__main__":
    unittest.main()
