"""Windows native window entrypoint for the Serveone Dashboard desktop build.

No external-host startup or data transfer. The bundled frontend and database APIs
are served solely to a local pywebview window on 127.0.0.1.
"""
from __future__ import annotations
import argparse
import importlib
import os
import secrets
import shutil
import socket
import sqlite3
import sys
import threading
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
    os.environ['SERVEONE_DATA_DIR']=str(data)
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
    parser.add_argument('--worker',choices=['smro','serp','pur','all'])
    parser.add_argument('--import-db',type=Path)
    parser.add_argument('--scheduled-job',type=str)
    parser.add_argument('--browser',action='store_true',help='Test in browser instead of pywebview')
    args=parser.parse_args()
    # Windows UI prerequisites are checked before importing Flask backend,
    # creating windows, opening the database, or contacting the network.
    # Worker / database import paths are headless and need no WebView2.
    if not (args.worker or args.import_db or args.scheduled_job or args.browser):
        from windows_prerequisites import ensure_gui_requirements
        if not ensure_gui_requirements():
            raise SystemExit(3)
    data=user_data_dir()
    os.environ['SERVEONE_DATA_DIR']=str(data)
    # Set explicitly by the Windows desktop launcher, never in shared source defaults.
    os.environ['SERVEONE_DESKTOP_MODE']='1'
    os.environ.pop('DATABASE_URL',None)  # prevent implicit remote/PostgreSQL connections
    os.environ['DASHBOARD_ALLOWED_ORIGINS']=''
    os.environ['FLASK_DEBUG']='0'
    # No sync on desktop startup; Sheets read-only sync is triggered only by first page visit or Sync Now.
    os.environ['PO_MONITOR_DISABLE_WARMUP']='1'
    os.environ['PO_MONITOR_DISABLE_SCHEDULER']='1'
    os.environ['DASHBOARD_POST_UPLOAD_WARMUP']='0'
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
        # Run with a temporary local Flask server even when the GUI is closed.
        import time
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
    url=f'http://127.0.0.1:{port}/desktop/bootstrap/{secret}'
    if args.browser:
        import webbrowser
        webbrowser.open(url)
        print('Desktop preview. Press Ctrl+C to exit.')
        threading.Event().wait()
    else:
        import webview
        # Prefer near-full screen on laptops; CSS zoom starts at the best fit.
        import ctypes
        screen_width=ctypes.windll.user32.GetSystemMetrics(0) if os.name=='nt' else 1440
        screen_height=ctypes.windll.user32.GetSystemMetrics(1) if os.name=='nt' else 900
        width=max(890,min(1560,screen_width-40))
        height=max(590,min(1000,screen_height-80))
        webview.create_window('Serveone Dashboard',url,width=width,height=height,min_size=(800,560),
                              background_color='#F8FAFC',confirm_close=True)
        webview.start(debug=False,private_mode=False)
    runtime.stop.set()

if __name__=='__main__':main()
