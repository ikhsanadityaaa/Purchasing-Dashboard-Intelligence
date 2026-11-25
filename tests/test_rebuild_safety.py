import importlib.util
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from schedule_policy import DEFAULT_WORKING_HOURS, validate_working_hours, within_working_hours

spec = importlib.util.spec_from_file_location('lifecycle', Path(__file__).parents[1] / 'automation' / 'lifecycle.py')
lifecycle = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lifecycle)


class RebuildSafetyTests(unittest.TestCase):
    def test_working_hours_boundaries(self):
        for hour, minute, expected in [(6, 59, False), (7, 0, True), (16, 59, True), (17, 0, False), (18, 0, False)]:
            with self.subTest(hour=hour, minute=minute):
                self.assertEqual(within_working_hours(now=datetime(2026, 11, 9, hour, minute)), expected)

    def test_weekend_is_skipped(self):
        self.assertFalse(within_working_hours(now=datetime(2026, 11, 8, 10)))

    def test_timezone_is_not_host_timezone(self):
        self.assertFalse(within_working_hours(now=datetime(2026, 11, 9, 10, tzinfo=timezone.utc)))
        self.assertTrue(within_working_hours(now=datetime(2026, 11, 9, 9, tzinfo=timezone.utc)))

    def test_manual_policy_can_be_disabled(self):
        self.assertTrue(within_working_hours({**DEFAULT_WORKING_HOURS, 'enabled': False}, datetime(2026, 11, 8, 23)))

    def test_invalid_working_hours_rejected(self):
        for values in [{'start': '7:00'}, {'end': '06:00'}, {'timezone': 'Invalid/Zone'}, {'days': [True]}, {'enabled': 'yes'}]:
            with self.subTest(values=values), self.assertRaises(ValueError):
                validate_working_hours({**DEFAULT_WORKING_HOURS, **values})

    def test_import_requires_commit_confirmation(self):
        for status, headers, body, expected in [(200, {'X-Import-Committed': '1'}, {'added': 3}, True),
            (202, {'X-Import-Committed': '1'}, {'queued': True}, False),
            (200, {}, {'success': True}, False),
            (500, {'X-Import-Committed': '1'}, {}, False),
            (200, {'X-Import-Committed': '1'}, {'error': 'rollback'}, False),
            (200, {'X-Import-Committed': '1'}, {'success': False}, False)]:
            with self.subTest(status=status, body=body):
                response = SimpleNamespace(status_code=status, headers=headers, json=lambda: body)
                self.assertEqual(lifecycle.import_committed(response), expected)

    def test_profile_cleanup_does_not_touch_outside_data_root(self):
        with tempfile.TemporaryDirectory() as folder, tempfile.TemporaryDirectory() as outside:
            profile = Path(outside) / 'chrome_profiles' / 'abc12345'
            profile.mkdir(parents=True)
            with patch.dict(os.environ, {'SERVEONE_DATA_DIR': folder}):
                lifecycle.cleanup_profile(SimpleNamespace(_profile=str(profile)), '_profile')
            self.assertTrue(profile.is_dir())

    def test_closed_profile_removed_and_active_profile_retained(self):
        with tempfile.TemporaryDirectory() as folder:
            profile = Path(folder) / 'chrome_profiles' / 'abc12345'
            profile.mkdir(parents=True)
            driver = SimpleNamespace(_profile=str(profile))
            process = SimpleNamespace(info={'name': 'chrome.exe', 'cmdline': ['chrome.exe', '--user-data-dir=' + str(profile)]})
            with patch.dict(os.environ, {'SERVEONE_DATA_DIR': folder}), patch('psutil.process_iter', return_value=[process]):
                lifecycle.cleanup_profile(driver, '_profile')
                self.assertTrue(profile.is_dir())
            with patch.dict(os.environ, {'SERVEONE_DATA_DIR': folder}), patch('psutil.process_iter', return_value=[]):
                lifecycle.cleanup_profile(driver, '_profile')
                self.assertFalse(profile.exists())

    def test_scheduled_worker_rechecks_after_lock(self):
        from desktop_launcher import run_worker
        fake = SimpleNamespace(public=lambda: {'automation_enabled': True,
            'schedules': [{'id': 'scheduled', 'enabled': True}], 'working_hours': DEFAULT_WORKING_HOURS})
        with patch.dict(os.environ, {'SERVEONE_SCHEDULE_ID': 'scheduled'}), patch('desktop_runtime.SettingsStore', return_value=fake), patch('schedule_policy.within_working_hours', return_value=False), patch('runpy.run_path') as execute:
            with self.assertRaises(SystemExit) as raised:
                run_worker('serp')
            self.assertEqual(raised.exception.code, 75)
            execute.assert_not_called()

    def test_due_stops_at_five(self):
        from desktop_runtime import SettingsStore, validate_schedule
        with tempfile.TemporaryDirectory() as folder:
            store = SettingsStore(Path(folder)/'settings.json')
            job = validate_schedule({'process': 'serp', 'mode': 'interval'})
            store.save(True, [job])
            store.data['run_history'].clear()
            self.assertEqual(len(store.due(datetime(2026, 11, 9, 16, 59))), 1)
            self.assertEqual(store.due(datetime(2026, 11, 9, 17, 0)), [])

    def test_portal_warning_only_for_approved_host(self):
        from unittest.mock import Mock
        for host, clicked in [('sportal.serveone.id', True), ('evil.example', False), ('sportal.serveone.id.evil.example', False)]:
            driver = Mock()
            driver.title = 'This site does not support a secure connection'
            driver.current_url = 'http://' + host + '/portal/PortalView.do'
            button = Mock()
            button.is_displayed.return_value = True
            driver.find_elements.return_value = [button]
            lifecycle.install_portal_navigation(driver)
            driver.get('http://' + host + '/portal/PortalView.do')
            self.assertEqual(button.click.called, clicked)


if __name__ == '__main__':
    unittest.main()
