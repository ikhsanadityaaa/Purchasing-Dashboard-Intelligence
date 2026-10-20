"""Tests use synthetic data and never connect to corporate services."""
import ast
import json
import os
import tempfile
import types
import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch
from desktop_runtime import SettingsStore, validate_schedule, validate_range, apply_range_env, describe_range
from desktop_secrets import CredentialStore

ROOT=Path(__file__).resolve().parents[1]

class DateRangeTests(unittest.TestCase):
    def test_presets_per_process(self):
        for preset in ('1month','3month','6month','this_year','custom'):
            self.assertEqual(validate_range({'preset':preset,'start_date':'2026-01-02'})['preset'],preset)
        with self.assertRaises(ValueError):validate_range({'preset':'custom','start_date':''})
        with self.assertRaises(ValueError):validate_range({'preset':'custom','start_date':'2026-03-05','end_date':'2026-01-01'})
        self.assertEqual(validate_range({'preset':'custom','start_date':'2026-01-01','end_date':''})['end_date'],'today')

    def test_env_respects_process_range(self):
        env={}
        apply_range_env(env,'smro',{'preset':'custom','start_date':'2026-01-15','end_date':''})
        self.assertEqual(env['SMRO_START_DATE'],'2026/01/15')
        self.assertEqual(env['SMRO_END_DATE'],'')
        self.assertNotIn('SERP_START_DATE',env)
        apply_range_env(env,'serp',{'preset':'this_year'})
        self.assertEqual(env['SERP_DATE_RANGE_PRESET'],'this_year')
        self.assertEqual(env['SMRO_DATE_RANGE_PRESET'],'custom')

    def test_activity_range_uses_exact_dates(self):
        today=datetime(2026,10,16).date()
        self.assertEqual(describe_range({'preset':'1month'},today),'2026-09-16 to 2026-10-16')
        self.assertEqual(describe_range({'preset':'this_year'},today),'2026-01-01 to 2026-10-16')
        self.assertEqual(describe_range({'preset':'custom','start_date':'2026-08-01','end_date':'2026-08-31'},today),'2026-08-01 to 2026-08-31')

    def test_prior_settings_migrate_without_losing_schedules(self):
        with tempfile.TemporaryDirectory() as d:
            path=Path(d)/'settings.json'
            old={'automation_enabled':True,'windows_scheduler_enabled':False,'schedules':[
                {'process':'smro','mode':'daily','time':'06:00','days':[0,1,2,3,4],
                 'interval_minutes':120,'start_date':'2024-01-01','end_date':'today'}],'run_history':{}}
            path.write_text(json.dumps(old),encoding='utf-8')
            store=SettingsStore(path)
            self.assertEqual(store.public()['schedules'][0]['range_preset'],'custom')
            self.assertEqual(store.public()['process_ranges']['smro']['preset'],'1month')
            r=store.public()['process_ranges']
            r['smro']={'preset':'custom','start_date':'2026-01-01','end_date':'today'}
            store.save(True,store.public()['schedules'],process_ranges=r)
            self.assertEqual(SettingsStore(path).public()['process_ranges']['smro']['start_date'],'2026-01-01')
            self.assertEqual(len(SettingsStore(path).public()['schedules']),1)

class SecretStoreTests(unittest.TestCase):
    def test_password_is_not_persisted_plaintext(self):
        def fake_crypto(b,decrypt=False):return b[::-1]
        with tempfile.TemporaryDirectory() as d, patch('desktop_secrets._crypt',side_effect=fake_crypto):
            f=Path(d)/'credentials.dpapi'; store=CredentialStore(f)
            self.assertFalse(store.status()['configured'])
            store.save('ID-ABCD','this-is-a-fake-test-password')
            self.assertNotIn(b'this-is-a-fake-test-password',f.read_bytes())
            self.assertNotIn(b'ID-ABCD',f.read_bytes())
            self.assertEqual(store.load(),('ID-ABCD','this-is-a-fake-test-password'))
            self.assertEqual(store.status(),{'configured':True,'username':'ID-ABCD'})

class AuthUploadTests(unittest.TestCase):
    def _guard(self,request):
        tree=ast.parse((ROOT/'desktop_runtime.py').read_text(encoding='utf-8'))
        outer=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='register_desktop_routes')
        fn=next(n for n in outer.body if isinstance(n,ast.FunctionDef) and n.name=='desktop_session_guard')
        fn.decorator_list=[]
        runtime=types.SimpleNamespace(port=9876,session_key='session_secret',upload_ui_key='ui-upload-token')
        scope={'runtime':runtime,'request':request,'jsonify':lambda d:d,'hmac':__import__('hmac')}
        exec(compile(ast.Module(body=[fn],type_ignores=[]),'<test-guard>','exec'),scope)
        return scope['desktop_session_guard']()

    def request(self,method='POST',path='/api/upload/smro',headers=None,cookies=None,args=None):
        return types.SimpleNamespace(host='127.0.0.1:9876',path=path,method=method,
                        headers=headers or {},cookies=cookies or {},args=args or {})

    def test_manual_upload_token_is_scoped_and_authenticated(self):
        head={'X-Serveone-Desktop-Upload':'ui-upload-token','Origin':'http://127.0.0.1:9876','Sec-Fetch-Site':'same-origin'}
        self.assertIsNone(self._guard(self.request(headers=head)))
        # WebView2 may omit Origin/Referer/Sec-Fetch headers on loopback XHR.
        self.assertIsNone(self._guard(self.request(headers={'X-Serveone-Desktop-Upload':'ui-upload-token'})))
        self.assertEqual(self._guard(self.request(headers={'X-Serveone-Desktop-Upload':'ui-upload-token','Referer':'https://evil.invalid/'}))[1],401)
        self.assertEqual(self._guard(self.request(headers={**head,'Origin':'https://evil.invalid'}))[1],401)
        self.assertEqual(self._guard(self.request(headers={**head,'Sec-Fetch-Site':'cross-site'}))[1],401)
        self.assertIsNone(self._guard(self.request(headers=head,path='/api/desktop/settings')))
        self.assertIsNone(self._guard(self.request(headers=head,path='/api/upload/job-status/fake',method='GET')))
        self.assertEqual(self._guard(self.request(headers=head,path='/desktop/auto_update.js'))[1],401)
        self.assertIsNone(self._guard(self.request(headers=head,method='GET')))
        self.assertEqual(self._guard(self.request(headers={**head,'X-Serveone-Desktop-Upload':'invalid'}))[1],401)
        self.assertEqual(self._guard(self.request(headers={},args={'desktop_key':'session_secret'})),None)
        self.assertEqual(self._guard(self.request(headers=head,cookies={'serveone_desktop_session':'session_secret'})),None)

    def test_token_not_stored_in_static_html(self):
        html=(ROOT/'dashboard/static/index.html').read_text(encoding='utf-8')
        self.assertIn('X-Serveone-Desktop-Upload',html)
        self.assertIn('withCredentials = true',html)
        code=(ROOT/'desktop_runtime.py').read_text(encoding='utf-8')
        self.assertIn("response.set_data(html.replace('</head>', script+'</head>', 1))",code)
        self.assertIn('response.direct_passthrough=False',code)
        self.assertIn("response.headers['Cache-Control']='no-store'",code)
        self.assertNotIn('ui-upload-token',html)

class UIChecks(unittest.TestCase):
    def test_ui_parts_exist(self):
        js=(ROOT/'dashboard/static/desktop/auto_update.js').read_text(encoding='utf-8')
        css=(ROOT/'dashboard/static/desktop/auto_update.css').read_text(encoding='utf-8')
        for token in ['zd-tab-scheduler','zd-tab-processes','zd-tab-activity','zd-username','zd-password','zd-save-credentials','zd-schedule','zd-logs','process_ranges']:
            self.assertIn(token,js)
        self.assertIn('log.scrollTop=log.scrollHeight',js)
        self.assertIn('height:min(300px,42vh)',css)
        self.assertIn('zd-clock',js)

if __name__=='__main__':unittest.main()
