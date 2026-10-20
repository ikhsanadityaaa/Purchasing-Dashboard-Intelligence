"""Prevent parallel automation workers from two desktop/task-scheduler instances."""
from pathlib import Path
from desktop_runtime import user_data_dir
import os


def run_exclusive(callback):
    lockpath=user_data_dir()/'automation-worker.lock'
    lockpath.parent.mkdir(parents=True,exist_ok=True)
    with lockpath.open('a+b') as lock:
        lock.seek(0);lock.write(b'X');lock.flush();lock.seek(0)
        if os.name=='nt':
            import msvcrt
            try:msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
            except OSError:raise RuntimeError('Another automation process is already running')
            try:return callback()
            finally:lock.seek(0);msvcrt.locking(lock.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            try:fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('Another automation process is already running')
            try:return callback()
            finally:fcntl.flock(lock.fileno(),fcntl.LOCK_UN)
