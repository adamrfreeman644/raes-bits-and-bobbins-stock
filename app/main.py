from app import server
from app.photoshoot import bp as photoshoot_bp, init_tables

server.VERSION = '0.0.6'
server.app.register_blueprint(photoshoot_bp)
init_tables()

app = server.app
