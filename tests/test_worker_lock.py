import os
import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from worker_lock import run_exclusive


class WorkerLockTests(unittest.TestCase):
    def test_second_worker_waits_then_runs(self):
        first_started=threading.Event()
        release_first=threading.Event()
        second_started=threading.Event()
        waiting=threading.Event()

        def first():
            first_started.set()
            release_first.wait(2)

        with tempfile.TemporaryDirectory() as folder, patch.dict(
                os.environ, {'SERVEONE_DATA_DIR': folder}):
            one=threading.Thread(target=lambda:run_exclusive(first,poll_seconds=.01))
            two=threading.Thread(target=lambda:run_exclusive(
                second_started.set,on_wait=waiting.set,poll_seconds=.01))
            one.start()
            self.assertTrue(first_started.wait(1))
            two.start()
            self.assertTrue(waiting.wait(1))
            time.sleep(.03)
            self.assertFalse(second_started.is_set())
            release_first.set()
            one.join(1);two.join(1)
            self.assertTrue(second_started.is_set())
            self.assertFalse(one.is_alive())
            self.assertFalse(two.is_alive())
            self.assertTrue((Path(folder)/'automation-worker.lock').is_file())


if __name__=='__main__':unittest.main()
