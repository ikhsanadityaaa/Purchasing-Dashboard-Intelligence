import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from desktop_runtime import DesktopRuntime
from worker_lock import run_exclusive

class MockWorker:
    def __init__(self, code, on_start=None, delay=0.08):
        self.code = code
        self.delay = delay
        self.done = False
        if on_start:
            on_start()

    def wait(self):
        time.sleep(self.delay)
        self.done = True
        return self.code

    def poll(self):
        return self.code if self.done else None


class AutoUpdateQueueTests(unittest.TestCase):
    def test_sequential_queue_execution(self):
        execution_order = []
        concurrent_violations = []
        active = set()
        lock = threading.Lock()

        def make_worker(process):
            def _spawn(*args, **kwargs):
                cmd = args[0] if args else kwargs.get('args', [])
                proc = cmd[-1]
                with lock:
                    if active:
                        concurrent_violations.append((list(active), proc))
                    active.add(proc)
                    execution_order.append(('start', proc))

                def on_finish():
                    with lock:
                        active.discard(proc)
                        execution_order.append(('finish', proc))

                w = MockWorker(0, delay=0.08)
                orig_wait = w.wait
                def custom_wait():
                    res = orig_wait()
                    on_finish()
                    return res
                w.wait = custom_wait
                return w
            return _spawn

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch('desktop_runtime.CredentialStore') as credential, \
                 patch('desktop_runtime.subprocess.Popen', side_effect=make_worker('smro')):
                credential.return_value.load.return_value = ('user', 'pass')
                runtime = DesktopRuntime(root, 'secret', 5022, root)
                runtime.store.save(True, [])

                # 1. Launch SMRO
                p1 = runtime.launch('smro')
                self.assertEqual(p1['process_statuses']['smro'], 'running')

                # 2. While SMRO is running, launch SERP and PUR
                p2 = runtime.launch('serp')
                self.assertEqual(p2['process_statuses']['serp'], 'queued')
                self.assertEqual(p2['process_progress']['serp'], 'Waiting')

                p3 = runtime.launch('pur')
                self.assertEqual(p3['process_statuses']['pur'], 'queued')

                # 3. Wait for all queued jobs to finish
                for _ in range(500):
                    if not runtime.status_payload()['running']:
                        break
                    time.sleep(0.01)

                status = runtime.status_payload()
                self.assertFalse(status['running'])
                self.assertEqual(status['process_statuses']['smro'], 'completed')
                self.assertEqual(status['process_statuses']['serp'], 'completed')
                self.assertEqual(status['process_statuses']['pur'], 'completed')

                # 4. Assert zero collisions
                self.assertEqual(concurrent_violations, [], 'Automations collided during execution')

                # 5. Assert sequential order: smro start/finish, then serp start/finish, then pur start/finish
                starts = [p for action, p in execution_order if action == 'start']
                self.assertEqual(starts, ['smro', 'serp', 'pur'])

    def test_stop_all_clears_queue(self):
        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch('desktop_runtime.CredentialStore') as credential, \
                 patch('desktop_runtime.subprocess.Popen', side_effect=lambda *args, **kwargs: MockWorker(0, delay=0.2)):
                credential.return_value.load.return_value = ('user', 'pass')
                runtime = DesktopRuntime(root, 'secret', 5023, root)
                runtime.store.save(True, [])

                runtime.launch('smro')
                runtime.launch('serp')
                runtime.launch('pur')

                st = runtime.status_payload()
                self.assertEqual(st['process_statuses']['serp'], 'queued')
                self.assertEqual(st['process_statuses']['pur'], 'queued')

                runtime.stop_all()

                st_after = runtime.status_payload()
                self.assertEqual(st_after['process_statuses']['serp'], 'cancelled')
                self.assertEqual(st_after['process_statuses']['pur'], 'cancelled')
                self.assertEqual(len(runtime._run_queue), 0)

    def test_run_all_queued_behind_single_process(self):
        execution_order = []
        lock = threading.Lock()

        def make_worker():
            def _spawn(*args, **kwargs):
                cmd = args[0] if args else kwargs.get('args', [])
                proc = cmd[-1]
                with lock:
                    execution_order.append(('start', proc))
                w = MockWorker(0, delay=0.04)
                orig_wait = w.wait
                def custom_wait():
                    res = orig_wait()
                    with lock:
                        execution_order.append(('finish', proc))
                    return res
                w.wait = custom_wait
                return w
            return _spawn

        with tempfile.TemporaryDirectory() as d:
            root = Path(d)
            with patch('desktop_runtime.CredentialStore') as credential, \
                 patch('desktop_runtime.subprocess.Popen', side_effect=make_worker()):
                credential.return_value.load.return_value = ('user', 'pass')
                runtime = DesktopRuntime(root, 'secret', 5024, root)
                runtime.store.save(True, [])

                # 1. Start serp single process
                runtime.launch('serp')
                # 2. Queue Run All
                payload = runtime.launch('all')
                self.assertTrue(payload['running'])
                self.assertEqual(payload['process_statuses']['pur'], 'queued')

                # 3. Wait for everything to finish
                for _ in range(500):
                    if not runtime.status_payload()['running']:
                        break
                    time.sleep(0.01)

                st = runtime.status_payload()
                self.assertFalse(st['running'])
                self.assertEqual(st['process_statuses']['smro'], 'completed')
                self.assertEqual(st['process_statuses']['serp'], 'completed')
                self.assertEqual(st['process_statuses']['pur'], 'completed')

                starts = [p for action, p in execution_order if action == 'start']
                # First serp, then Run All (smro, serp, pur)
                self.assertEqual(starts, ['serp', 'smro', 'serp', 'pur'])


if __name__ == '__main__':
    unittest.main()
