"""Serveone desktop local-only control layer (no company data sent outside PC).

The source dashboard is registered as-is; the only new endpoints are /api/desktop/*.
The scheduling metadata stores no credentials. Actual automation account secrets live
in a local .env file and are never included in exports or ZIP artifacts.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from pathlib import Path
from threading import RLock, Thread, Event
from urllib.parse import urlsplit
import collections
import copy
import hmac
import json
import os
import re
import secrets
import subprocess
import sys
import time
import uuid
from desktop_secrets import CredentialStore
from desktop_sheet_sync import VendorSnapshotStore, SheetSyncCoordinator
from desktop_google_readonly import GoogleReadOnlyAccess

PROCESSES = {'smro': 'Search Client Odr', 'serp': 'All Product ID', 'pur': 'Prod Reg Status', 'all': 'Run All Process'}
DAYS = (0,1,2,3,4,5,6)
RANGE_PRESETS = ('1month', '3month', '6month', 'this_year', 'custom')
DEFAULT_RANGE = {'preset': '1month', 'start_date': '', 'end_date': 'today'}
DEFAULT_SETTINGS = {'automation_enabled': False, 'windows_scheduler_enabled': False,
                    'schedules': [], 'run_history': {}, 'process_completed_at': {},
                    'process_ranges': {p: dict(DEFAULT_RANGE) for p in ('smro', 'serp', 'pur')},
                    'headless': True}


def validate_range(raw, legacy=False):
    if not isinstance(raw, dict):
        raise ValueError('Date range must be an object')
    preset = str(raw.get('preset', 'custom' if legacy else '1month')).lower().strip()
    if preset not in RANGE_PRESETS:
        raise ValueError('Invalid date range preset')
    start = str(raw.get('start_date') or '').strip()
    end = str(raw.get('end_date') or 'today').strip()
    if end == '': end = 'today'
    if preset == 'custom' and not start:
        raise ValueError('Custom date range requires a start date')
    for label, value in (('start_date',start),('end_date',end)):
        if not value or (label == 'end_date' and value == 'today'):
            continue
        try:
            d = datetime.strptime(value, '%Y-%m-%d')
            if d.strftime('%Y-%m-%d') != value:
                raise ValueError()
        except ValueError as exc:
            raise ValueError(label + ' must be YYYY-MM-DD') from exc
    if preset == 'custom' and end != 'today' and end < start:
        raise ValueError('End date is before start date')
    return {'preset': preset, 'start_date': start, 'end_date': end}


def apply_range_env(env, process, chosen):
    normalized = validate_range(chosen)
    prefixes = ('SMRO','SERP','PUR_INFO_REG') if process == 'all' else (
        {'smro':'SMRO','serp':'SERP','pur':'PUR_INFO_REG'}[process],)
    for prefix in prefixes:
        env[prefix + '_DATE_RANGE_PRESET'] = normalized['preset']
        env[prefix + '_START_DATE'] = normalized['start_date'].replace('-', '/')
        env[prefix + '_END_DATE'] = '' if normalized['end_date'] == 'today' else normalized['end_date'].replace('-', '/')
    return env


def describe_range(chosen, today=None):
    value=validate_range(chosen)
    end=today or datetime.now().date()
    preset=value['preset']
    if preset == '1month':start=end-timedelta(days=30)
    elif preset == '3month':start=end-timedelta(days=90)
    elif preset == '6month':start=end-timedelta(days=180)
    elif preset == 'this_year':start=end.replace(month=1,day=1)
    else:
        start=datetime.strptime(value['start_date'],'%Y-%m-%d').date()
        if value['end_date'] != 'today':end=datetime.strptime(value['end_date'],'%Y-%m-%d').date()
    return f'{start.isoformat()} to {end.isoformat()}'



def user_data_dir() -> Path:
    override = os.environ.get('SERVEONE_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if os.name == 'nt':
        return Path(os.environ.get('LOCALAPPDATA', str(Path.home()/'AppData'/'Local'))) / 'ServeoneDashboardDesktop'
    return Path.home() / '.serveone-dashboard-desktop'


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.tmp')
    tmp.write_text(json.dumps(value, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(tmp, path)


def validate_schedule(raw: dict) -> dict:
    if not isinstance(raw, dict): raise ValueError('Schedule must be an object')
    p = str(raw.get('process', '')).lower()
    if p not in PROCESSES: raise ValueError('Invalid process')
    mode = str(raw.get('mode', 'daily')).lower()
    if mode not in {'daily','weekly','interval'}: raise ValueError('Invalid schedule type')
    time_text = str(raw.get('time', '06:00'))
    try:
        datetime.strptime(time_text, '%H:%M')
    except ValueError as exc: raise ValueError('Time must be HH:MM') from exc
    days = raw.get('days', list(DAYS))
    if not isinstance(days, list) or any(type(d) is not int or d not in DAYS for d in days) or len(days) != len(set(days)):
        raise ValueError('Days must be unique values 0–6')
    if mode == 'weekly' and not days: raise ValueError('Choose at least one day')
    interval = raw.get('interval_minutes', 120)
    if type(interval) is not int or not 30 <= interval <= 1440: raise ValueError('Interval must be 30–1440 minutes')
    date_from = str(raw.get('start_date', '2024-01-01'))
    date_to = str(raw.get('end_date', 'today'))
    for label, v in [('start_date',date_from),('end_date',date_to)]:
        if label == 'end_date' and v == 'today': continue
        try: datetime.strptime(v,'%Y-%m-%d')
        except ValueError as exc: raise ValueError(label + ' must be YYYY-MM-DD') from exc
    if date_to != 'today' and date_to < date_from: raise ValueError('End date is before start date')
    sid = str(raw.get('id') or uuid.uuid4().hex)
    if len(sid) != 32 or any(c not in '0123456789abcdef' for c in sid): raise ValueError('Invalid schedule ID')
    return {'id': sid,'process':p,'mode':mode,'time':time_text,
            'days':sorted(days),'interval_minutes':interval,'start_date':date_from,
            'end_date':date_to,'enabled':bool(raw.get('enabled', True)),
            'range_preset':validate_range({'preset':raw.get('range_preset','custom'),
                            'start_date':date_from,'end_date':date_to})['preset']}


class SettingsStore:
    def __init__(self, path: Path):
        self.path=path
        self.lock=RLock()
        self.data=copy.deepcopy(DEFAULT_SETTINGS)
        if path.exists():
            loaded=json.loads(path.read_text(encoding='utf-8'))
            self.data['automation_enabled'] = bool(loaded.get('automation_enabled', False))
            self.data['windows_scheduler_enabled'] = bool(loaded.get('windows_scheduler_enabled', False))
            self.data['headless'] = bool(loaded.get('headless', True))
            self.data['schedules']=[validate_schedule(v) for v in loaded.get('schedules', [])]
            self.data['run_history'] = loaded.get('run_history', {}) if isinstance(loaded.get('run_history', {}),dict) else {}
            completed = loaded.get('process_completed_at', {})
            self.data['process_completed_at'] = ({p:str(completed[p]) for p in ('smro','serp','pur')
                                                   if isinstance(completed,dict) and completed.get(p)})
            process_ranges = loaded.get('process_ranges') or {}
            self.data['process_ranges'] = {p:validate_range(process_ranges.get(p, DEFAULT_RANGE))
                                           for p in ('smro','serp','pur')}

    def public(self):
        with self.lock:
            return {'automation_enabled':self.data['automation_enabled'],
                    'windows_scheduler_enabled':self.data['windows_scheduler_enabled'],
                    'headless':self.data['headless'],
                    'schedules':copy.deepcopy(self.data['schedules']),
                    'process_completed_at':copy.deepcopy(self.data['process_completed_at']),
                    'process_ranges':copy.deepcopy(self.data['process_ranges'])}

    def save(self, automation_enabled, schedules, windows_scheduler_enabled=False, process_ranges=None, headless=True):
        if not isinstance(schedules,list) or len(schedules)>120: raise ValueError('Too many schedules')
        normalized=[validate_schedule(v) for v in schedules]
        normalized_ranges = {p:validate_range((process_ranges or self.data['process_ranges']).get(p, DEFAULT_RANGE))
                             for p in ('smro','serp','pur')}
        if len({v['id'] for v in normalized}) != len(normalized): raise ValueError('Duplicate schedule ID')
        from windows_schedule import sync_windows_tasks
        with self.lock:
            old_settings=copy.deepcopy(self.data)
            # Task creation can fail due to IT restrictions; report failure without
            # changing the saved settings.
            sync_windows_tasks(self.data['schedules'], normalized,
                bool(windows_scheduler_enabled and automation_enabled), sys.executable)
            old_ids={s['id'] for s in self.data['schedules']}
            for s in normalized:
                if s['id'] not in old_ids:
                    # Avoid firing overdue newly-created daily/interval jobs immediately.
                    self.data['run_history'][s['id']]=datetime.now().isoformat(timespec='seconds')
            self.data['automation_enabled']=bool(automation_enabled)
            self.data['windows_scheduler_enabled']=bool(windows_scheduler_enabled and automation_enabled)
            self.data['schedules']=normalized
            self.data['process_ranges']=normalized_ranges
            self.data['headless']=bool(headless)
            atomic_json(self.path,self.data)
            return self.public()

    def note_run(self, sid, when=None):
        with self.lock:
            self.data['run_history'][sid] = (when or datetime.now()).isoformat(timespec='seconds')
            atomic_json(self.path,self.data)

    def note_completed(self, process, when=None):
        if process not in ('smro','serp','pur'):return
        with self.lock:
            self.data['process_completed_at'][process]=(when or datetime.now()).isoformat(timespec='seconds')
            atomic_json(self.path,self.data)

    def due(self, now:datetime):
        with self.lock:
            if not self.data['automation_enabled'] or self.data['windows_scheduler_enabled']: return []
            due=[]
            for s in self.data['schedules']:
                if not s['enabled']:continue
                last_s=self.data['run_history'].get(s['id'])
                try:last=datetime.fromisoformat(last_s) if last_s else None
                except ValueError:last=None
                if s['mode']=='interval':
                    if last is None or (now-last).total_seconds()>=60*s['interval_minutes']:
                        due.append(s)
                elif now.strftime('%H:%M')>=s['time'] and (last is None or last.date()<now.date()):
                    if s['mode']=='daily' or now.weekday() in s['days']:
                        due.append(s)
            return due


class DesktopRuntime:
    def __init__(self, data_dir: Path, session_key: str, port:int, source_dir:Path):
        self.data_dir=data_dir
        self.port=port
        self.session_key=session_key
        self.source_dir=source_dir
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.store=SettingsStore(self.data_dir/'settings.json')
        self.credentials=CredentialStore(self.data_dir/'smro_credentials.dpapi')
        self.google_credentials=GoogleReadOnlyAccess(self.data_dir)
        # Legacy service-account key is obsolete; keep database and settings.
        # Delete only this old DPAPI secret file, never local business data.
        try:(self.data_dir/'google_service_account.dpapi').unlink(missing_ok=True)
        except OSError:pass
        self.vendor_snapshot=VendorSnapshotStore(self.data_dir/'vendor_control_snapshot.dpapi')
        self.sheet_sync=None
        if os.name == 'nt':
            for stream in (sys.stdout, sys.stderr):
                if hasattr(stream, 'reconfigure'):
                    try: stream.reconfigure(encoding='utf-8',errors='backslashreplace')
                    except (OSError,ValueError,AttributeError): pass
        self.upload_ui_key=secrets.token_urlsafe(36)
        self.logfile=self.data_dir/'logs'/'automation.log'
        self.logfile.parent.mkdir(exist_ok=True)
        self.lock=RLock()
        self.running=None
        self.status='idle'
        self.last_error=''
        self.stop=Event()
        self.batch_active=False
        self.current_process_id=None
        self.process_statuses={key:'idle' for key in ('smro','serp','pur')}
        self.process_progress={key:'' for key in ('smro','serp','pur')}
        self.batch_failed=False
        self.cancel_requested=False
        self.process_errors={p:'' for p in ('smro','serp','pur')}

    def log(self, message):
        line=f'[{datetime.now().isoformat(timespec="seconds")}] {message}\n'
        with self.lock:
            with self.logfile.open('a',encoding='utf-8') as f:f.write(line)

    def status_payload(self):
        with self.lock:
            p=self.running
            alive=bool((p and p.poll() is None) or self.batch_active)
            settings=self.store.public()
            return {'process_statuses':dict(self.process_statuses),
                    'process_progress':dict(self.process_progress),
                    'process_completed_at':settings['process_completed_at'],
                    'batch_active':bool(self.batch_active),'batch_failed':bool(self.batch_failed),
                    'stop_requested':self.cancel_requested,
                    'process_errors':dict(self.process_errors),
                    'current_process_id':self.current_process_id,
                    'version':'V63.27.73-desktop-preview','status':'running' if alive else self.status,
                    'current_process':(PROCESSES.get(self.current_process_id,self.status) if alive else None),
                    'last_error':self.last_error,'running':alive,'storage':str(self.data_dir),
                    'offline_import':True,'scheduled_while_closed':settings['windows_scheduler_enabled']}

    @staticmethod
    def _clean_log_line(line):
        # Human-facing activity must not expose the session capability, passwords,
        # bearer tokens, or endless ephemeral Chrome blob URLs.
        line=re.sub(r'blob:(?:https?://[^\s\"\']+|null)/[0-9a-f-]{12,}', '[browser blob URL]', line, flags=re.I)
        line=re.sub(r'(?i)(desktop_key|desktop_ui_ticket|access_token|refresh_token|password|token)=([^\s&]+)',r'\1=[redacted]',line)
        line=re.sub(r'(?i)(Authorization:\s*Bearer\s+)\S+',r'\1[redacted]',line)
        return line[:1500] + ('…\n' if len(line)>1500 else '')

    @staticmethod
    def _progress_text(line):
        text=str(line or '').strip()
        text=re.sub(r'^\[\d{4}-\d\d-\d\dT[^\]]+\]\s*', '', text)
        text=re.sub(r'^\[(?:smro|serp|pur)\]\s*', '', text, flags=re.I)
        text=re.sub(r'^\d{4}-\d\d-\d\d[ T]\d\d:\d\d:\d\d(?:,\d+)?\s*-\s*(?:INFO|WARNING|PERINGATAN|ERROR|DEBUG)\s*-\s*', '', text, flags=re.I)
        if text.startswith('ACTIVITY '):return text[9:]
        lower=text.lower()
        step=re.match(r'^\[(\d+)/(\d+)\]', text)
        attempt=re.search(r'attempt\s+(\d+)/(\d+)', text, flags=re.I)
        filename=re.search(r'([^\\/:*?"<>|\s]+\.xlsx?)', text, flags=re.I)
        size_bytes=re.search(r'\((\d+)\s+bytes\)', text, flags=re.I)
        size_kb=re.search(r'\(([\d.]+)\s+KB(?:,|\))', text, flags=re.I)
        size=''
        if size_bytes:size=f'{int(size_bytes.group(1)) / 1048576:.1f} MB'
        elif size_kb:size=f'{float(size_kb.group(1)) / 1024:.1f} MB'
        date_range=re.search(r'(?:processing date|default range all product id|isi register date):\s*(\d{4}[-/.]\d\d[-/.]\d\d)\s*(?:-|s/d|to)\s*(\d{4}[-/.]\d\d[-/.]\d\d)', text, flags=re.I)
        row_count=re.search(r'(?:hasil search|result|found)\D*([\d,]+)\s+rows?\b', text, flags=re.I)
        if date_range:return f'Date range: {date_range.group(1).replace(".", "-").replace("/", "-")} to {date_range.group(2).replace(".", "-").replace("/", "-")}'
        if re.search(r'upload gagal|import failed', lower):
            http_status=re.search(r'http\s+(\d{3})', lower)
            reason='SQLite parameter limit exceeded' if 'too many sql variables' in lower else (f'HTTP {http_status.group(1)}' if http_status else 'See error details')
            tries=f' | attempt {attempt.group(1)}/{attempt.group(2)}' if attempt else ''
            return f'Import failed | {reason}{tries}'
        if re.search(r'upload sukses|local import berhasil|import complete', lower):
            counts=[]
            for label in ('added','updated'):
                match=re.search(rf'\b{label}=([\d,]+)', text, flags=re.I)
                if match:counts.append(f'{int(match.group(1).replace(",", "")):,} {label}')
            return 'Import complete' + (f' | {" | ".join(counts)}' if counts else '')
        if re.search(r'local import|upload dashboard|updating local|importing', lower):
            details=[filename.group(1) if filename else 'local data']
            if size:details.append(size)
            if attempt:details.append(f'attempt {attempt.group(1)}/{attempt.group(2)}')
            return 'Importing ' + ' | '.join(details)
        if row_count:return f'Found {int(row_count.group(1).replace(",", "")):,} rows'
        if filename and re.search(r'file excel stabil|\bfile:', lower):
            return f'Downloaded {filename.group(1)}' + (f' | {size}' if size else '')
        if 'run all complete with errors' in lower:return 'Completed with errors'
        if 'transient browser blob entries omitted' in lower:return 'Browser noise hidden'
        if re.search(r'\b(stop|stopped|cancel|cancelled|dibatalkan|dihentikan)\b', lower):return 'Stopping'
        if 'vpn' in lower:return 'VPN connected' if re.search(r'(connected|terhubung)', lower) else 'Waiting for VPN'
        if re.search(r'(retry|mencoba ulang|coba lagi|attempt \d+)', lower):return 'Retrying' + (f' | attempt {attempt.group(1)}/{attempt.group(2)}' if attempt else '')
        if re.search(r'(sqlite|database|saved locally|disimpan lokal|saving)', lower):return 'Saving data'
        if re.search(r'(excel|\.xls\b|\.xlsx\b|download)', lower):return 'Downloading data'
        if re.search(r'(t-?code|transaction|ok-code)', lower):return 'Opening SAP transaction'
        if re.search(r'(\.sap\b|sap gui|sap session|sap siap|opening sap|membuka sap)', lower):return 'Opening SAP'
        if re.search(r'(result|hasil search|rows?\b|records?\b)', lower):return 'Processing results'
        if re.search(r'(processing date|search|query|filter)', lower):return 'Searching data'
        if re.search(r'(login|sign in|username|password)', lower):return 'Signing in'
        if re.search(r'(chrome|browser|webdriver|portal)', lower):return 'Opening browser'
        if re.search(r'(menu|form|page|tab)', lower):return 'Opening process'
        if re.search(r'\b(complete|completed|success|successful|done|selesai|berhasil)\b', lower):return 'Completed'
        if step:return f'Processing step {step.group(1)} of {step.group(2)}'
        if re.search(r'\b(start|starting|started|mulai)\b', lower):return 'Starting'
        return ''

    @classmethod
    def _activity_text(cls, line):
        text=str(line or '').strip()
        match=re.match(r'^\[(\d{4}-\d\d-\d\dT(\d\d:\d\d:\d\d))\]\s*(?:\[([^\]]+)\]\s*)?(.*)$', text)
        if not match:return text + ('\n' if text else '')
        process=match.group(3)
        message=cls._progress_text(match.group(4))
        prefix=f'[{match.group(2)}]' + (f' [{process}]' if process else '')
        return f'{prefix} {message}\n'

    @classmethod
    def _is_activity_event(cls, line):
        return bool(cls._progress_text(line))

    def tail_log(self, limit=80):
        if not self.logfile.exists():return []
        with self.logfile.open('r',encoding='utf-8',errors='replace') as f:
            raw=list(collections.deque(f,maxlen=min(max(limit*5,250),1500)))
        result=[]
        blob_count=0
        previous=None
        repeated=0
        def flush():
            if repeated:result.append(f'[Activity] {repeated} repeated browser log lines hidden\n')
        for line in raw:
            if ('blob:' in line.lower() or '[browser blob url]' in line.lower()) and not re.search(r'(?i)(error|failed|exception|traceback|critical)',line):
                blob_count+=1
                continue
            safe_line=self._clean_log_line(line)
            if not self._is_activity_event(safe_line):continue
            clean=self._activity_text(safe_line)
            if clean==previous:
                repeated+=1
                continue
            flush();repeated=0;result.append(clean);previous=clean
        flush()
        if blob_count:result.append(f'[Activity] {blob_count} transient browser blob entries omitted (not separate processes)\n')
        if not result:
            fallback=[self._activity_text(self._clean_log_line(line)) for line in raw if line.strip()]
            result=fallback[-min(limit,5):]
        return result[-limit:]

    def clear_log(self):
        with self.lock:
            self.logfile.write_text('', encoding='utf-8')
        return {'cleared': True}

    def _failure_reason(self, offset, exit_code):
        """Read only THIS worker's log section and return a useful safe cause."""
        try:
            with self.logfile.open('rb') as stream:
                stream.seek(0,os.SEEK_END)
                # Keep the newest worker errors even after a very chatty run.
                stream.seek(max(0,offset,stream.tell()-131072))
                fragment=stream.read(131072).decode('utf-8',errors='replace')
            lines=[self._clean_log_line(x).strip() for x in fragment.splitlines() if x.strip()]
            lines=[x for x in lines if not ('blob:' in x.lower() or '[browser blob URL]' in x)]
            def error_score(value):
                if re.search(r'(?i)(traceback|exception|\bERROR\b|sqlite|too many sql variables)',value):return 3
                if re.search(r'(?i)(failed|gagal|HTTP [45]\d\d|denied|timeout|not found|invalid|required|refused|permission)',value):return 2
                if re.search(r'(?i)vpn.*(not connected|disconnected|failed|required)',value):return 1
                return 0
            candidates=[(error_score(x),index,x) for index,x in enumerate(lines) if error_score(x)]
            detail=(max(candidates)[2] if candidates else (lines[-1] if lines else '')).strip()
            if 'too many sql variables' in detail.lower():
                detail='SQLite parameter limit exceeded while matching Product IDs'
            return (detail[:240] if detail else f'Worker exited with code {exit_code}; open Activity Log for details')
        except (OSError,UnicodeError):
            return f'Worker exited with code {exit_code}; log unavailable'

    @staticmethod
    def _stop_worker_tree(child, windows=None):
        """Cancel a real Windows Popen process tree; safely handle test workers.

        subprocess.Popen exposes pid. Unit-test stand-ins are not OS processes
        and may expose only terminate(), so never pass an unknown PID to taskkill.
        """
        if child is None or child.poll() is not None:
            return
        if windows is None:
            windows = os.name == 'nt'
        pid = getattr(child, 'pid', None)
        if windows and type(pid) is int and pid > 0:
            # /T kills Selenium/Chrome descendants, /F prevents orphan workers.
            outcome = subprocess.run(
                ['taskkill', '/PID', str(pid), '/T', '/F'],
                capture_output=True, timeout=12, check=False,
                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
            if outcome.returncode == 0 or child.poll() is not None:
                return
        # Fallback covers failed taskkill and in-memory synthetic worker mocks.
        child.terminate()

    def stop_all(self):
        """Explicit user cancellation; stop active worker tree and skip pending batch."""
        with self.lock:
            if not self.batch_active and not (self.running and self.running.poll() is None):
                return self.status_payload()
            self.cancel_requested = True
            self.status = 'stopping'
            child = self.running
            self.log('STOP ALL REQUESTED by user')
        if child and child.poll() is None:
            try:
                self._stop_worker_tree(child)
            except (OSError, subprocess.SubprocessError, ValueError, AttributeError) as exc:
                self.log('Stop warning: ' + str(exc)[:200])
                # Even if taskkill cannot run, try to cancel the direct worker.
                try:
                    if child.poll() is None:
                        child.terminate()
                except (OSError, AttributeError) as fallback_exc:
                    self.log('Stop fallback warning: ' + str(fallback_exc)[:150])
        return self.status_payload()

    def launch(self, process:str, schedule_id=None, date_range=None, ranges_override=None):
        if process not in PROCESSES: raise ValueError('Invalid automation process')
        if not self.store.public()['automation_enabled']:
            raise PermissionError('Enable automation in Auto Update settings first')
        # Never start a worker with blank/forgotten credentials.
        username, password = self.credentials.load()
        if not username or not password:
            raise ValueError('SMRO ID/password missing. Save credentials in Auto Update > Processes first.')
        with self.lock:
            if self.batch_active or (self.running is not None and self.running.poll() is None):
                raise RuntimeError('Another automation is still running')
            if getattr(sys,'frozen',False):
                command=[sys.executable,'--worker',process]
            else:
                command=[sys.executable,str(self.source_dir/'desktop_launcher.py'),'--worker',process]
            env=os.environ.copy()
            env['SMRO_USERNAME']=username
            env['SMRO_PASSWORD']=password
            env['SMRO_SHOW_POPUP']='false'
            for prefix in ('SMRO','SERP','PUR_INFO_REG'):
                env[prefix+'_HEADLESS']='true' if self.store.public()['headless'] else 'false'
            env['NO_PROXY']='127.0.0.1,localhost'
            env['no_proxy']='127.0.0.1,localhost'
            # Hard override: old remote upload endpoints can never be used.
            root=f'http://127.0.0.1:{self.port}'
            env['DASHBOARD_API_URL']=root+'/api/upload/smro?desktop_key='+self.session_key
            env['SERP_DASHBOARD_API']=root+'/api/upload/product-id?desktop_key='+self.session_key
            env['PUR_INFO_REG_DASHBOARD_API']=root+'/api/upload/item-registration?desktop_key='+self.session_key
            env['SMRO_UPDATE_DASHBOARD']='true'
            env['SERP_UPDATE_DASHBOARD']='true'
            env['PUR_INFO_REG_UPDATE_DASHBOARD']='true'
            # Each manual process has its own range. A schedule can override it,
            # without affecting the saved ranges for other processes.
            ranges = self.store.public()['process_ranges']
            selected_ranges={}
            if ranges_override is not None:
                if process != 'all' or not isinstance(ranges_override, dict):
                    raise ValueError('Per-process ranges are available only for Run All')
                for p in ('smro','serp','pur'):
                    selected_ranges[p]=validate_range(ranges_override.get(p,ranges[p]))
                    apply_range_env(env,p,selected_ranges[p])
            elif date_range is not None:
                selected_ranges[process]=validate_range(date_range)
                apply_range_env(env, process, selected_ranges[process])
            elif schedule_id:
                schedule=next((s for s in self.store.public()['schedules'] if s['id']==schedule_id),None)
                if not schedule:
                    raise ValueError('Scheduled job not found')
                if process == 'all':
                    # Run All ignores the schedule's legacy date range and uses
                    # independently configured ranges from Processes.
                    for p in ('smro','serp','pur'):
                        selected_ranges[p]=validate_range(ranges[p])
                        apply_range_env(env,p,selected_ranges[p])
                else:
                    selected_ranges[process]=validate_range({'preset':schedule['range_preset'],
                        'start_date':schedule['start_date'], 'end_date':schedule['end_date']})
                    apply_range_env(env, process, selected_ranges[process])
            else:
                targets=('smro','serp','pur') if process == 'all' else (process,)
                for p in targets:
                    selected_ranges[p]=validate_range(ranges[p])
                    apply_range_env(env,p,selected_ranges[p])
            trigger='Scheduled' if schedule_id else 'Manual'
            display_mode='Headless' if self.store.public()['headless'] else 'Visible browser'
            self.cancel_requested=False
            self.process_errors={p:'' for p in ('smro','serp','pur')}
            self.process_progress={p:'' for p in ('smro','serp','pur')}
            if process == 'all':
                # A single worker --process all hides individual results and
                # spawns children that may not survive PyInstaller freezing.
                # Orchestrate three sequential desktop workers here instead.
                self.batch_active=True
                self.batch_failed=False
                self.process_statuses={p:'queued' for p in ('smro','serp','pur')}
                self.process_progress={p:'Waiting' for p in ('smro','serp','pur')}
                self.current_process_id=None
                self.status=PROCESSES['all']
                self.last_error=''
                self.log(f'ACTIVITY Run All started | Trigger: {trigger} | Mode: {display_mode}')
            else:
                self.batch_failed=False
                self.batch_active=False
                self.process_statuses={p:'idle' for p in ('smro','serp','pur')}
                self.process_statuses[process]='running'
                self.process_progress[process]='Starting'
                self.current_process_id=process
                self.status=PROCESSES[process]
                self.last_error=''
                self.log(f'ACTIVITY Started | Process: {PROCESSES[process]} | Range: {describe_range(selected_ranges[process])} | Trigger: {trigger} | Mode: {display_mode}')
            def spawn(one):
                child_command=command[:-1]+[one]
                kwargs={'env':env,'cwd':str(self.source_dir),
                        'stdout':subprocess.PIPE,'stderr':subprocess.STDOUT,
                        'text':True,'encoding':'utf-8','errors':'replace','bufsize':1}
                if os.name=='nt':kwargs['creationflags']=getattr(subprocess,'CREATE_NO_WINDOW',0)
                child=subprocess.Popen(child_command,**kwargs)
                output=getattr(child,'stdout',None)
                if output is not None:
                    def consume():
                        blobs=0
                        repeated=0
                        last=None
                        try:
                            for line in output:
                                if 'blob:' in line.lower() and not re.search(r'(?i)(error|failed|exception|traceback|critical)',line):
                                    blobs+=1
                                    continue
                                clean=self._clean_log_line(line).rstrip('\n')
                                if not clean.strip():continue
                                if clean==last:
                                    repeated+=1
                                    continue
                                if repeated:self.log(f'[{one}] {repeated} repeated entries omitted')
                                repeated=0
                                last=clean
                                progress=self._progress_text(clean)
                                if progress:
                                    with self.lock:self.process_progress[one]=progress
                                self.log(f'[{one}] {clean}')
                        except (OSError,ValueError) as exc:
                            self.log(f'[{one}] log stream stopped: {type(exc).__name__}')
                        finally:
                            if repeated:self.log(f'[{one}] {repeated} repeated entries omitted')
                            if blobs:self.log(f'[{one}] {blobs} transient browser blob entries omitted')
                            try:output.close()
                            except (OSError,ValueError):pass
                    pump=Thread(target=consume,daemon=True,name='log-'+one)
                    pump.start()
                    child._serveone_log_pump=pump
                return child
            def wait_child(child):
                rc=child.wait()
                pump=getattr(child,'_serveone_log_pump',None)
                if pump:pump.join(timeout=2)
                return rc
            if process!='all':
                try:
                    worker_offset=self.logfile.stat().st_size
                    self.running=spawn(process)
                except Exception:
                    self.process_statuses[process]='failed'
                    self.current_process_id=None
                    self.status='error'
                    raise
                runner=self.running

        if process=='all':
            def run_batch():
                # Sequential prevents racing SAP GUI, VPN and SQLite writes.
                # Failures are visible and do not mask following processes.
                had_failure=False
                for one in ('smro','serp','pur'):
                    with self.lock:
                        if self.cancel_requested:
                            self.process_statuses[one]='cancelled'
                            continue
                        self.current_process_id=one
                        self.process_statuses[one]='running'
                        self.process_progress[one]='Starting'
                        self.status=PROCESSES[one]
                        self.log(f'ACTIVITY Started | Process: {PROCESSES[one]} | Range: {describe_range(selected_ranges[one])} | Trigger: {trigger} | Mode: {display_mode}')
                    try:
                        with self.lock:
                            child_offset=self.logfile.stat().st_size
                            if self.cancel_requested:
                                self.process_statuses[one]='cancelled'
                                continue
                            self.running=spawn(one)
                            child=self.running
                        rc=wait_child(child)
                    except Exception as exc:
                        rc=1
                        self.process_errors[one]=(type(exc).__name__+': '+str(exc))[:240]
                        self.log('ERROR '+PROCESSES[one]+': '+str(exc)[:180])
                    with self.lock:
                        cancelled=self.cancel_requested
                        self.process_statuses[one]='cancelled' if cancelled else ('completed' if rc==0 else 'failed')
                        self.process_progress[one]='Stopped' if cancelled else ('Completed' if rc==0 else 'Failed')
                        if rc==0 and not cancelled:self.store.note_completed(one)
                        self.running=None
                        if rc and not cancelled:
                            had_failure=True
                            reason=self.process_errors.get(one) or self._failure_reason(child_offset if 'child_offset' in locals() else 0,rc)
                            self.process_errors[one]=reason
                            self.last_error=f'{PROCESSES[one]}: {reason}'
                            self.log('FAILURE '+PROCESSES[one]+': '+reason)
                        result='Stopped' if cancelled else ('Success' if rc==0 else 'Failed')
                        detail=f' | Error: {reason}' if rc and not cancelled else ''
                        self.log(f'ACTIVITY Finished | Process: {PROCESSES[one]} | Result: {result}{detail}')
                with self.lock:
                    self.batch_active=False
                    self.batch_failed=had_failure
                    self.current_process_id=None
                    self.status='stopped' if self.cancel_requested else ('error' if had_failure else 'idle')
                    result='Stopped' if self.cancel_requested else ('Completed with errors' if had_failure else 'Success')
                    self.log(f'ACTIVITY Run All finished | Result: {result}')
            Thread(target=run_batch,daemon=True,name='serveone-run-all').start()
        else:
            def finished():
                rc=wait_child(runner)
                with self.lock:
                    cancelled=self.cancel_requested
                    self.process_statuses[process]='cancelled' if cancelled else ('completed' if rc==0 else 'failed')
                    self.process_progress[process]='Stopped' if cancelled else ('Completed' if rc==0 else 'Failed')
                    if rc==0 and not cancelled:self.store.note_completed(process)
                    if rc and not cancelled:
                        self.status='error'
                        reason=self._failure_reason(worker_offset,rc)
                        self.process_errors[process]=reason
                        self.last_error=f'{PROCESSES[process]}: {reason}'
                        self.log('FAILURE '+PROCESSES[process]+': '+reason)
                    else:
                        self.status='stopped' if cancelled else 'idle'
                    self.current_process_id=None
                    self.running=None
                    result='Stopped' if cancelled else ('Success' if rc==0 else 'Failed')
                    detail=f' | Error: {reason}' if rc and not cancelled else ''
                    self.log(f'ACTIVITY Finished | Process: {PROCESSES[process]} | Result: {result}{detail}')
            Thread(target=finished,daemon=True).start()
        if schedule_id:self.store.note_run(schedule_id)
        return self.status_payload()

    def scheduler_loop(self):
        while not self.stop.wait(15):
            try:
                for s in self.store.due(datetime.now()):
                    if not self.status_payload()['running']:
                        self.launch(s['process'],schedule_id=s['id'])
                        break
            except Exception as e:
                self.log('Scheduler error: '+str(e)[:150])

    def start_scheduler(self):
        Thread(target=self.scheduler_loop,daemon=True,name='desktop-scheduler').start()


def register_desktop_routes(app, runtime:DesktopRuntime):
    from flask import request,jsonify,make_response,redirect,send_from_directory
    from pathlib import Path
    from functools import wraps
    import socket

    @app.before_request
    def desktop_session_guard():
        host = request.host.split(':')[0]
        if host not in ('127.0.0.1','localhost','[::1]'):
            return jsonify({'error':'Localhost only'}),403
        if request.path.startswith('/desktop/bootstrap/'):
            return None
        # Local worker: a distinct per-run capability accepted ONLY on the three
        # import endpoints. Browser tokens are never accepted on external hosts.
        worker_routes = ('/api/upload/smro','/api/upload/product-id',
                         '/api/upload/item-registration')
        if request.method == 'POST' and request.path in worker_routes:
            key=request.args.get('desktop_key','')
            if key and hmac.compare_digest(key,runtime.session_key):
                return None

        # The WebView can reload at SPA paths rather than '/'. Authenticated
        # HTML receives an ephemeral UI capability, with no permanent secret.
        # For manual import, send the capability explicitly in the request URL
        # as well as a header. This avoids XHR adapter/header differences.
        api_token=request.headers.get('X-Serveone-Desktop-Upload','')
        upload_routes=worker_routes + ('/api/upload/master-pic',)
        poll_path=request.path.startswith('/api/upload/job-status/')
        local_import_request=(request.path in upload_routes and request.method == 'POST') or (poll_path and request.method == 'GET')
        if local_import_request and not api_token:
            api_token=request.args.get('desktop_ui_ticket','')
        origin=(request.headers.get('Origin') or '').rstrip('/')
        fetch_site=request.headers.get('Sec-Fetch-Site','')
        referer=(request.headers.get('Referer') or '')
        accepted_origins=(f'http://127.0.0.1:{runtime.port}',f'http://localhost:{runtime.port}')
        # The unguessable per-launch capability is the CSRF protection.
        # WebView2 may omit both Origin and Referer for same-origin XHR, so
        # do not reject those requests solely for lacking browser metadata.
        # Explicitly cross-origin requests still remain blocked.
        same_origin=(origin in accepted_origins if origin else
                     (fetch_site in ('','same-origin','none') and
                      (not referer or any(referer.startswith(base+'/') for base in accepted_origins))))
        if (request.path.startswith('/api/') and api_token
            and hmac.compare_digest(api_token,runtime.upload_ui_key)
            and same_origin
            and fetch_site in ('','same-origin','none')):
            return None
        cookie=request.cookies.get('serveone_desktop_session','')
        if not cookie or not hmac.compare_digest(cookie,runtime.session_key):
            return jsonify({'error':'Desktop session required'}),401
        if request.method in ('POST','PUT','PATCH','DELETE'):
            origin=request.headers.get('Origin')
            if origin and origin.rstrip('/') not in (f'http://127.0.0.1:{runtime.port}',f'http://localhost:{runtime.port}'):
                return jsonify({'error':'Cross-origin request blocked'}),403

    # Flask runs before_request callbacks in registration order. The source
    # dashboard installed its web Access Monitor callbacks during import. Make
    # local session authentication the FIRST guard so no dashboard API can
    # respond before desktop_session_guard checks the session cookie.
    app.before_request_funcs[None].remove(desktop_session_guard)
    app.before_request_funcs[None].insert(0, desktop_session_guard)

    @app.after_request
    def desktop_response_headers(response):
        # Keep company data and assets within the bundled desktop shell.
        response.headers['Content-Security-Policy'] = (
            "default-src 'self' data: blob:; connect-src 'self'; img-src 'self' data: blob:; "
            "font-src 'self' data:; script-src 'self' 'unsafe-inline' blob:; "
            "style-src 'self' 'unsafe-inline'; form-action 'self'; frame-ancestors 'none'"
        )
        response.headers['X-Content-Type-Options']='nosniff'
        if (request.method=='GET' and response.status_code==200
                and response.mimetype=='text/html' and
                response.headers.get('Content-Disposition', '').lower().find('attachment')<0):
            # This response is behind the HttpOnly desktop session guard.
            # Do not place the token inside static assets or log files.
            script='<script>window.__serveoneDesktopUploadToken='+json.dumps(runtime.upload_ui_key)+'</script>'
            # send_from_directory/send_file responses are direct_passthrough.
            # Disable it before reading/writing the small HTML shell.
            response.direct_passthrough=False
            html=response.get_data(as_text=True)
            if '</head>' in html and '<div id="root"' in html:
                response.set_data(html.replace('</head>', script+'</head>', 1))
            response.headers['Cache-Control']='no-store'
        return response

    @app.route('/desktop/bootstrap/<key>')
    def desktop_bootstrap(key):
        if not hmac.compare_digest(key,runtime.session_key): return jsonify({'error':'Unauthorized'}),403
        response=make_response(redirect('/'))
        response.set_cookie('serveone_desktop_session',key,httponly=True,samesite='Strict',secure=False,path='/')
        response.headers['Cache-Control']='no-store'
        return response

    @app.route('/desktop/<path:filename>')
    def desktop_assets(filename):
        return send_from_directory(str(runtime.source_dir/'dashboard'/'static'/'desktop'),filename)

    @app.route('/api/desktop/settings',methods=['GET','PUT'])
    def desktop_settings():
        if request.method=='GET':return jsonify(runtime.store.public())
        try:
            j=request.get_json(silent=True) or {}
            return jsonify(runtime.store.save(j.get('automation_enabled',False), j.get('schedules',[]),
                j.get('windows_scheduler_enabled',False),j.get('process_ranges'),j.get('headless',True)))
        except (ValueError,TypeError) as e:return jsonify({'error':str(e)}),400

    @app.route('/api/desktop/credentials',methods=['GET','PUT'])
    def desktop_credentials():
        if request.method=='GET':
            try:return jsonify(runtime.credentials.status())
            except RuntimeError as e:return jsonify({'error':str(e)}),503
        j=request.get_json(silent=True) or {}
        try:
            username=str(j.get('username') or '').strip()
            password=str(j.get('password') or '')
            if not password:
                old_username,old_password=runtime.credentials.load()
                if username==old_username and old_password:
                    return jsonify(runtime.credentials.status())
                raise ValueError('Enter password when saving a new SMRO ID')
            return jsonify(runtime.credentials.save(username,password))
        except ValueError as e:return jsonify({'error':str(e)}),400
        except (OSError,RuntimeError) as e:return jsonify({'error':str(e)}),503

    @app.route('/api/desktop/status')
    def desktop_status():return jsonify(runtime.status_payload())

    @app.route('/api/desktop/database-status')
    def desktop_database_status():
        # Information only. No company records leave the local app. Do not
        # create an empty SQLite file while checking status.
        import sqlite3
        from contextlib import closing
        filename = runtime.data_dir / 'instance' / 'po_database.db'
        if not filename.is_file():
            return jsonify({'status':'missing','has_so_data':False})
        try:
            uri = filename.resolve().as_uri() + '?mode=ro'
            with closing(sqlite3.connect(uri, uri=True, timeout=2)) as conn:
                exists = conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='so_data'").fetchone()
                n = (conn.execute('SELECT COUNT(*) FROM so_data').fetchone()[0]
                     if exists else 0)
            return jsonify({'status':'ready' if n else 'no_so_data',
                            'has_so_data':bool(n),'so_data_rows':n})
        except (sqlite3.Error, OSError):
            return jsonify({'status':'unreadable','has_so_data':False}),503

    @app.route('/api/desktop/import-diagnostics')
    def desktop_import_diagnostics():
        """Local aggregate SO counts; no individual SO, supplier or customer values."""
        import sqlite3
        from contextlib import closing
        filename = runtime.data_dir / 'instance' / 'po_database.db'
        if not filename.is_file():
            return jsonify({'status':'missing', 'message':'Local SQLite database not found'})
        try:
            uri=filename.resolve().as_uri()+'?mode=ro'
            with closing(sqlite3.connect(uri,uri=True,timeout=3)) as conn:
                tables={r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                result={'status':'ready','database':'local', 'so_data_rows':0,
                        'so_last_2_months':0,'completed_last_2_months':0,
                        'latest_so_date':None,'history_rows':0}
                if 'so_data' in tables:
                    cols={r[1] for r in conn.execute('PRAGMA table_info(so_data)')}
                    result['so_data_rows']=conn.execute('SELECT COUNT(*) FROM so_data').fetchone()[0]
                    if 'so_create_date' in cols:
                        result['latest_so_date']=conn.execute('SELECT MAX(so_create_date) FROM so_data').fetchone()[0]
                        result['so_last_2_months']=conn.execute("SELECT COUNT(*) FROM so_data WHERE date(so_create_date)>=date('now','-2 months')").fetchone()[0]
                        if 'so_status' in cols:
                            result['completed_last_2_months']=conn.execute("SELECT COUNT(*) FROM so_data WHERE date(so_create_date)>=date('now','-2 months') AND so_status='Delivery Completed'").fetchone()[0]
                if 'so_history_lite' in tables:
                    result['history_rows']=conn.execute('SELECT COUNT(*) FROM so_history_lite').fetchone()[0]
            return jsonify(result)
        except (sqlite3.Error, OSError):
            return jsonify({'status':'unreadable','message':'Cannot inspect local SQLite database'}),503

    # V0.1.8 — Explicit Google access belongs to the sync/edit thread only.
    import sys
    backend=sys.modules.get('app')
    if backend is not None:
        backend._DESKTOP_GOOGLE_READONLY_ACCESS=runtime.google_credentials
        backend._DESKTOP_VENDOR_SNAPSHOT_LOADER=runtime.vendor_snapshot.load
        backend._DESKTOP_VENDOR_SNAPSHOT_EDITOR=runtime.vendor_snapshot.edit
        runtime.sheet_sync=SheetSyncCoordinator(runtime.google_credentials,runtime.vendor_snapshot,backend,app)

    @app.route('/api/desktop/sheets/credentials',methods=['GET','PUT'])
    def desktop_sheets_credentials():
        if request.method=='GET':return jsonify(runtime.google_credentials.status())
        try:
            payload=request.get_json(silent=True)
            if not isinstance(payload,dict):raise ValueError('Expected Google OAuth Desktop configuration')
            if 'client_id' in payload:
                payload=runtime.google_credentials.desktop_client_from_fields(payload.get('client_id'), payload.get('client_secret',''))
            return jsonify(runtime.google_credentials.save(payload))
        except (ValueError,KeyError,TypeError) as exc:return jsonify({'error':str(exc)}),400
        except RuntimeError as exc:return jsonify({'error':str(exc)}),503

    @app.route('/api/desktop/sheets/connect',methods=['POST'])
    def desktop_sheets_connect():
        try:
            started=runtime.google_credentials.connect()
            return jsonify({'started':started,'credentials':runtime.google_credentials.status()}),202 if started else 200
        except RuntimeError as exc:return jsonify({'error':str(exc)}),400

    @app.route('/api/desktop/sheets/disconnect',methods=['POST'])
    def desktop_sheets_disconnect():
        return jsonify(runtime.google_credentials.disconnect())

    @app.route('/api/desktop/sheets/status',methods=['GET'])
    def desktop_sheets_status():
        response=jsonify(runtime.sheet_sync.status())
        response.headers['Cache-Control']='no-store'
        return response

    @app.route('/api/desktop/sheets/sync',methods=['POST'])
    def desktop_sheets_sync():
        try:
            value=request.get_json(silent=True) or {}
            if not isinstance(value,dict):raise ValueError('Invalid sync request')
            result=runtime.sheet_sync.start(value.get('page'),value.get('mode','once'))
            return jsonify(result),202 if result['accepted'] else 200
        except ValueError as exc:return jsonify({'error':str(exc)}),400

    # No Google Sheet network permission is granted for local cell edits.
    # Desktop always saves edits locally, NEVER writes them to Sheets.

    # V0.1.7: lightweight local revision signal. The dashboard's existing
    # upload pipeline already publishes a tiny data_revision.txt after commit;
    # no Excel, SQL rows or company records are copied to this endpoint.
    @app.route('/api/desktop/data-revision', methods=['GET'])
    def desktop_data_revision():
        marker=runtime.data_dir/'instance'/'data_revision.txt'
        try:
            revision=marker.read_text(encoding='ascii').strip()[:80]
        except (OSError, UnicodeError):
            revision=''
        response=jsonify({'revision':revision})
        response.headers['Cache-Control']='no-store, max-age=0'
        return response

    # Local edits done outside the async import queue must publish revisions,
    # too. An HTTP 202 only means queued; it is NEVER a committed update.
    # Data-changing APIs are whitelisted; no refresh on GET/status/login.
    @app.after_request
    def desktop_publish_inline_changes(response):
        path=request.path or ''
        prefixes=('/api/upload/', '/api/data/so/', '/api/item-registration/',
                  '/api/rfq/', '/api/import/', '/api/vendor-control/',
                  '/api/cost-reduction/')
        if (request.method in ('POST','PUT','PATCH','DELETE')
            and 200 <= response.status_code < 300 and response.status_code != 202
            and path.startswith(prefixes)
            and '/template' not in path and '/export' not in path
            and '/debug' not in path and '/status' not in path):
            # Queue replay uses its own post-commit publisher; avoid emitting a
            # data revision while an async worker is still processing.
            try:
                import sys
                backend=sys.modules.get('app')
                if backend is not None and not getattr(backend._UPLOAD_JOB_EXECUTING,'on',False):
                    backend._soft_invalidate_response_caches()
                    backend._publish_data_revision()
            except (AttributeError, OSError) as exc:
                app.logger.warning('Could not publish desktop data revision: %s',exc)
        return response

    @app.route('/api/desktop/logs')
    def desktop_logs():return jsonify({'lines':runtime.tail_log(40)})

    @app.route('/api/desktop/logs/clear',methods=['POST'])
    def desktop_logs_clear():return jsonify(runtime.clear_log())

    @app.route('/api/desktop/stop-all',methods=['POST'])
    def desktop_stop_all():
        return jsonify(runtime.stop_all())

    @app.route('/api/desktop/run',methods=['POST'])
    def desktop_run():
        try:
            j=request.get_json(silent=True) or {}
            date_range = j.get('date_range')
            if date_range is not None:
                validate_range(date_range)
            overrides=j.get('process_ranges')
            if overrides is not None:
                if not isinstance(overrides,dict):raise ValueError('Invalid process ranges')
                for key, value in overrides.items():
                    if key not in ('smro','serp','pur'):raise ValueError('Unknown range process')
                    validate_range(value)
            return jsonify(runtime.launch(str(j.get('process','')),date_range=date_range,ranges_override=overrides)),202
        except PermissionError as e:return jsonify({'error':str(e)}),403
        except RuntimeError as e:return jsonify({'error':str(e)}),409
        except ValueError as e:return jsonify({'error':str(e)}),400
