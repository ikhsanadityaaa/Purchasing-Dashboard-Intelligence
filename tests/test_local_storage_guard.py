import ast
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_guard_functions():
    source = (ROOT / 'dashboard/app.py').read_text(encoding='utf-8')
    tree = ast.parse(source)
    wanted = {'_v6324_is_storage_error', '_v6324_action_required'}
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name in wanted]
    namespace = {'os': __import__('os')}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), 'app.py', 'exec'), namespace)
    return namespace


class LocalStorageGuardTests(unittest.TestCase):
    def test_desktop_lock_is_not_reported_as_full_storage(self):
        guard = load_guard_functions()['_v6324_action_required']
        self.assertFalse(guard(15000, False, 6.3, 'database is locked', True))

    def test_desktop_real_disk_error_still_warns(self):
        guard = load_guard_functions()['_v6324_action_required']
        self.assertTrue(guard(15000, False, 6.3, 'database or disk is full', True))
        self.assertTrue(guard(150, True, 0, '', True))

    def test_desktop_bundle_has_no_pythonanywhere_repair_message(self):
        bundle = (ROOT / 'dashboard/static/assets/index-C9BxB1mr.js').read_text(encoding='utf-8')
        self.assertNotIn('Fix in the Bash console', bundle)
        self.assertNotIn('python3 cleanup_disk.py --repair', bundle)
        self.assertIn("r.storage_mode === 'local'", bundle)

    def test_probe_pressure_is_scoped_to_disk_guard(self):
        source = (ROOT / 'dashboard/app.py').read_text(encoding='utf-8')
        tree = ast.parse(source)
        functions = {node.name: ast.unparse(node) for node in tree.body
                     if isinstance(node, ast.FunctionDef)}
        self.assertNotIn('_probe_pressure', functions['_v6323_guard_event'])
        self.assertIn('_probe_pressure', functions['_v6323_disk_guard'])


if __name__ == '__main__':
    unittest.main()
