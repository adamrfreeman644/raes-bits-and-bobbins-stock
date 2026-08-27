import os
import sqlite3
from pathlib import Path

from flask import flash, redirect, render_template, request, session, url_for

from app import server
from app import photoshoot
from app import tenant
from app.features_v010 import configure as configure_features
from app.v010_extras import configure as configure_extras
from app.update_ui import configure as configure_update_ui


class DynamicPath:
    """Path-like object resolved from the active owner account on every use."""
    def __init__(self, resolver, fallback):
        self.resolver = resolver
        self.fallback = Path(fallback)

    def _path(self):
        try:
            return Path(self.resolver())
        except Exception:
            return self.fallback

    def __fspath__(self):
        return os.fspath(self._path())

    def __str__(self):
        return str(self._path())

    def __truediv__(self, other):
        return DynamicPath(lambda: self._path() / other, self.fallback / other)

    def mkdir(self, *args, **kwargs):
        return self._path().mkdir(*args, **kwargs)

    def exists(self):
        return self._path().exists()

    def iterdir(self):
        return self._path().iterdir()

    def read_text(self, *args, **kwargs):
        return self._path().read_text(*args, **kwargs)

    def write_text(self, *args, **kwargs):
        return self._path().write_text(*args, **kwargs)


version_file = Path(__file__).resolve().parent.parent / 'VERSION'
try:
    current_version = version_file.read_text().strip() or server.VERSION
except OSError:
    current_version = server.VERSION

server.VERSION = current_version
server.app.jinja_env.globals['app_version'] = current_version

# Make every existing feature transparently use the currently logged-in owner's
# isolated database and photo directory. The proxy is resolved per request, so
# different owners cannot share the same SQLite file even with multiple workers.
server.DB_PATH = DynamicPath(tenant.account_db_path, tenant.DATA_DIR / 'inventory.db')
server.PHOTO_DIR = DynamicPath(tenant.account_photo_dir, tenant.PHOTO_ROOT)
photoshoot.DB_PATH = server.DB_PATH
photoshoot.PHOTO_DIR = server.PHOTO_DIR


def tenant_db():
    conn = sqlite3.connect(tenant.account_db_path())
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


server.db = tenant_db
photoshoot.db = tenant_db

configure_features(server.app, server)
configure_extras(server.app, server)
configure_update_ui(server.app, server.UPDATER_DIR, current_version)

app = server.app


def initialise_owner_inventory():
    server.init_db()
    try:
        photoshoot.init_tables()
    except Exception:
        pass


@app.context_processor
def owner_context():
    account = tenant.current_account()
    return {
        'current_account': account,
        'business_name': account['business_name'] if account else 'Stock Manager',
    }


PUBLIC_ENDPOINTS = {
    'static', 'login', 'logout', 'setup', 'new_owner_account', 'health'
}


@app.before_request
def require_owner_login():
    endpoint = request.endpoint or ''
    if endpoint in PUBLIC_ENDPOINTS or endpoint.startswith('static'):
        return None

    if tenant.account_count() == 0:
        return redirect(url_for('setup'))

    account = tenant.current_account()
    if not account:
        return redirect(url_for('login', next=request.path))

    # Ensure this owner's private schema exists before any existing route opens it.
    initialise_owner_inventory()
    return None


@app.route('/setup', methods=['GET', 'POST'])
def setup():
    if tenant.account_count() > 0:
        return redirect(url_for('login'))

    if request.method == 'POST':
        try:
            account_id = tenant.create_account(
                request.form.get('email', ''),
                request.form.get('password', ''),
                request.form.get('business_name', ''),
                migrate_legacy=True,
            )
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_template('setup.html')

        session.clear()
        session['account_id'] = account_id
        initialise_owner_inventory()
        flash('Your owner account is ready. Existing stock has been kept with this account.', 'success')
        return redirect(url_for('index'))

    return render_template('setup.html')


@app.route('/login', methods=['GET', 'POST'])
def login():
    if tenant.account_count() == 0:
        return redirect(url_for('setup'))

    if request.method == 'POST':
        account = tenant.verify_login(request.form.get('email', ''), request.form.get('password', ''))
        if not account:
            flash('Email or password is incorrect.', 'error')
            return render_template('login.html')
        session.clear()
        session['account_id'] = account['id']
        initialise_owner_inventory()
        target = request.args.get('next', '')
        if not target.startswith('/'):
            target = url_for('index')
        return redirect(target)

    return render_template('login.html')


@app.post('/logout')
def logout():
    session.clear()
    return redirect(url_for('login'))


@app.route('/account/new', methods=['GET', 'POST'])
def new_owner_account():
    if request.method == 'POST':
        try:
            account_id = tenant.create_account(
                request.form.get('email', ''),
                request.form.get('password', ''),
                request.form.get('business_name', ''),
                migrate_legacy=False,
            )
        except ValueError as exc:
            flash(str(exc), 'error')
            return render_template('new_account.html')
        session.clear()
        session['account_id'] = account_id
        initialise_owner_inventory()
        flash('New owner account created.', 'success')
        return redirect(url_for('index'))
    return render_template('new_account.html')


@app.route('/account', methods=['GET', 'POST'])
def account_settings():
    account = tenant.current_account()
    if not account:
        return redirect(url_for('login'))

    if request.method == 'POST':
        action = request.form.get('action')
        try:
            if action == 'business_name':
                tenant.update_business_name(account['id'], request.form.get('business_name', ''))
                flash('Business name updated.', 'success')
            elif action == 'password':
                tenant.update_password(
                    account['id'],
                    request.form.get('current_password', ''),
                    request.form.get('new_password', ''),
                )
                flash('Password updated.', 'success')
        except ValueError as exc:
            flash(str(exc), 'error')
        return redirect(url_for('account_settings'))

    return render_template('account.html', account=account)
