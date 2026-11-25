import logging
import shutil
from pathlib import Path
from urllib.parse import urlsplit


def import_committed(response):
    if response.status_code != 200 or response.headers.get('X-Import-Committed') != '1':
        return False
    try:
        body = response.json()
        return isinstance(body, dict) and not body.get('queued') and not body.get('error') and body.get('success') is not False
    except ValueError:
        return False


def cleanup_profile(driver, attribute):
    raw = getattr(driver, attribute, None)
    if not raw:
        return
    from serveone_storage import data_dir
    root = data_dir().resolve()
    profile = Path(raw)
    try:
        resolved = profile.resolve()
        if profile.is_symlink() or not resolved.is_relative_to(root) or profile.parent.name != 'chrome_profiles':
            return
        import psutil
        for process in psutil.process_iter(['name', 'cmdline']):
            try:
                if (process.info['name'] or '').lower() in ('chrome.exe', 'chrome'):
                    if any(str(resolved).lower() in arg.lower() for arg in process.info.get('cmdline') or []):
                        logging.warning('Profile retained: Chrome still active')
                        return
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        shutil.rmtree(resolved, ignore_errors=True)
        logging.info('Cleaned up automation chrome profile: %s', resolved.name)
    except FileNotFoundError:
        pass
    except OSError as exc:
        logging.warning('Profile cleanup deferred: %s', exc)


def cleanup_all_orphan_profiles():
    """Sweeps and deletes old or orphaned automation chrome profiles to keep disk clean."""
    import time, tempfile
    from serveone_storage import data_dir
    roots_to_check = [
        data_dir() / 'chrome_profiles',
        data_dir() / 'downloads' / 'chrome_profiles',
        Path(tempfile.gettempdir()) / 'serveone_chrome_serp',
    ]
    for root in roots_to_check:
        try:
            if not root.exists():
                continue
            for item in list(root.iterdir()):
                if not item.is_dir() or item.name.startswith('.'):
                    continue
                try:
                    mtime = item.stat().st_mtime
                    if time.time() - mtime > 60:
                        shutil.rmtree(item, ignore_errors=True)
                except Exception:
                    pass
        except Exception:
            pass


def continue_portal_warning(driver, requested_url):
    if urlsplit(requested_url).hostname != 'sportal.serveone.id':
        return False
    current = urlsplit(driver.current_url)
    if current.hostname not in ('sportal.serveone.id', 'chromewebdata'):
        return False
    from selenium.webdriver.common.by import By
    from selenium.common.exceptions import WebDriverException
    import time
    try:
        # Check title or page text for warning
        t = (driver.title or '').lower()
        if any(word in t for word in ('privacy', 'secure', 'connection', 'security', 'peringatan', 'keamanan', 'privasi')):
            for btn_id in ('proceed-button', 'proceed-link', 'primary-button', 'details-button'):
                for button in driver.find_elements(By.ID, btn_id):
                    if button.is_displayed():
                        button.click()
                        time.sleep(0.5)
                        if btn_id == 'details-button':
                            for pl in driver.find_elements(By.ID, 'proceed-link'):
                                if pl.is_displayed():
                                    pl.click()
                                    return True
                        return True
            for button in driver.find_elements(By.XPATH, "//button[contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'continue') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'proceed') or contains(translate(text(),'ABCDEFGHIJKLMNOPQRSTUVWXYZ','abcdefghijklmnopqrstuvwxyz'),'lanjutkan')]"):
                if button.is_displayed():
                    button.click()
                    return True
    except WebDriverException:
        pass
    # The HTTP upgrade warning may be Chrome UI, outside the page DOM.
    import os
    if os.name != 'nt':
        return False
    try:
        import psutil
        from pywinauto import Desktop
        service = driver.service.process.pid
        browsers = [child.pid for child in psutil.Process(service).children(recursive=True)
                    if child.name().lower() == 'chrome.exe']
        desktop = Desktop(backend='uia')
        for pid in browsers:
            for window in desktop.windows(process=pid, visible_only=True):
                labels = [item.window_text().lower() for item in window.descendants(control_type='Text')]
                if not any(k in text for text in labels for k in ('support a secure connection', 'secure connection', 'not secure', 'tidak aman', 'tidak mendukung sambungan aman', 'peringatan')):
                    continue
                for button in window.descendants(control_type='Button'):
                    btn_text = button.window_text().strip().lower()
                    if any(w in btn_text for w in ('continue to site', 'continue', 'proceed', 'lanjutkan ke situs', 'lanjutkan', 'tetap buka')) and button.is_enabled():
                        button.invoke()
                        return True
    except Exception as exc:
        logging.debug('Portal warning not available to UI automation: %s', type(exc).__name__)
    return False


def install_portal_navigation(driver):
    original_get = driver.get

    def navigate(url):
        original_get(url)
        if continue_portal_warning(driver, url):
            logging.warning('Continued to approved HTTP portal; connection is not encrypted')
    driver.get = navigate
