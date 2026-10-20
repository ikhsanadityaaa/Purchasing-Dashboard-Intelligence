from pathlib import Path
from tempfile import TemporaryDirectory
from datetime import datetime
import json
import unittest

from desktop_runtime import DesktopRuntime


ROOT = Path(__file__).resolve().parents[1]


class AutoUpdate0115Tests(unittest.TestCase):
    def test_legacy_xls_reader_is_packaged(self):
        requirements = (ROOT / 'requirements-windows.txt').read_text(encoding='utf-8')
        build = (ROOT / 'build_windows.bat').read_text(encoding='utf-8')
        self.assertIn('xlrd>=2.0.1,<3', requirements)
        self.assertIn('--hidden-import xlrd', build)

    def test_activity_controls_and_single_spinner(self):
        ui = (ROOT / 'dashboard/static/desktop/auto_update.js').read_text(encoding='utf-8')
        css = (ROOT / 'dashboard/static/desktop/auto_update.css').read_text(encoding='utf-8')
        self.assertIn('id="zd-copy-log"', ui)
        self.assertIn('id="zd-clear-log"', ui)
        self.assertIn("api('/api/desktop/logs/clear'", ui)
        self.assertNotIn("label.innerHTML=(step==='running'||step==='queued')?'<span class=\"zd-spinner\"", ui)
        self.assertIn("row.classList.add('zd-log-success')",ui)
        self.assertIn("row.classList.add('zd-log-error')",ui)
        self.assertIn('.zd-log-success{color:#15803d',css)
        self.assertIn('.zd-log-error{color:#b91c1c',css)

    def test_log_clear_and_simple_progress_text(self):
        with TemporaryDirectory() as folder:
            runtime = DesktopRuntime(Path(folder), 'test-secret', 12345, ROOT)
            runtime.log('old entry')
            self.assertTrue(runtime.tail_log())
            self.assertEqual({'cleared': True}, runtime.clear_log())
            self.assertEqual([], runtime.tail_log())
        line = '2026-10-16 08:52:33,351 - INFO - [V29] File Excel stabil'
        self.assertEqual('Downloading data', DesktopRuntime._progress_text(line))
        activity = '[2026-10-16T08:52:33] [serp] ' + line
        self.assertEqual('[08:52:33] [serp] Downloading data\n', DesktopRuntime._activity_text(activity))
        detail='ACTIVITY Started | Process: All Product ID | Range: 2026-09-16 to 2026-10-16 | Trigger: Manual | Mode: Headless'
        self.assertEqual(detail[9:],DesktopRuntime._progress_text(detail))
        failed='ACTIVITY Finished | Process: All Product ID | Result: Failed | Error: SAP login timeout'
        self.assertEqual(failed[9:],DesktopRuntime._progress_text(failed))

    def test_activity_log_keeps_milestones_and_hides_sap_debug(self):
        with TemporaryDirectory() as folder:
            runtime = DesktopRuntime(Path(folder), 'test-secret', 12345, ROOT)
            runtime.log('[serp] SAPFileField[1] /app/con[0]/ses[0]/wnd[1]/usr/ctxtDY_FILENAME')
            runtime.log('[serp] [3/6] Login portal...')
            runtime.log('[serp] Upload gagal HTTP 500 setelah 5x')
            activity = ''.join(runtime.tail_log())
            self.assertNotIn('SAPFileField', activity)
            self.assertIn('Signing in', activity)
            self.assertIn('Updating local data', activity)

    def test_process_card_progress_is_short_english(self):
        cases = {
            'Buka portal ServeOne...': 'Opening browser',
            'Login berhasil': 'Signing in',
            'Membuka SAP GUI...': 'Opening SAP',
            'Processing date: 2026-10-01 - 2026-10-16': 'Searching data',
            'Local Import Item Registration': 'Updating local data',
        }
        for line, expected in cases.items():
            self.assertEqual(expected, DesktopRuntime._progress_text(line))

    def test_only_latest_completion_time_is_saved(self):
        with TemporaryDirectory() as folder:
            settings = Path(folder) / 'settings.json'
            runtime = DesktopRuntime(Path(folder), 'test-secret', 12345, ROOT)
            runtime.store.note_completed('serp', datetime(2026, 10, 16, 8, 30))
            runtime.store.note_completed('serp', datetime(2026, 10, 16, 9, 45))
            self.assertEqual('2026-10-16T09:45:00', runtime.status_payload()['process_completed_at']['serp'])
            saved = json.loads(settings.read_text(encoding='utf-8'))
            self.assertEqual({'serp': '2026-10-16T09:45:00'}, saved['process_completed_at'])

    def test_google_actions_move_into_more_menu(self):
        ui = (ROOT / 'dashboard/static/desktop/sheet_sync.js').read_text(encoding='utf-8')
        css = (ROOT / 'dashboard/static/desktop/sheet_sync.css').read_text(encoding='utf-8')
        self.assertIn("menuButton('Google Login'", ui)
        self.assertIn('mountImportActions()', ui)
        self.assertIn('mountVendorActions()', ui)
        self.assertIn('#zd-sheets-status{display:none!important}', css)

    def test_startup_never_clears_item_registration(self):
        backend = (ROOT / 'dashboard/app.py').read_text(encoding='utf-8')
        self.assertNotIn('DELETE FROM item_registration', backend)
        self.assertNotIn("flag_key = 'item_reg_v60z37_cleared'", backend)


if __name__ == '__main__':
    unittest.main()
