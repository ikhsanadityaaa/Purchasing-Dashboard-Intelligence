"""Run All orchestration, individual date ranges, and real per-step status.
Only synthetic placeholders; no company services or network usage.
"""
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from desktop_runtime import DesktopRuntime

class Worker:
    def __init__(self,code,delay=.05):
        self.code=code
        self.delay=delay
        self.done=False
    def wait(self):
        time.sleep(self.delay)
        self.done=True
        return self.code
    def poll(self):
        return self.code if self.done else None

class RunAllTests(unittest.TestCase):
    def test_each_range_and_green_red_per_step(self):
        calls=[]
        steps={'smro':0,'serp':1,'pur':0}
        def fake_popen(command,**kwargs):
            step=command[-1]
            calls.append((step,dict(kwargs['env'])))
            return Worker(steps[step])
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with patch('desktop_runtime.CredentialStore') as credential, \
                 patch('desktop_runtime.subprocess.Popen',side_effect=fake_popen):
                credential.return_value.load.return_value=('test-user','test-secret')
                runtime=DesktopRuntime(root,'synthetic-key',5033,root)
                custom={
                  'smro':{'preset':'custom','start_date':'2026-05-01','end_date':'today'},
                  'serp':{'preset':'3month','start_date':'','end_date':'today'},
                  'pur':{'preset':'6month','start_date':'','end_date':'today'},
                }
                runtime.store.save(True,[],process_ranges=custom)
                payload=runtime.launch('all')
                self.assertTrue(payload['batch_active'])
                self.assertTrue(payload['running'])
                self.assertTrue(set(payload['process_statuses'].values())<= {'queued','running'})
                for _ in range(100):
                    done=runtime.status_payload()
                    if not done['batch_active']:break
                    time.sleep(.02)
                self.assertFalse(done['running'])
                self.assertEqual(done['process_statuses'],{'smro':'completed','serp':'failed','pur':'completed'})
                self.assertTrue(done['batch_failed'])
                self.assertEqual([step for step,_ in calls],['smro','serp','pur'])
                for _step,env in calls:
                    self.assertEqual(env['SMRO_START_DATE'],'2026/05/01')
                    self.assertEqual(env['SMRO_END_DATE'],'')
                    self.assertEqual(env['SERP_DATE_RANGE_PRESET'],'3month')
                    self.assertEqual(env['PUR_INFO_REG_DATE_RANGE_PRESET'],'6month')
                    self.assertEqual(env['DASHBOARD_API_URL'].split('/api/')[0],'http://127.0.0.1:5033')
                logs=''.join(runtime.tail_log())
                self.assertIn('Completed with errors',logs)
    def test_only_one_run_at_once(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            with patch('desktop_runtime.CredentialStore') as credential, \
                 patch('desktop_runtime.subprocess.Popen',return_value=Worker(0,delay=.3)):
                credential.return_value.load.return_value=('fake','fake')
                runtime=DesktopRuntime(root,'dummy',5011,root)
                runtime.store.save(True,[])
                runtime.launch('all')
                with self.assertRaisesRegex(RuntimeError,'still running'):
                    runtime.launch('smro')
                for _ in range(200):
                    if not runtime.status_payload()['batch_active']:break
                    time.sleep(.01)
    def test_client_ui_process_badges(self):
        src=(Path(__file__).resolve().parents[1]/'dashboard/static/desktop/auto_update.js').read_text('utf8')
        for text in ('zd-process-success','zd-process-failed','zd-process-waiting','zd-spinner','state.process_statuses','process_ranges:process===\'all\'?edited:undefined'):
            self.assertIn(text,src)

if __name__=='__main__':unittest.main()
