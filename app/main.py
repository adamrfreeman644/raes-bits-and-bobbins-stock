from pathlib import Path
from app import server
from app.update_ui import configure as configure_update_ui

version_file = Path(__file__).resolve().parent.parent / 'VERSION'
try:
    current_version = version_file.read_text().strip() or server.VERSION
except OSError:
    current_version = server.VERSION

server.VERSION = current_version
server.app.jinja_env.globals['app_version'] = current_version
configure_update_ui(server.app, server.UPDATER_DIR, current_version)

app = server.app
