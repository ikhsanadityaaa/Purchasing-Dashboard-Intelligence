"""Regression tests for the Windows unicode and scheduled Run All behavior."""
import unittest
from pathlib import Path
from unittest.mock import patch
from desktop_runtime import DesktopRuntime,SettingsStore

ROOT=Path(__file__).resolve().parents[1]

class Release019Tests(unittest.TestCase):
    def test_scheduler_run_all_ignores_independent_schedule_range(self):
        js=(ROOT/'dashboard/static/desktop/auto_update.js').read_text()
        runtime=(ROOT/'desktop_runtime.py').read_text()
        self.assertIn("$('#zd-process').onchange=updateScheduleRange",js)
        self.assertIn("$('#zd-schedule-range').hidden=all",js)
        self.assertIn("Uses Processes date ranges",js)
        self.assertIn("if process == 'all':\n                    # Run All ignores",runtime)
        self.assertIn("apply_range_env(env,p,selected_ranges[p])",runtime)
    def test_unicode_safe_windows_streams(self):
        launcher=(ROOT/'desktop_launcher.py').read_text()
        self.assertIn("stream.reconfigure(encoding='utf-8',errors='backslashreplace')",launcher)
        self.assertIn('configure_windows_unicode_streams()',launcher)
    def test_run_all_scheduler_schedule_validation_kept(self):
        source=(ROOT/'desktop_runtime.py').read_text()
        self.assertIn("if not schedule:\n                    raise ValueError('Scheduled job not found')",source)

if __name__=='__main__':unittest.main()
