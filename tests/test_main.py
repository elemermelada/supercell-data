"""Unit tests for main.py: the per-step error capture and the alert trigger.

Run with: pytest tests/test_main.py
(or, without pytest: python -m unittest tests.test_main)
"""

import unittest
from unittest import mock

import main


class RunStepTests(unittest.TestCase):
    def setUp(self):
        # run_step appends to the module-level ``failures``; isolate each test.
        patcher = mock.patch.object(main, "failures", [])
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_success_records_nothing(self):
        def ok_step():
            return None

        main.run_step(ok_step)
        self.assertEqual(main.failures, [])

    def test_failure_records_name_exc_and_traceback(self):
        boom = RuntimeError("kaboom")

        def failing_step():
            raise boom

        # Never raises, even though the step does.
        main.run_step(failing_step)

        self.assertEqual(len(main.failures), 1)
        name, exc, tb = main.failures[0]
        self.assertEqual(name, "failing_step")
        self.assertIs(exc, boom)
        self.assertIsInstance(tb, str)
        # The traceback captures the raising call site.
        self.assertIn("failing_step", tb)
        self.assertIn("RuntimeError", tb)


class MainTests(unittest.TestCase):
    def setUp(self):
        patcher = mock.patch.object(main, "failures", [])
        patcher.start()
        self.addCleanup(patcher.stop)

        # Neutralize side effects: no dirs created, no logging setup. Patch only
        # os.makedirs (not the whole os module) so any future os.path/os.getenv
        # use in main() isn't silently mocked out.
        makedirs_patcher = mock.patch.object(main.os, "makedirs")
        makedirs_patcher.start()
        self.addCleanup(makedirs_patcher.stop)

        setup_logging_patcher = mock.patch.object(main, "setup_logging")
        self.setup_logging = setup_logging_patcher.start()
        self.addCleanup(setup_logging_patcher.stop)

    def _patch_steps(self, **side_effects):
        # run_step reads fn.__name__ (outside its try), so each step mock must
        # carry the real step name.
        for name in ("request", "retrieve", "process", "update"):
            step = mock.Mock(__name__=name, side_effect=side_effects.get(name))
            p = mock.patch.object(main, name, step)
            p.start()
            self.addCleanup(p.stop)

    def test_no_alert_when_all_steps_succeed(self):
        self._patch_steps()
        with mock.patch.object(main, "send_failure_email") as notify:
            main.main()
        notify.assert_not_called()

    def test_alert_sent_when_a_step_fails(self):
        self._patch_steps(request=RuntimeError("boom"))
        with mock.patch.object(main, "send_failure_email") as notify:
            main.main()

        notify.assert_called_once()
        (failures_arg, log_file), _ = notify.call_args
        self.assertEqual(len(failures_arg), 1)
        self.assertEqual(failures_arg[0][0], "request")

        # setup_logging and send_failure_email must operate on the same log file.
        (setup_log_file,), _ = self.setup_logging.call_args
        self.assertEqual(setup_log_file, log_file)


if __name__ == "__main__":
    unittest.main()
