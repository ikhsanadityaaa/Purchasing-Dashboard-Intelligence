"""Windows native window entrypoint for the Serveone Dashboard desktop build.

No external-host startup or data transfer. The bundled frontend and database APIs
are served solely to a local pywebview window on 127.0.0.1.
"""
from __future__ import annotations
try:
    import ssl
    _orig_ssl_context_new = ssl.SSLContext.__new__
    def _safe_ssl_context_new(cls, *args, **kwargs):
        try:
            return _orig_ssl_context_new(cls, *args, **kwargs)
        except FileNotFoundError:
            protocol = kwargs.get('protocol')
            if not protocol and args:
                protocol = args[0]
            if protocol is None:
                protocol = ssl.PROTOCOL_TLS
            return ssl._SSLContext.__new__(cls, protocol)
    ssl.SSLContext.__new__ = _safe_ssl_context_new
except Exception:
    pass
import argparse
import importlib
import os
import secrets
import shutil
import socket
import sqlite3
import sys
import threading
import time
from pathlib import Path
from desktop_runtime import DesktopRuntime, register_desktop_routes, user_data_dir


def source_dir() -> Path:
    return Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent)).resolve()


def import_existing_db(source: Path, destination: Path):
    """Copy a transaction-consistent SQLite snapshot; never edit source DB."""
    if not source.is_file():raise FileNotFoundError(f'Source DB not found: {source}')
    if destination.exists():raise FileExistsError('Local DB already exists. Refusing to overwrite it.')
    destination.parent.mkdir(parents=True, exist_ok=True)
    staging=destination.with_name(destination.name+'.incoming')
    if staging.exists():raise FileExistsError('An incomplete import is present; verify before retrying')
    src=sqlite3.connect(f'file:{source.as_posix()}?mode=ro',uri=True,timeout=30)
    dst=sqlite3.connect(staging,timeout=30)
    try:
        src.backup(dst,pages=500)
        if dst.execute('PRAGMA quick_check').fetchone()[0] != 'ok':
            raise RuntimeError('SQLite quick_check failed')
        dst.commit()
    except Exception:
        src.close();dst.close()
        staging.unlink(missing_ok=True)
        raise
    src.close();dst.close()
    os.replace(staging,destination)


def run_worker(process):
    sid=os.environ.get('SERVEONE_SCHEDULE_ID')
    if sid:
        from desktop_runtime import SettingsStore
        from schedule_policy import within_working_hours
        settings=SettingsStore(user_data_dir()/'settings.json').public()
        schedule=next((s for s in settings['schedules'] if s['id']==sid),None)
        if not settings['automation_enabled'] or not schedule or not schedule['enabled'] or not within_working_hours(settings['working_hours']):
            print('ACTIVITY Skipped | Outside working hours or schedule disabled',flush=True)
            raise SystemExit(75)
    # Use bundled Python source, not the old Tkinter GUI application.
    import runpy
    folder=source_dir()/'automation'
    if not (folder/'main.py').is_file():raise RuntimeError('Automation V43 resources missing')
    sys.path.insert(0,str(folder))
    sys.argv=[str(folder/'main.py'),'--process',process]
    runpy.run_path(str(folder/'main.py'),run_name='__main__')


def reserve_port():
    with socket.socket(socket.AF_INET,socket.SOCK_STREAM) as sock:
        sock.bind(('127.0.0.1',0))
        return sock.getsockname()[1]


def start_app(port, key):
    # PyInstaller uses read-only _MEIPASS; database always points to LocalAppData.
    from waitress import serve
    from desktop_network_guard import install_local_only_guard
    data=user_data_dir()
    data.mkdir(parents=True,exist_ok=True)
    os.environ['SERVEONE_DATA_DIR']=str(data)
    temp = data.parent / 'temp'
    temp.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(temp)
    import tempfile
    tempfile.tempdir = str(temp)
    os.environ['SERVEONE_DESKTOP_MODE']='1'
    os.environ.pop('DATABASE_URL',None)
    os.environ['DASHBOARD_ALLOWED_ORIGINS']=''
    os.environ['FLASK_DEBUG']='0'
    os.environ['PO_MONITOR_DISABLE_WARMUP']='1'
    os.environ['PO_MONITOR_DISABLE_SCHEDULER']='1'
    os.environ['DASHBOARD_POST_UPLOAD_WARMUP']='0'
    install_local_only_guard()
    root=source_dir()
    sys.path.insert(0,str(root/'dashboard'))
    os.environ['SERVEONE_FRONTEND_DIR']=str(root/'dashboard'/'static')
    backend=importlib.import_module('app')
    instance=backend.app
    runtime=DesktopRuntime(data,key,port,root)
    register_desktop_routes(instance,runtime)
    runtime.start_scheduler()
    server=threading.Thread(target=lambda:serve(instance,host='127.0.0.1',port=port,threads=6),
                            name='serveone-local-server',daemon=True)
    server.start()
    return runtime


def ensure_single_instance():
    """Ensure only one GUI instance of ServeoneDashboard runs at a time.
    If another instance is already running, activate its window and exit.
    """
    if os.name != 'nt':
        return None
    import ctypes
    mutex = ctypes.windll.kernel32.CreateMutexW(None, False, "Local\\ServeoneDashboard_GUI_SingleInstance")
    if mutex and ctypes.windll.kernel32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        hwnd = ctypes.windll.user32.FindWindowW(None, "Serveone Dashboard")
        if hwnd:
            ctypes.windll.user32.ShowWindow(hwnd, 9)  # SW_RESTORE
            ctypes.windll.user32.SetForegroundWindow(hwnd)
        sys.exit(0)
    return mutex


def configure_windows_unicode_streams():
    """Console-less Windows builds may default to cp1252 even for Google Sheet
    values and progress messages containing arrows, symbols, or non-Latin text.
    Keep stdout/stderr UTF-8 and replace unrepresentable output rather than
    aborting an otherwise valid data sync. Never alter spreadsheet data itself.
    """
    if os.name != 'nt':return
    for name in ('stdout','stderr'):
        stream=getattr(sys,name,None)
        if stream is None:
            # PyInstaller --windowed can leave both streams as None. A print()
            # in a sync worker must not turn a successful import into a 500.
            setattr(sys,name,open(os.devnull,'w',encoding='utf-8',errors='backslashreplace'))
        elif hasattr(stream,'reconfigure'):
            try:stream.reconfigure(encoding='utf-8',errors='backslashreplace')
            except (OSError,ValueError,AttributeError):pass


def main():
    configure_windows_unicode_streams()
    parser=argparse.ArgumentParser()
    parser.add_argument('--self-check',action='store_true')
    parser.add_argument('--worker',choices=['smro','serp','pur','all'])
    parser.add_argument('--import-db',type=Path)
    parser.add_argument('--scheduled-job',type=str)
    parser.add_argument('--browser',action='store_true',help='Test in browser instead of pywebview')
    args=parser.parse_args()
    # Windows UI prerequisites are checked before importing Flask backend,
    # creating windows, opening the database, or contacting the network.
    # Worker / database import paths are headless and need no WebView2.
    if not (args.worker or args.import_db or args.scheduled_job or args.browser or args.self_check):
        ensure_single_instance()
        from windows_prerequisites import ensure_gui_requirements
        if not ensure_gui_requirements():
            raise SystemExit(3)
    data=user_data_dir()
    data.mkdir(parents=True,exist_ok=True)
    os.environ['SERVEONE_DATA_DIR']=str(data)
    temp = data.parent / 'temp'
    temp.mkdir(parents=True, exist_ok=True)
    os.environ['TEMP'] = os.environ['TMP'] = str(temp)
    import tempfile
    tempfile.tempdir = str(temp)
    # Set explicitly by the Windows desktop launcher, never in shared source defaults.
    os.environ['SERVEONE_DESKTOP_MODE']='1'
    os.environ.pop('DATABASE_URL',None)  # prevent implicit remote/PostgreSQL connections
    os.environ['DASHBOARD_ALLOWED_ORIGINS']=''
    os.environ['FLASK_DEBUG']='0'
    # No sync on desktop startup; Sheets read-only sync is triggered only by first page visit or Sync Now.
    os.environ['PO_MONITOR_DISABLE_WARMUP']='1'
    os.environ['PO_MONITOR_DISABLE_SCHEDULER']='1'
    os.environ['DASHBOARD_POST_UPLOAD_WARMUP']='0'
    if args.self_check:
        import json
        sys.path.insert(0,str(source_dir()/'automation'))
        modules=['main','automation_serp','automation_pur_info_reg','lifecycle','schedule_policy','serveone_storage','win32com.client','webview']
        for module in modules:importlib.import_module(module)
        output={'status':'ok','modules':modules,'data_dir':str(data)}
        (data/'self-check.json').write_text(json.dumps(output),encoding='utf-8')
        return
    if args.worker:
        from worker_lock import run_exclusive
        run_exclusive(
            lambda:run_worker(args.worker),
            on_wait=lambda:print('ACTIVITY Waiting for current automation to finish',flush=True))
        return
    if args.import_db:
        import_existing_db(args.import_db,data/'instance'/'po_database.db')
        print('Database imported to local user data folder.')
        return
    if not (source_dir()/'dashboard'/'static'/'index.html').exists():
        raise RuntimeError('Frontend asset bundle missing')
    port=reserve_port()
    secret=secrets.token_urlsafe(40)
    runtime=start_app(port,secret)
    if args.scheduled_job:
        schedule=next((x for x in runtime.store.public()['schedules'] if x['id']==args.scheduled_job),None)
        if not schedule or not runtime.store.public()['automation_enabled'] or not schedule['enabled']:
            raise RuntimeError('Scheduled process missing or disabled')
        from schedule_policy import within_working_hours
        if not within_working_hours(runtime.store.public()['working_hours']):
            runtime.log('ACTIVITY Skipped | Outside working hours')
            runtime.stop.set()
            return
        # Run with a temporary local Flask server even when the GUI is closed.
        import urllib.request
        for _ in range(100):
            try:
                with urllib.request.urlopen(f'http://127.0.0.1:{port}/desktop/bootstrap/{secret}',timeout=1):break
            except Exception:time.sleep(.1)
        runtime.launch(schedule['process'],schedule_id=schedule['id'])
        while runtime.status_payload()['running'] or runtime.status_payload()['status'] not in ('idle','error'):
            time.sleep(.5)
        runtime.stop.set()
        if runtime.status_payload()['status']=='error':raise RuntimeError(runtime.status_payload()['last_error'])
        return
    # Wait for local waitress server to be ready before opening WebView
    for _ in range(100):
        try:
            with socket.create_connection(('127.0.0.1', port), timeout=0.1):
                break
        except OSError:
            time.sleep(0.05)
    url=f'http://127.0.0.1:{port}/desktop/bootstrap/{secret}'
    if args.browser:
        import webbrowser
        webbrowser.open(url)
        print('Desktop preview. Press Ctrl+C to exit.')
        threading.Event().wait()
    else:
        import webview
        webview.settings['ALLOW_DOWNLOADS'] = True
        # Prefer near-full screen on laptops; CSS zoom starts at the best fit.
        import ctypes
        screen_width=ctypes.windll.user32.GetSystemMetrics(0) if os.name=='nt' else 1440
        screen_height=ctypes.windll.user32.GetSystemMetrics(1) if os.name=='nt' else 900
        width=max(890,min(1560,screen_width-40))
        height=max(590,min(1000,screen_height-80))
        webview.create_window('Serveone Dashboard',url,width=width,height=height,min_size=(800,560),
                              background_color='#F8FAFC',confirm_close=True)
        storage_path = str(data / 'webview_profile')
        os.makedirs(storage_path, exist_ok=True)
        webview.start(debug=False, private_mode=False, storage_path=storage_path)
    runtime.stop.set()

if __name__=='__main__':main()
