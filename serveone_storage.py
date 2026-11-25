from pathlib import Path
import os


def data_dir():
    override = os.environ.get('SERVEONE_DATA_DIR')
    if override:
        return Path(override).expanduser().resolve()
    if os.name == 'nt':
        root = Path('D:/ServeoneDashboard/data')
        if not Path('D:/').is_dir():
            raise OSError('Drive D is required for Serveone data; no fallback to C.')
        return root
    return Path.home() / '.serveone-dashboard-desktop'
