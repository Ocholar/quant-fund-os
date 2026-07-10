import unittest

from qfos_runtime.control_plane import ControlPlane


class ControlPlaneTests(unittest.TestCase):
    def setUp(self):
        self.state = {
            "paused": True,
            "reason": "manual_pause",
            "updated_at": "2026-06-21T12:00:00+00:00",
        }
        self.pause_calls = []
        self.resume_calls = 0

        def pause_writer(reason):
            self.pause_calls.append(reason)
            self.state = {
                "paused": True,
                "reason": reason,
                "updated_at": "pause-time",
            }

        def resume_writer():
            self.resume_calls += 1
            self.state = {
                "paused": False,
                "reason": "",
                "updated_at": "resume-time",
            }

        self.control = ControlPlane(
            pause_writer=pause_writer,
            resume_writer=resume_writer,
            state_reader=lambda: self.state,
        )

    def test_pause_writes_reason_and_returns_authoritative_state(self):
        state = self.control.pause("manual_kill_switch")

        self.assertEqual(self.pause_calls, ["manual_kill_switch"])
        self.assertTrue(state["paused"])
        self.assertEqual(state["reason"], "manual_kill_switch")

    def test_pause_uses_safe_default_reason(self):
        state = self.control.pause("")

        self.assertEqual(self.pause_calls, ["manual_pause"])
        self.assertTrue(state["paused"])
        self.assertEqual(state["reason"], "manual_pause")

    def test_resume_returns_unpaused_authoritative_state(self):
        state = self.control.resume()

        self.assertEqual(self.resume_calls, 1)
        self.assertFalse(state["paused"])
        self.assertEqual(state["reason"], "")

    def test_state_normalizes_malformed_values(self):
        control = ControlPlane(
            pause_writer=lambda reason: None,
            resume_writer=lambda: None,
            state_reader=lambda: {"paused": 1, "reason": None},
        )

        self.assertEqual(
            control.state(),
            {"paused": True, "reason": "", "updated_at": None},
        )


if __name__ == "__main__":
    unittest.main()
