"""Prevent parallel automation workers from two desktop/task-scheduler instances."""
from pathlib import Path
from desktop_runtime import user_data_dir
import os
import time


def run_exclusive(callback, on_wait=None, poll_seconds=1):
    lockpath=user_data_dir()/'automation-worker.lock'
    lockpath.parent.mkdir(parents=True,exist_ok=True)
    with lockpath.open('a+b') as lock:
        lock.seek(0);lock.write(b'X');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            waiting=False
            while True:
                try:
                    lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
                    break
                except OSError as exc:
                    if exc.errno not in (11,13,35):raise
                    if not waiting and on_wait:on_wait()
                    waiting=True
                    time.sleep(poll_seconds)
            try:return callback()
            finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            waiting=False
            while True:
                try:
                    fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    if not waiting and on_wait:on_wait()
                    waiting=True
                    time.sleep(poll_seconds)
            try:return callback()
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
