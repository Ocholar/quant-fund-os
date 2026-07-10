import unittest

from qfos_runtime.execution_telemetry import ExecutionCycleTelemetry


class ExecutionCycleTelemetryTests(unittest.TestCase):
    def test_none_result_is_rejected_not_applied(self):
        telemetry = ExecutionCycleTelemetry(raw_orders=0, proposed_fills=1)

        persisted = telemetry.record_persistence_result(
            {"symbol": "PLAY/USDT", "side": "sell"},
            None,
            "audit_fill_barrier",
        )

        self.assertFalse(persisted)
        self.assertEqual(telemetry.persisted_fills, 0)
        self.assertEqual(telemetry.final_applied_fills, 0)
        self.assertEqual(telemetry.rejected_fills, 1)
        self.assertEqual(telemetry.rejected[0].symbol, "PLAY/USDT")
        self.assertEqual(telemetry.rejected[0].reason, "audit_fill_barrier")

    def test_false_result_is_rejected_not_applied(self):
        telemetry = ExecutionCycleTelemetry(proposed_fills=1)

        persisted = telemetry.record_persistence_result(
            {"symbol": "XMR/USDT", "side": "sell"},
            False,
        )

        self.assertFalse(persisted)
        self.assertEqual(telemetry.final_applied_fills, 0)
        self.assertEqual(telemetry.rejected_fills, 1)

    def test_successful_result_is_applied(self):
        telemetry = ExecutionCycleTelemetry(proposed_fills=1)

        persisted = telemetry.record_persistence_result(
            {"symbol": "TIA/USDT", "side": "sell"},
            {"trade_id": 999},
        )

        self.assertTrue(persisted)
        self.assertEqual(telemetry.persisted_fills, 1)
        self.assertEqual(telemetry.final_applied_fills, 1)
        self.assertEqual(telemetry.rejected_fills, 0)

    def test_payload_reports_truthful_counts(self):
        telemetry = ExecutionCycleTelemetry(raw_orders=3, proposed_fills=2)

        telemetry.record_persistence_result(
            {"symbol": "LTC/USDT", "side": "sell"},
            None,
            "atomic_persistence_rejected",
        )
        telemetry.record_persistence_result(
            {"symbol": "TIA/USDT", "side": "buy"},
            {"trade_id": 1000},
        )

        payload = telemetry.as_dict()

        self.assertEqual(payload["raw_orders"], 3)
        self.assertEqual(payload["proposed_fills"], 2)
        self.assertEqual(payload["persisted_fills"], 1)
        self.assertEqual(payload["rejected_fills"], 1)
        self.assertEqual(payload["final_applied_fills"], 1)


if __name__ == "__main__":
    unittest.main()
