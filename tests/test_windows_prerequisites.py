"""Source-only tests; fake registry means no Windows host access or network."""
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import windows_prerequisites as win


class FakeKey:
    def __init__(self, values):
        self.values = values
    def __enter__(self):
        return self
    def __exit__(self, *args):
        pass


class FakeRegistry:
    KEY_READ = 0x20019
    KEY_WOW64_32KEY = 0x200
    KEY_WOW64_64KEY = 0x100
    HKEY_CURRENT_USER = 'current'
    HKEY_LOCAL_MACHINE = 'machine'

    def __init__(self, entries=None):
        self.entries = entries or {}

    def OpenKey(self, hive, path, unused, flags):
        values = self.entries.get((hive, path, flags & 0x300))
        if values is None:
            raise FileNotFoundError(path)
        return FakeKey(values)

    def QueryValueEx(self, key, name):
        return key.values[name], 1


class WindowsPrerequisiteTests(unittest.TestCase):
    def test_missing_registrations(self):
        empty = FakeRegistry()
        self.assertFalse(win.webview2_installed(empty))
        self.assertFalse(win.dotnet_framework_available(empty))
        self.assertEqual(len(win.missing_components(empty)), 2)

    def test_webview2_user_registry(self):
        reg = FakeRegistry({('current', win.WEBVIEW2_KEY, 0): {'pv': '151.0.0.1'}})
        self.assertTrue(win.webview2_installed(reg))
        self.assertFalse(win.dotnet_framework_available(reg))
        self.assertEqual(win.missing_components(reg), ['.NET Framework 4.6.2 or newer'])

    def test_webview2_machine_registry_32bit_view(self):
        key = ('machine', win.WEBVIEW2_KEY, FakeRegistry.KEY_WOW64_32KEY)
        dotnet = ('machine', win.DOTNET_KEY, FakeRegistry.KEY_WOW64_64KEY)
        reg = FakeRegistry({key: {'pv': '151.0.0.1'}, dotnet: {'Release': 528040}})
        self.assertTrue(win.webview2_installed(reg))
        self.assertTrue(win.dotnet_framework_available(reg))
        self.assertEqual(win.missing_components(reg), [])

    def test_zero_or_blank_webview2_not_valid(self):
        for pv in ('', '0.0.0.0'):
            reg = FakeRegistry({('current', win.WEBVIEW2_KEY, 0): {'pv': pv}})
            self.assertFalse(win.webview2_installed(reg))

    def test_cannot_execute_unsigned_or_absent_installer(self):
        with tempfile.TemporaryDirectory() as d:
            missing = Path(d) / win.WEBVIEW2_INSTALLER
            self.assertFalse(win.microsoft_signature_valid(missing))
            ok, msg = win.install_offline_webview2(missing)
            self.assertFalse(ok)
            self.assertIn('missing', msg.lower())
            missing.write_bytes(b'fake payload')
            with mock.patch.object(win.subprocess, 'run') as execute:
                ok, msg = win.install_offline_webview2(missing)
            self.assertFalse(ok)
            execute.assert_not_called()

    def test_launcher_checks_before_starting_backend(self):
        root = Path(__file__).resolve().parents[1]
        source = (root/'desktop_launcher.py').read_text(encoding='utf-8')
        main = source[source.index('def main():'):]
        self.assertLess(main.index('ensure_gui_requirements()'), main.index('data=user_data_dir()'))
        self.assertIn('if not (args.worker or args.import_db or args.scheduled_job or args.browser):', source)

    def test_installer_includes_offline_payload_and_gates_launch(self):
        root = Path(__file__).resolve().parents[1]
        iss = (root/'installer.iss').read_text(encoding='utf-8')
        self.assertIn('PrivilegesRequired=lowest', iss)
        self.assertIn('ArchitecturesAllowed=x64compatible', iss)
        self.assertIn('vendor\\MicrosoftEdgeWebView2RuntimeInstallerX64.exe', iss)
        self.assertIn('Check: PrerequisitesOk', iss)
        self.assertIn('if WebView2Installed() then', iss)
        self.assertNotIn('drive.google.com', iss)
        ps = (root/'prepare_webview2_runtime.ps1').read_text(encoding='utf-8')
        self.assertIn('Get-AuthenticodeSignature', ps)
        self.assertIn('Microsoft Corporation', ps)
        self.assertIn('go.microsoft.com', ps)


if __name__ == '__main__':
    unittest.main()
