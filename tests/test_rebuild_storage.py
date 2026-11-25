import os
import unittest
from pathlib import Path
from unittest.mock import patch
from serveone_storage import data_dir


class StorageTests(unittest.TestCase):
    def test_override(self):
        with patch.dict(os.environ, {'SERVEONE_DATA_DIR': str(Path.cwd())}):
            self.assertEqual(data_dir(), Path.cwd().resolve())

    def test_windows_requires_d(self):
        with patch.dict(os.environ, {}, clear=True), patch('serveone_storage.os.name', 'nt'), patch('serveone_storage.Path') as path:
            path.return_value.is_dir.return_value = False
            with self.assertRaisesRegex(OSError, 'no fallback to C'):
                data_dir()

    def test_windows_default(self):
        with patch.dict(os.environ, {}, clear=True), patch('serveone_storage.os.name', 'nt'), patch('serveone_storage.Path') as path:
            path.return_value.is_dir.return_value = True
            data_dir()
            path.assert_any_call('D:/ServeoneDashboard/data')


if __name__ == '__main__':
    unittest.main()
