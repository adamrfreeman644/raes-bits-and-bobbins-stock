import csv
import io
import os
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import Response, abort, flash, redirect, render_template, request, send_from_directory, session, url_for

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

    def glob(self, pattern):
        return self._path().glob(pattern)

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

# Every existing inventory feature now resolves storage from the logged-in owner.
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
    if not tenant.current_account():
        return redirect(url_for('login', next=request.path))
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


# --- Tenant-aware overrides for older shared-storage feature routes ---
def owner_backup_path(name):
    return tenant.account_backup_dir() / Path(name).name


def make_owner_backup(prefix='manual'):
    source = tenant.account_db_path()
    destination = tenant.account_backup_dir() / f"{prefix}-{datetime.now().strftime('%Y%m%d-%H%M%S')}.db"
    if source.exists():
        shutil.copy2(source, destination)
    return destination


def owner_backups():
    directory = tenant.account_backup_dir()
    files = sorted(directory.glob('*.db'), key=lambda p: p.stat().st_mtime, reverse=True)
    return render_template('backups.html', backups=[
        {'name': p.name, 'size': p.stat().st_size, 'mtime': datetime.fromtimestamp(p.stat().st_mtime)}
        for p in files
    ])


def owner_backup_create():
    make_owner_backup('manual')
    flash('Backup created.', 'success')
    return redirect(url_for('backups'))


def owner_backup_download(name):
    path = owner_backup_path(name)
    if not path.exists():
        abort(404)
    return send_from_directory(tenant.account_backup_dir(), path.name, as_attachment=True)


def owner_backup_restore(name):
    source_path = owner_backup_path(name)
    if not source_path.exists():
        abort(404)
    make_owner_backup('before-restore')
    source = sqlite3.connect(source_path)
    destination = sqlite3.connect(tenant.account_db_path())
    try:
        source.backup(destination)
    finally:
        destination.close()
        source.close()
    flash('Backup restored. A safety copy of the previous database was kept.', 'success')
    return redirect(url_for('dashboard'))


def owner_backup_delete(name):
    owner_backup_path(name).unlink(missing_ok=True)
    return redirect(url_for('backups'))


app.view_functions['backups'] = owner_backups
app.view_functions['backup_create'] = owner_backup_create
app.view_functions['backup_download'] = owner_backup_download
app.view_functions['backup_restore'] = owner_backup_restore
app.view_functions['backup_delete'] = owner_backup_delete


def generic_paypal_export():
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Name', 'Price', 'Barcode', 'SKU', 'In Stock'])
    with tenant_db() as conn:
        rows = conn.execute('''SELECT p.item,p.price_pence,ib.barcode,ib.state
                               FROM item_barcodes ib JOIN products p ON p.id=ib.product_id
                               ORDER BY p.id,ib.id''').fetchall()
    for row in rows:
        writer.writerow([row['item'], f"{row['price_pence'] / 100:.2f}", row['barcode'], row['barcode'], 1 if row['state'] == 'Available' else 0])
    filename = f"stock-paypal-{datetime.now().strftime('%Y%m%d-%H%M')}.csv"
    return Response(output.getvalue(), mimetype='text/csv', headers={'Content-Disposition': f'attachment; filename={filename}'})


app.view_functions['paypal_export'] = generic_paypal_export
