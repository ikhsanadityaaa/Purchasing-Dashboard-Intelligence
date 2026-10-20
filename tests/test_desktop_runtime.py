import json
import sqlite3
import tempfile
import unittest
from datetime import datetime,timedelta
from pathlib import Path
from desktop_runtime import SettingsStore, validate_schedule
from desktop_launcher import import_existing_db


def scheduled(**extra):
    d={'process':'smro','mode':'weekly','time':'06:00','days':[4],
       'start_date':'2024-01-01','end_date':'today','interval_minutes':120,'enabled':True}
    d.update(extra)
    return d


class RuntimeTests(unittest.TestCase):
    def test_validation(self):
        self.assertEqual(validate_schedule(scheduled())['days'],[4])
        with self.assertRaises(ValueError):validate_schedule(scheduled(process='remote-upload'))
        with self.assertRaises(ValueError):validate_schedule(scheduled(days=[9]))
        with self.assertRaises(ValueError):validate_schedule(scheduled(mode='interval',interval_minutes=5))

    def test_schedule_persist_and_additive(self):
        with tempfile.TemporaryDirectory() as d:
            store=SettingsStore(Path(d)/'settings.json')
            one=validate_schedule(scheduled())
            two=validate_schedule(scheduled(mode='interval',process='serp',interval_minutes=60))
            store.save(True,[one,two])
            recovered=SettingsStore(Path(d)/'settings.json')
            self.assertEqual(len(recovered.public()['schedules']),2)
            self.assertEqual(recovered.due(datetime.now()),[])
            old=datetime.now()-timedelta(hours=3)
            recovered.note_run(two['id'],old)
            self.assertIn(two['id'],[s['id'] for s in recovered.due(datetime.now())])

    def test_database_import_snapshot(self):
        with tempfile.TemporaryDirectory() as d:
            source=Path(d)/'input.db'
            con=sqlite3.connect(source)
            con.execute('PRAGMA journal_mode=WAL')
            con.execute('CREATE TABLE t(id INTEGER PRIMARY KEY, value TEXT)')
            con.execute('INSERT INTO t(value) VALUES(?)',('synthetic',))
            con.commit()
            out=Path(d)/'instance'/'po_database.db'
            import_existing_db(source,out)
            chk=sqlite3.connect(out)
            self.assertEqual(chk.execute('SELECT value FROM t').fetchone()[0],'synthetic')
            chk.close();con.close()
            with self.assertRaises(FileExistsError):import_existing_db(source,out)

class PackagingTests(unittest.TestCase):
    def test_frontend_and_offline_endpoint(self):
        root=Path(__file__).resolve().parents[1]
        text=(root/'dashboard'/'static'/'index.html').read_text(encoding='utf-8')
        self.assertIn('/desktop/auto_update.js',text)
        self.assertNotIn('drive.google.com',text)
        self.assertNotIn('fonts.googleapis.com',text)
        self.assertTrue((root/'dashboard'/'static'/'assets'/'index-C9BxB1mr.js').is_file())
        self.assertIn("SERVEONE_FRONTEND_DIR",(root/'dashboard'/'app.py').read_text(encoding='utf-8'))
        from windows_schedule import task_args
        a=task_args(validate_schedule(scheduled()),r'C:\\Program Files\\ServeoneDashboard.exe')
        self.assertIn('/SC',a)
        self.assertIn('WEEKLY',a)

    def test_upload_guard_rejects_external(self):
        import sys
        root=Path(__file__).resolve().parents[1]
        sys.path.insert(0,str(root/'automation'))
        from net_tls import tls_resilient_post
        with self.assertRaises(ValueError):
            tls_resilient_post('https://not-local.example/api/upload',files={})


class AccessGuardRegressionTests(unittest.TestCase):
    """Minimal stdlib tests of the actual source guard, no company DB needed."""

    def test_desktop_skips_web_device_registration_only_on_loopback(self):
        import ast, os, types, unittest.mock
        root=Path(__file__).resolve().parents[1]
        code=(root/'dashboard'/'app.py').read_text(encoding='utf-8')
        tree=ast.parse(code)
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_access_browser_guard_v632740')
        fn.decorator_list=[]
        scope={'os':os,'request':types.SimpleNamespace(path='/api/completed/summary',host='127.0.0.1:5546')}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<extracted-guard>','exec'),scope)
        # If _access_* is reached in desktop mode, there is an unexpected
        # dependency on multi-user registration.
        with unittest.mock.patch.dict(os.environ,{'SERVEONE_DESKTOP_MODE':'1'}):
            self.assertIsNone(scope[fn.name]())
            scope['request'].host='external.example:5546'
            scope['request'].headers={}
            scope['_access_is_ip_blocked_v632740']=lambda _:False
            scope['_request_ip_v632740']=lambda :'127.0.0.1'
            scope['_access_browser_request_v632740']=lambda :True
            scope['_access_principal_v632740']=lambda :None
            scope['jsonify']=lambda value:value
            result=scope[fn.name]()
            self.assertEqual(result[1],428)
            self.assertTrue(result[0]['registration_required'])
        with unittest.mock.patch.dict(os.environ,{'SERVEONE_DESKTOP_MODE':'0'}):
            scope['request'].host='127.0.0.1:5546'
            self.assertEqual(scope[fn.name]()[1],428)

    def test_desktop_session_precedes_web_filters(self):
        root=Path(__file__).resolve().parents[1]
        r=(root/'desktop_runtime.py').read_text(encoding='utf-8')
        a=r.index('app.before_request_funcs[None].insert(0, desktop_session_guard)')
        b=r.index('def desktop_response_headers')
        self.assertLess(a,b)
        launch=(root/'desktop_launcher.py').read_text(encoding='utf-8')
        self.assertIn("os.environ['SERVEONE_DESKTOP_MODE']='1'",launch)
        start=launch.index('def start_app(port, key):')
        mode=launch.index("os.environ['SERVEONE_DESKTOP_MODE']='1'",start)
        data=launch.index("os.environ['SERVEONE_DATA_DIR']=str(data)",start)
        backend=launch.index("importlib.import_module('app')",start)
        guard=launch.index('install_local_only_guard()',start)
        self.assertLess(mode,guard)
        self.assertLess(data,backend)

    def test_db_status_and_notice_packaged(self):
        root=Path(__file__).resolve().parents[1]
        html=(root/'dashboard'/'static'/'index.html').read_text(encoding='utf-8')
        self.assertIn('/desktop/database_notice.js',html)
        self.assertIn('/api/desktop/database-status',
                      (root/'desktop_runtime.py').read_text(encoding='utf-8'))
        self.assertIn('SQLite yang sah diimpor',
                      (root/'dashboard'/'static'/'desktop'/'database_notice.js').read_text(encoding='utf-8'))


class DesktopEndpointTests(unittest.TestCase):
    def _get_handler(self, name, scope):
        import ast
        root=Path(__file__).resolve().parents[1]
        tree=ast.parse((root/'desktop_runtime.py').read_text(encoding='utf-8'))
        outer=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='register_desktop_routes')
        fn=next(n for n in outer.body if isinstance(n,ast.FunctionDef) and n.name==name)
        fn.decorator_list=[]
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<extracted-endpoint>','exec'),scope)
        return scope[name]

    def test_database_status_missing_empty_and_ready(self):
        import types
        with tempfile.TemporaryDirectory() as d:
            runtime=types.SimpleNamespace(data_dir=Path(d))
            get=self._get_handler('desktop_database_status',{'runtime':runtime,'jsonify':lambda v:v})
            self.assertEqual(get()['status'],'missing')
            dbf=Path(d)/'instance'/'po_database.db'; dbf.parent.mkdir()
            from contextlib import closing
            with closing(sqlite3.connect(dbf)) as con:
                con.execute('CREATE TABLE so_data (id INTEGER PRIMARY KEY, field TEXT)')
                con.commit()
            self.assertEqual(get()['status'],'no_so_data')
            with closing(sqlite3.connect(dbf)) as con:
                con.execute('INSERT INTO so_data(field) VALUES (?)',('synthetic',))
                con.commit()
            data=get()
            self.assertEqual(data['status'],'ready')
            self.assertEqual(data['so_data_rows'],1)

    def test_desktop_session_guard_denies_unauthenticated_api(self):
        import types,hmac
        runtime=types.SimpleNamespace(session_key='secret-local',port=5546)
        request=types.SimpleNamespace(host='127.0.0.1:5546',path='/api/completed/summary',
                                      method='GET',args={},headers={},
                                      cookies={})
        get=self._get_handler('desktop_session_guard',{'runtime':runtime,'request':request,
                                 'hmac':hmac,'jsonify':lambda d:d})
        self.assertEqual(get()[1],401)
        request.cookies={'serveone_desktop_session':'secret-local'}
        self.assertIsNone(get())
        request.host='external.invalid:5546'
        self.assertEqual(get()[1],403)

if __name__=='__main__':unittest.main()
