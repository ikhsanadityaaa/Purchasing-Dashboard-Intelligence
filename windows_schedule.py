"""Optional Windows Task Scheduler registration for unattended automatic updates.

No network access. Tasks are run as the current user, and only after an explicit
opt-in in the desktop Auto Update settings. Does not elevate privileges.
"""
import os
import subprocess
import sys

PREFIX = 'ServeoneDesktop_'
DAY_NAMES = ['MON','TUE','WED','THU','FRI','SAT','SUN']


def task_args(schedule, executable):
    sid=schedule['id']
    task_name=PREFIX+sid
    task_cmd='"'+str(executable)+'" --scheduled-job '+sid
    args=['schtasks','/Create','/F','/TN',task_name,'/TR',task_cmd]
    if schedule['mode']=='interval':
        args += ['/SC','MINUTE','/MO',str(schedule['interval_minutes'])]
    elif schedule['mode']=='daily':
        args += ['/SC','DAILY','/ST',schedule['time']]
    else:
        days=','.join(DAY_NAMES[i] for i in schedule['days'])
        args += ['/SC','WEEKLY','/D',days,'/ST',schedule['time']]
    return args


def sync_windows_tasks(old_schedules, new_schedules, enabled, executable):
    if os.name != 'nt':
        if enabled:raise RuntimeError('Windows Task Scheduler is only available on Windows')
        return
    if not getattr(sys,'frozen',False):
        if enabled:raise RuntimeError('Build and install .exe before enabling Windows Task Scheduler')
        return
    old_ids={s['id'] for s in old_schedules}
    new_ids={s['id'] for s in new_schedules if s['enabled']} if enabled else set()
    errors=[]
    for sid in sorted(old_ids | new_ids):
        if sid not in new_ids:
            res=subprocess.run(['schtasks','/Delete','/TN',PREFIX+sid,'/F'],capture_output=True,text=True,timeout=20)
            # Not-found on cleanup can be ignored: tasks were not necessarily created before.
    for schedule in new_schedules:
        if not enabled or not schedule['enabled']:continue
        args=task_args(schedule,executable)
        res=subprocess.run(args,capture_output=True,text=True,timeout=30)
        if res.returncode:errors.append(schedule['process']+': '+(res.stderr or res.stdout).strip()[:200])
    if errors:raise RuntimeError('Task Scheduler registration failed: '+' | '.join(errors))
