"""V0.1.5 desktop packaging regression tests; synthetic files only."""
import ast
import sqlite3
import tempfile
import types
import unittest
from pathlib import Path
from desktop_runtime import DesktopRuntime
from automation._paths import assert_local_dashboard_api
ROOT=Path(__file__).resolve().parents[1]

class LocalImportSecurityTests(unittest.TestCase):
 def test_destination_allowlist(self):
  for url in ('http://127.0.0.1:4921/api/upload/smro?desktop_key=fake','http://localhost:5001/api/upload/item-registration'):
   assert_local_dashboard_api(url)
  for url in ('https://pythonanywhere.com/api/upload/smro', 'http://192.168.1.243/api/upload/smro', 'https://127.0.0.1/api/upload/smro', 'http://127.0.0.1/evil'):
   with self.assertRaises(ValueError):assert_local_dashboard_api(url)
 def test_all_automation_workers_validate_target(self):
  for rel in ('automation/main.py','automation/automation_serp.py','automation/automation_pur_info_reg.py'):
   self.assertIn('assert_local_dashboard_api', (ROOT/rel).read_text(encoding='utf8'))
 def test_local_import_log_hides_secret_url(self):
  txt=(ROOT/'automation/main.py').read_text('utf8')
  self.assertNotIn('-> {CONFIG[\'DASHBOARD_API\']}',txt)
 def test_html_session_injected_only_dynamically(self):
  html=(ROOT/'dashboard/static/index.html').read_text('utf8')
  self.assertIn("localUrl.origin === location.origin",html)
  self.assertIn("reqUrl.origin === location.origin",html)
  self.assertIn('#z-loading-toast.z-show{display:flex!important}',html)
  self.assertIn('Importing Excel into local database',html)

class VisualTests(unittest.TestCase):
 def test_exe_icon_build(self):
  self.assertTrue((ROOT/'assets/serveone_icon.ico').is_file())
  self.assertIn('--icon "assets\\serveone_icon.ico"',(ROOT/'build_windows.bat').read_text('utf8'))
  self.assertIn('SetupIconFile=assets\\serveone_icon.ico',(ROOT/'installer.iss').read_text('utf8'))
 def test_zoom(self):
  txt=(ROOT/'dashboard/static/desktop/display_controls.js').read_text('utf8')
  for part in ('autoFit()', 'localStorage.setItem', "window.addEventListener('wheel'", "e.ctrlKey", "e.key==='0'"):
   self.assertIn(part,txt)
  self.assertNotIn('zd-zoom-toolbar',txt)
 def test_process_animation_and_diagnostics(self):
  txt=(ROOT/'dashboard/static/desktop/auto_update.js').read_text('utf8')
  self.assertIn('updateRunButtons(state)',txt)
  self.assertIn('zd-spinner',txt)
  self.assertIn('Check Local SO Data',txt)

class DiagnosticsTests(unittest.TestCase):
 def test_aggregate_counts_without_data_records(self):
  tree=ast.parse((ROOT/'desktop_runtime.py').read_text('utf8'))
  outer=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='register_desktop_routes')
  fn=next(n for n in outer.body if isinstance(n,ast.FunctionDef) and n.name=='desktop_import_diagnostics')
  fn.decorator_list=[]
  with tempfile.TemporaryDirectory() as temp:
   base=Path(temp);(base/'instance').mkdir()
   conn=sqlite3.connect(base/'instance/po_database.db')
   conn.execute('CREATE TABLE so_data (so_create_date TEXT, so_status TEXT)')
   conn.execute("INSERT INTO so_data VALUES (date('now'), 'Pending')")
   conn.execute("INSERT INTO so_data VALUES (date('now'), 'Delivery Completed')")
   conn.execute('CREATE TABLE so_history_lite (id INTEGER)')
   conn.execute('INSERT INTO so_history_lite VALUES (1)')
   conn.commit();conn.close()
   scope={'runtime':types.SimpleNamespace(data_dir=base),'jsonify':lambda x:x}
   exec(compile(ast.Module(body=[fn],type_ignores=[]),'<diag>','exec'),scope)
   result=scope['desktop_import_diagnostics']()
   self.assertEqual(result['so_last_2_months'],2)
   self.assertEqual(result['completed_last_2_months'],1)
   self.assertEqual(result['history_rows'],1)

if __name__=='__main__':unittest.main()
