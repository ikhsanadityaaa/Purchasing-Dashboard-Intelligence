"""V0.1.13 form OAuth and Windows module bundling regression tests."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from desktop_google_readonly import GoogleReadOnlyAccess, SCOPES
ROOT=Path(__file__).resolve().parents[1]
ID='123456789-test.apps.googleusercontent.com'

class V013Tests(unittest.TestCase):
    def test_oauth_fields_convert_to_google_installed_client(self):
        config=GoogleReadOnlyAccess.desktop_client_from_fields(' '+ID+' ',' GOCSPX-example ')
        self.assertEqual(config['installed']['client_id'],ID)
        self.assertEqual(config['installed']['client_secret'],'GOCSPX-example')
        self.assertEqual(config['installed']['token_uri'],'https://oauth2.googleapis.com/token')
        self.assertNotIn('private_key',json.dumps(config))
        self.assertEqual(SCOPES,['https://www.googleapis.com/auth/spreadsheets.readonly'])

    def test_rejects_google_service_account_and_non_oauth_ids(self):
        for value in ('service@project.iam.gserviceaccount.com','https://localhost',''):
            with self.assertRaises(ValueError):
                GoogleReadOnlyAccess.desktop_client_from_fields(value,'GOCSPX-example')
        with self.assertRaises(ValueError):
            GoogleReadOnlyAccess.desktop_client_from_fields(ID,'unsafe\nvalue')

    def test_saved_client_not_returned_to_frontend(self):
        with tempfile.TemporaryDirectory() as directory:
            # Fake reversible protection for non-Windows test environment.
            with patch('desktop_google_readonly._crypt',side_effect=lambda b,*a,**k: b[::-1]):
                access=GoogleReadOnlyAccess(directory)
                status=access.save(GoogleReadOnlyAccess.desktop_client_from_fields(ID,'GOCSPX-abc'))
                self.assertTrue(status['oauth_client_configured'])
                self.assertNotIn('GOCSPX-abc',str(status))
                self.assertNotIn(b'GOCSPX-abc',access.client_path.read_bytes())
                self.assertEqual(access._load_store(access.client_path,b'ServeoneDesktop.OAuthClient.ReadOnly.v1')['installed']['client_id'],ID)

    def test_form_supports_json_and_manual_oauth_configuration(self):
        js=(ROOT/'dashboard/static/desktop/sheet_sync.js').read_text('utf8')
        self.assertIn('zd-google-setup-overlay',js)
        self.assertIn('Client ID',js)
        self.assertIn('client_secret:secret',js)
        self.assertIn('console.cloud.google.com/apis/credentials',js)
        self.assertIn('type="file"',js)
        self.assertIn('JSON.parse(await file.text())',js)
        self.assertIn("if(file.size>32768)",js)
        self.assertIn("jsonPut(payload)",js)
        self.assertIn("start('once')",js)
        self.assertIn('const attempted=new Set()',js)

    def test_windows_packages_sap_automation_dependencies(self):
        requirements=(ROOT/'requirements-windows.txt').read_text('utf8').lower()
        build=(ROOT/'build_windows.bat').read_text('utf8').lower()
        for module in ('psutil','pyautogui','pygetwindow','pyperclip','pywinauto','pywin32'):
            self.assertIn(module,requirements)
        for module in ('psutil','pyautogui','pygetwindow','pyperclip','pywinauto','win32com.client'):
            self.assertIn(module,build)

if __name__=='__main__':unittest.main()
