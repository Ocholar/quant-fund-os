import unittest

from qfos_runtime.execution_gate import evaluate_execution_gate


class ExecutionGateTests(unittest.TestCase):
    def test_running_buy_allows_persistence(self):
        decision = evaluate_execution_gate(
            lambda: {"paused": False, "reason": ""},
            {"side": "buy"},
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "control_running")

    def test_running_sell_allows_persistence(self):
        decision = evaluate_execution_gate(
            lambda: {"paused": False, "reason": ""},
            {"side": "sell"},
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(decision.reason, "control_running")

    def test_manual_pause_blocks_buy(self):
        decision = evaluate_execution_gate(
            lambda: {"paused": True, "reason": "manual_pause"},
            {"side": "buy"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.reason,
            "control_paused_entry_blocked:manual_pause",
        )

    def test_manual_pause_allows_sell(self):
        decision = evaluate_execution_gate(
            lambda: {"paused": True, "reason": "manual_pause"},
            {"side": "sell"},
        )

        self.assertTrue(decision.allowed)
        self.assertTrue(decision.paused)
        self.assertEqual(
            decision.reason,
            "control_paused_exit_allowed:manual_pause",
        )

    def test_kill_switch_blocks_buy(self):
        decision = evaluate_execution_gate(
            lambda: {"paused": True, "reason": "manual_kill_switch"},
            {"side": "buy"},
        )

        self.assertFalse(decision.allowed)
        self.assertEqual(
            decision.reason,
            "control_paused_entry_blocked:manual_kill_switch",
        )

    def test_kill_switch_allows_sell(self):
        decision = evaluate_execution_gate(
            lambda: {"paused": True, "reason": "manual_kill_switch"},
            {"side": "sell"},
        )

        self.assertTrue(decision.allowed)
        self.assertEqual(
            decision.reason,
            "control_paused_exit_allowed:manual_kill_switch",
        )

    def test_missing_control_state_blocks_all_persistence(self):
        decision = evaluate_execution_gate(lambda: None, {"side": "sell"})

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "control_state_unavailable")

    def test_malformed_control_state_blocks_all_persistence(self):
        decision = evaluate_execution_gate(lambda: {"reason": "unknown"}, {"side": "buy"})

        self.assertFalse(decision.allowed)
        self.assertEqual(decision.reason, "control_state_unavailable")


if __name__ == "__main__":
    unittest.main()
