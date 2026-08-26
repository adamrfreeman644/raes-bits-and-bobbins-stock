from pathlib import Path
from app import server

version_file = Path(__file__).resolve().parent.parent / 'VERSION'
try:
    server.VERSION = version_file.read_text().strip() or server.VERSION
except OSError:
    pass

app = server.app
