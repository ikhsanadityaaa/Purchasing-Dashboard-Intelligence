"""Regression for Stop All Windows taskkill and packaging build gate."""
import io
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from desktop_runtime import DesktopRuntime


class FakeWorker:
    def __init__(self, pid=None):
        if pid is not None:
            self.pid = pid
        self.alive = True
        self.terminated = False
    def poll(self):
        return None if self.alive else -15
    def terminate(self):
        self.terminated = True
        self.alive = False


class StopAllWindowsTests(unittest.TestCase):
    def test_windows_mock_worker_without_pid_uses_terminate(self):
        worker = FakeWorker()
        with patch('desktop_runtime.subprocess.run') as taskkill:
            DesktopRuntime._stop_worker_tree(worker, windows=True)
        taskkill.assert_not_called()
        self.assertTrue(worker.terminated)

    def test_windows_real_pid_invokes_taskkill_tree(self):
        worker = FakeWorker(pid=543210)
        # After a successful taskkill the process is gone (poll is no longer None).
        def kill(*args, **kwargs):
            worker.alive = False
            return SimpleNamespace(returncode=0)
        with patch('desktop_runtime.subprocess.run', side_effect=kill) as taskkill:
            DesktopRuntime._stop_worker_tree(worker, windows=True)
        taskkill.assert_called_once()
        self.assertEqual(taskkill.call_args.args[0],
                         ['taskkill', '/PID', '543210', '/T', '/F'])
        self.assertFalse(worker.terminated)

    def test_windows_taskkill_failure_falls_back_to_terminate(self):
        worker = FakeWorker(pid=543210)
        with patch('desktop_runtime.subprocess.run',
                   return_value=SimpleNamespace(returncode=1)):
            DesktopRuntime._stop_worker_tree(worker, windows=True)
        self.assertTrue(worker.terminated)

    def test_invalid_pid_never_passed_to_taskkill(self):
        worker = FakeWorker(pid='543210')
        with patch('desktop_runtime.subprocess.run') as taskkill:
            DesktopRuntime._stop_worker_tree(worker, windows=True)
        taskkill.assert_not_called()
        self.assertTrue(worker.terminated)


if __name__ == '__main__':
    unittest.main()
