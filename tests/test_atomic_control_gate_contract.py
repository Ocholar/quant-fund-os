import unittest
from pathlib import Path


class AtomicControlGateContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.source = (
            Path(__file__).resolve().parents[1] / "main.py"
        ).read_text(encoding="utf-8")

    @classmethod
    def active_atomic_function(cls):
        marker = 'def qfos_persist_fill_atomic(conn, fill, source="main_loop"):'
        start = cls.source.rindex(marker)
        next_def = cls.source.find("\ndef ", start + len(marker))
        if next_def == -1:
            next_def = len(cls.source)
        return cls.source[start:next_def]

    def test_gate_import_exists(self):
        self.assertIn(
            "from qfos_runtime.execution_gate import default_execution_gate",
            self.source,
        )

    def test_active_atomic_boundary_passes_fill_to_gate(self):
        section = self.active_atomic_function()
        self.assertIn("default_execution_gate()(fill)", section)

    def test_gate_precedes_active_atomic_audit_mode(self):
        section = self.active_atomic_function()
        gate_index = section.index("default_execution_gate()(fill)")
        audit_index = section.index("if _QFOS_AUDIT_BOOT:")
        self.assertLess(gate_index, audit_index)

    def test_paused_sell_allowance_is_telemetrically_visible(self):
        section = self.active_atomic_function()
        self.assertIn("[EXECUTION_GATE_ALLOW_EXIT]", section)

    def test_paused_entry_rejection_is_telemetrically_visible(self):
        section = self.active_atomic_function()
        self.assertIn("[EXECUTION_GATE_BLOCK]", section)


if __name__ == "__main__":
    unittest.main()
