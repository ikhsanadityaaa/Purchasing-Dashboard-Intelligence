import ast
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


class FakeQuery:
    def __init__(self, result):
        self.result = result

    def group_by(self, *_args):
        return self

    def all(self):
        return self.result

    def first(self):
        return self.result

    def scalar(self):
        return self.result


class DashboardUpdateTimestampTests(unittest.TestCase):
    def test_latest_table_timestamp_overrides_older_upload_log(self):
        tree = ast.parse((ROOT / 'dashboard/app.py').read_text(encoding='utf-8'))
        node = next(
            item for item in tree.body
            if isinstance(item, ast.FunctionDef) and item.name == '_dashboard_update_timestamps'
        )
        old = datetime(2026, 10, 16, 7, 45, 12)
        latest = datetime(2026, 10, 21, 9, 24, 32)
        results = iter([
            [('SO', old), ('ITEM_REG', old)],
            (old, old),
            (latest, latest),
        ])
        session = SimpleNamespace(query=lambda *_args: FakeQuery(next(results)))
        model = lambda **fields: type('Model', (), fields)
        scope = {
            'db': SimpleNamespace(session=session),
            'func': SimpleNamespace(max=lambda value: value),
            'UploadLog': model(file_type='file_type', uploaded_at='upload_uploaded_at'),
            'SOData': model(uploaded_at='so_uploaded_at', updated_at='so_updated_at'),
            'ItemRegistration': model(uploaded_at='item_uploaded_at', updated_at='item_updated_at'),
            'utc_isoformat': lambda value: value.isoformat() + '+00:00' if value else None,
        }
        exec(compile(ast.Module(body=[node], type_ignores=[]), '<dashboard-update>', 'exec'), scope)

        result = scope['_dashboard_update_timestamps']()

        self.assertEqual(result['last_updated_item_registration'], '2026-10-21T09:24:32+00:00')
        self.assertEqual(result['last_updated_smro'], '2026-10-16T07:45:12+00:00')

    def test_cached_dashboard_payload_refreshes_update_metadata(self):
        source = (ROOT / 'dashboard/app.py').read_text(encoding='utf-8')
        self.assertIn('cached.update(_dashboard_update_timestamps())', source)


if __name__ == '__main__':
    unittest.main()
