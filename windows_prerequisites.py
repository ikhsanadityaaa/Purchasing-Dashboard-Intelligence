"""Windows launch-time prerequisite check / offline WebView2 repair.

Python, Flask, Python packages and app assets must be bundled by PyInstaller.
Only Microsoft WebView2 Runtime is installed on demand from the *signed offline*
installer packaged under support/. No downloads and no company-data transfer.
"""
from __future__ import annotations

import os
from pathlib import Path
import queue
import subprocess
import sys
import threading

WEBVIEW2_GUID = '{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}'
WEBVIEW2_KEY = 'Software\\Microsoft\\EdgeUpdate\\Clients\\' + WEBVIEW2_GUID
DOTNET_KEY = r'SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full'
DOTNET_MIN_RELEASE = 394802  # .NET Framework 4.6.2; Windows 10/11 ship with newer versions.
WEBVIEW2_INSTALLER = 'MicrosoftEdgeWebView2RuntimeInstallerX64.exe'


def _registry_reader(registry=None):
    if registry is not None:
        return registry
    if os.name != 'nt':
        return None
    import winreg
    return winreg


def webview2_installed(registry=None) -> bool:
    """Check the Microsoft-published per-user and per-machine EdgeUpdate keys.

    Explicitly query 32-bit registry view on 64-bit Windows for machine keys.
    This is not equivalent to checking whether Edge browser is installed.
    """
    reg = _registry_reader(registry)
    if reg is None:
        return False
    access = [(reg.HKEY_CURRENT_USER, 0)]
    access.extend((reg.HKEY_LOCAL_MACHINE, view) for view in
                  (getattr(reg, 'KEY_WOW64_32KEY', 0), getattr(reg, 'KEY_WOW64_64KEY', 0)))
    for hive, view in access:
        try:
            with reg.OpenKey(hive, WEBVIEW2_KEY, 0, reg.KEY_READ | view) as key:
                version, _ = reg.QueryValueEx(key, 'pv')
            if str(version).strip() not in ('', '0.0.0.0', 'None'):
                return True
        except (OSError, PermissionError):
            continue
    return False


def dotnet_framework_available(registry=None) -> bool:
    reg = _registry_reader(registry)
    if reg is None:
        return False
    for view in (getattr(reg, 'KEY_WOW64_64KEY', 0), getattr(reg, 'KEY_WOW64_32KEY', 0)):
        try:
            with reg.OpenKey(reg.HKEY_LOCAL_MACHINE, DOTNET_KEY, 0, reg.KEY_READ | view) as key:
                release, _ = reg.QueryValueEx(key, 'Release')
            if isinstance(release, int) and release >= DOTNET_MIN_RELEASE:
                return True
        except (OSError, PermissionError):
            continue
    return False


def missing_components(registry=None) -> list[str]:
    if os.name != 'nt' and registry is None:
        return []
    missing = []
    if not dotnet_framework_available(registry):
        missing.append('.NET Framework 4.6.2 or newer')
    if not webview2_installed(registry):
        missing.append('Microsoft Edge WebView2 Runtime')
    return missing


def bundled_webview2_installer() -> Path:
    """Support is beside the installed exe, not in PyInstaller _MEIPASS."""
    folder = Path(sys.executable).resolve().parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
    return folder / 'support' / WEBVIEW2_INSTALLER


def microsoft_signature_valid(path: Path) -> bool:
    """Fail closed: verify trusted Microsoft Authenticode signature before launch."""
    if not path.is_file() or path.stat().st_size < 10_000_000:
        return False
    literal = str(path).replace("'", "''")
    script = (
        "$s=Get-AuthenticodeSignature -LiteralPath '" + literal + "'; "
        "if ($s.Status -eq 'Valid' -and $s.SignerCertificate -and "
        "$s.SignerCertificate.Subject -match 'O=Microsoft Corporation') {exit 0} else {exit 1}"
    )
    try:
        check = subprocess.run(
            ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', script],
            capture_output=True, text=True, timeout=90, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0)
        )
        return check.returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


def install_offline_webview2(installer: Path | None = None) -> tuple[bool, str]:
    installer = installer or bundled_webview2_installer()
    if not installer.exists():
        return False, 'Bundled Microsoft WebView2 installer is missing. Run the full Serveone Dashboard Setup again.'
    if not microsoft_signature_valid(installer):
        return False, 'WebView2 installer signature could not be verified as valid Microsoft software.'
    try:
        completed = subprocess.run([str(installer), '/silent', '/install'], timeout=900)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, 'Cannot run Microsoft WebView2 installer: ' + str(exc)
    if completed.returncode not in (0, 3010):
        return False, f'Microsoft WebView2 installation failed (exit code {completed.returncode}).'
    if not webview2_installed():
        return False, 'Installer completed, but Windows did not register the WebView2 Runtime. A restart may be required.'
    return True, 'WebView2 Runtime is ready.'


def _native_error(message: str) -> None:
    try:
        import ctypes
        ctypes.windll.user32.MessageBoxW(None, message, 'Serveone Dashboard - Requirements', 0x10)
    except Exception:
        print(message, file=sys.stderr)


def ensure_gui_requirements() -> bool:
    """Called before starting Flask or pywebview; no UI when fully satisfied.

    Missing WebView2: button installs bundled/offline Microsoft signed package.
    Missing .NET: explicit safe diagnostic; does not download unapproved packages.
    """
    if os.name != 'nt':
        return True  # Linux/macOS only used for source tests; not a production build.
    problems = missing_components()
    if not problems:
        return True
    if '.NET Framework 4.6.2 or newer' in problems:
        _native_error('This PC needs .NET Framework 4.6.2 or newer. '
                      'Please use Windows Update or contact your IT administrator. '
                      'Serveone Dashboard has not been started.')
        return False
    installer = bundled_webview2_installer()
    if not installer.is_file():
        _native_error('Microsoft WebView2 Runtime is missing and the offline repair package '
                      'is not included with this build. Re-run the complete Serveone Dashboard Setup.exe.')
        return False
    try:
        import tkinter as tk
        from tkinter import ttk
        root = tk.Tk()
    except Exception:
        _native_error('Microsoft WebView2 Runtime is missing. Re-run Serveone Dashboard Setup.exe '
                      'to install the bundled offline Microsoft component.')
        return False
    root.title('Serveone Dashboard | Required Components')
    root.resizable(False, False)
    root.geometry('520x245')
    root.configure(bg='#F8FAFC')
    result = {'ok': False}
    messages: queue.Queue[tuple[bool, str]] = queue.Queue()
    frame = ttk.Frame(root, padding=22)
    frame.pack(fill='both', expand=True)
    ttk.Label(frame, text='Additional component required', font=('Segoe UI', 13, 'bold')).pack(anchor='w', pady=(0, 10))
    ttk.Label(frame, text='Microsoft Edge WebView2 Runtime is needed to display the dashboard.',
              wraplength=470).pack(anchor='w', pady=(0, 8))
    ttk.Label(frame, text='The official Microsoft installer is already included. No internet or separate download is needed.',
              wraplength=470).pack(anchor='w')
    info = tk.StringVar(value='1 component missing. Click Install Required Components.')
    ttk.Label(frame, textvariable=info, wraplength=470).pack(anchor='w', pady=(14, 8))
    progress = ttk.Progressbar(frame, mode='indeterminate')
    progress.pack(fill='x', pady=(0, 8))
    controls = ttk.Frame(frame)
    controls.pack(fill='x')

    def worker():
        messages.put(install_offline_webview2(installer))

    def start():
        button.configure(state='disabled')
        progress.start(15)
        info.set('Installing 1 of 1: Microsoft WebView2 Runtime...')
        threading.Thread(target=worker, name='webview2-offline-installer', daemon=True).start()

    def poll():
        try:
            ok, message = messages.get_nowait()
        except queue.Empty:
            root.after(200, poll)
            return
        progress.stop()
        info.set(message)
        if ok:
            result['ok'] = True
            button.configure(text='Continue to Dashboard', state='normal', command=root.destroy)
        else:
            button.configure(text='Retry Installation', state='normal', command=start)

    button = ttk.Button(controls, text='Install Required Components', command=start)
    button.pack(side='left')
    ttk.Button(controls, text='Cancel', command=root.destroy).pack(side='right')
    root.after(200, poll)
    root.mainloop()
    return result['ok']
