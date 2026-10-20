"""V0.1.11 smoke/regression tests: no real company data, services, or browser."""
import io
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from desktop_runtime import DesktopRuntime, SettingsStore

ROOT=Path(__file__).resolve().parents[1]

class CancellableWorker:
    def __init__(self, out=None):
        self.done=threading.Event()
        self.started=threading.Event()
        self.started.set()
        self.stdout=io.StringIO(out or '')
        self.exitcode=-15
    def wait(self):
        self.done.wait(2)
        return self.exitcode
    def poll(self):
        return self.exitcode if self.done.is_set() else None
    def terminate(self):
        self.done.set()

class QuickWorker:
    def __init__(self,code,log):
        self.code=code
        self.finished=threading.Event()
        self.stdout=io.StringIO(log)
    def wait(self):
        time.sleep(.05)
        self.finished.set()
        return self.code
    def poll(self):return self.code if self.finished.is_set() else None

class Operations0111(unittest.TestCase):
    def make_runtime(self,dirname):
        root=Path(dirname)
        runtime=DesktopRuntime(root,'synthetic-key',5033,root)
        runtime.store.save(True,[],headless=False)
        return runtime

    def test_stop_all_terminates_worker_and_skips_queue(self):
        spawned=[]
        fake=CancellableWorker()
        def spawn(*args,**kwargs):
            spawned.append(args[0][-1]);return fake
        with tempfile.TemporaryDirectory() as d, \
          patch('desktop_runtime.CredentialStore') as credential, \
          patch('desktop_runtime.subprocess.Popen',side_effect=spawn):
            credential.return_value.load.return_value=('test','secret')
            rt=self.make_runtime(d)
            rt.launch('all')
            for _ in range(100):
                if spawned:break
                time.sleep(.005)
            self.assertTrue(spawned)
            self.assertFalse(rt.status_payload()['stop_requested'])
            rt.stop_all()
            for _ in range(200):
                if not rt.status_payload()['running']:break
                time.sleep(.01)
            st=rt.status_payload()
            self.assertEqual(spawned,['smro'])
            self.assertFalse(st['running'])
            self.assertTrue(st['stop_requested'])
            self.assertEqual(st['status'],'stopped')
            self.assertEqual(st['process_statuses'],{'smro':'cancelled','serp':'cancelled','pur':'cancelled'})

    def test_headless_false_passed_to_each_worker(self):
        recorded=[]
        def spawn(*args,**kwargs):
            recorded.append(kwargs['env'])
            return QuickWorker(0,'Normal result\n')
        with tempfile.TemporaryDirectory() as d, \
          patch('desktop_runtime.CredentialStore') as credential, \
          patch('desktop_runtime.subprocess.Popen',side_effect=spawn):
            credential.return_value.load.return_value=('test','secret')
            rt=self.make_runtime(d)
            rt.launch('smro')
            for _ in range(200):
                if not rt.status_payload()['running']:break
                time.sleep(.01)
            self.assertEqual(len(recorded),1)
            for prefix in ('SMRO','SERP','PUR_INFO_REG'):
                self.assertEqual(recorded[0][prefix+'_HEADLESS'],'false')
            self.assertFalse(SettingsStore(Path(d)/'settings.json').public()['headless'])

    def test_worker_failure_reason_and_blob_filter(self):
        report='blob:http://example.test/8f317e00-a6ba-44da-9974-78235a31d991\n'*150+\
               'ERROR: VPN not connected; Windows connection refused\n'
        with tempfile.TemporaryDirectory() as d, \
          patch('desktop_runtime.CredentialStore') as credential, \
          patch('desktop_runtime.subprocess.Popen',return_value=QuickWorker(1,report)):
            credential.return_value.load.return_value=('test','secret')
            rt=self.make_runtime(d)
            rt.launch('smro')
            for _ in range(200):
                if not rt.status_payload()['running']:break
                time.sleep(.01)
            payload=rt.status_payload()
            self.assertEqual(payload['process_statuses']['smro'],'failed')
            self.assertIn('VPN not connected',payload['process_errors']['smro'])
            lines=''.join(rt.tail_log(100))
            self.assertIn('Browser noise hidden',lines)
            self.assertNotIn('blob:http',lines)

    def test_sidebar_outranks_table_floats_but_not_modals_and_loading_toast(self):
        shell=(ROOT/'dashboard/static/index.html').read_text('utf8')
        self.assertIn('aside.nav-side {position:fixed!important;z-index:9996!important',shell)
        self.assertIn('#root{--nav-collapsed:3.5rem;--nav-expanded:13rem}',shell)
        self.assertIn('#root main{margin-left:var(--nav-collapsed)!important}',shell)
        self.assertIn('#z-loading-toast.z-show{display:flex!important}',shell)
        self.assertIn('/desktop/auto_update.js?v=18',shell)
        tablecss=next((ROOT/'dashboard/static/assets').glob('index-*.css')).read_text('utf8')
        self.assertIn('.global-table-float-head{position:fixed',tablecss)
        self.assertIn('z-index:9994',tablecss)
        self.assertIn('z-index:10030',tablecss)

    def test_stop_all_ui_and_scheduler_process_ranges(self):
        ui=(ROOT/'dashboard/static/desktop/auto_update.js').read_text('utf8')
        css=(ROOT/'dashboard/static/desktop/auto_update.css').read_text('utf8')
        for required in ('id="zd-stop-all"','stopAll()','/api/desktop/stop-all','id="zd-headless"',
                         "headless:$('#zd-headless').checked",'state.process_errors','state.process_progress',
                         'label.textContent=',
                         "$('#zd-schedule-range').hidden=all"):
            self.assertIn(required,ui)
        self.assertIn('.zd-btn.zd-stop{background:#dc2626;color:#fff',css)
        core=(ROOT/'desktop_runtime.py').read_text('utf8')
        for required in ("env[prefix+'_HEADLESS']", "if process == 'all':",'def stop_all(self):'):
            self.assertIn(required,core)

if __name__=='__main__':unittest.main()
