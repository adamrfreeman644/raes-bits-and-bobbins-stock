import os
import secrets
import shutil
import sqlite3
from datetime import datetime
from pathlib import Path

from flask import session
from werkzeug.security import generate_password_hash, check_password_hash

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data'))
PHOTO_ROOT = Path(os.environ.get('PHOTO_DIR', BASE_DIR / 'photos'))
BACKUP_ROOT = Path(os.environ.get('BACKUP_DIR', BASE_DIR / 'backups'))
PLATFORM_DB = DATA_DIR / 'platform.db'
SECRET_FILE = DATA_DIR / '.session_secret'

for path in (DATA_DIR, PHOTO_ROOT, BACKUP_ROOT, DATA_DIR / 'accounts'):
    path.mkdir(parents=True, exist_ok=True)


def session_secret():
    try:
        value = SECRET_FILE.read_text().strip()
        if value:
            return value
    except OSError:
        pass
    value = secrets.token_hex(48)
    SECRET_FILE.write_text(value)
    try:
        SECRET_FILE.chmod(0o600)
    except OSError:
        pass
    return value


def platform_db():
    conn = sqlite3.connect(PLATFORM_DB)
    conn.row_factory = sqlite3.Row
    return conn


def _columns(conn):
    return {r['name'] for r in conn.execute('PRAGMA table_info(accounts)').fetchall()}


def init_platform():
    with platform_db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS accounts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            email TEXT NOT NULL UNIQUE COLLATE NOCASE,
            password_hash TEXT NOT NULL,
            business_name TEXT NOT NULL,
            created_at TEXT NOT NULL,
            last_login_at TEXT
        );
        ''')
        columns = _columns(conn)
        if 'auth_subject' not in columns:
            conn.execute('ALTER TABLE accounts ADD COLUMN auth_subject TEXT')
        if 'display_name' not in columns:
            conn.execute('ALTER TABLE accounts ADD COLUMN display_name TEXT')
        conn.execute('CREATE UNIQUE INDEX IF NOT EXISTS idx_accounts_auth_subject ON accounts(auth_subject) WHERE auth_subject IS NOT NULL')


def account_count():
    with platform_db() as conn:
        return int(conn.execute('SELECT COUNT(*) FROM accounts').fetchone()[0])


def get_account(account_id):
    if not account_id:
        return None
    with platform_db() as conn:
        return conn.execute('SELECT * FROM accounts WHERE id=?', (int(account_id),)).fetchone()


def get_account_by_email(email):
    with platform_db() as conn:
        return conn.execute('SELECT * FROM accounts WHERE email=? COLLATE NOCASE', ((email or '').strip(),)).fetchone()


def get_account_by_subject(subject):
    subject = (subject or '').strip()
    if not subject:
        return None
    with platform_db() as conn:
        return conn.execute('SELECT * FROM accounts WHERE auth_subject=?', (subject,)).fetchone()


def current_account():
    return get_account(session.get('account_id'))


def account_data_dir(account_id=None):
    account_id = int(account_id or session.get('account_id') or 0)
    if not account_id:
        raise RuntimeError('No owner account selected')
    path = DATA_DIR / 'accounts' / str(account_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def account_db_path(account_id=None):
    return account_data_dir(account_id) / 'inventory.db'


def account_photo_dir(account_id=None):
    account_id = int(account_id or session.get('account_id') or 0)
    if not account_id:
        raise RuntimeError('No owner account selected')
    path = PHOTO_ROOT / 'accounts' / str(account_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def account_backup_dir(account_id=None):
    account_id = int(account_id or session.get('account_id') or 0)
    if not account_id:
        raise RuntimeError('No owner account selected')
    path = BACKUP_ROOT / 'accounts' / str(account_id)
    path.mkdir(parents=True, exist_ok=True)
    return path


def _copy_legacy_data(account_id, migrate_legacy=False):
    data_dir = account_data_dir(account_id)
    photo_dir = account_photo_dir(account_id)
    account_backup_dir(account_id)
    legacy_db = DATA_DIR / 'inventory.db'
    if migrate_legacy and legacy_db.exists() and not (data_dir / 'inventory.db').exists():
        shutil.copy2(legacy_db, data_dir / 'inventory.db')
    if migrate_legacy and PHOTO_ROOT.exists():
        for source in PHOTO_ROOT.iterdir():
            if source.name == 'accounts' or not source.is_file():
                continue
            target = photo_dir / source.name
            if not target.exists():
                try:
                    shutil.copy2(source, target)
                except OSError:
                    pass


def create_account(email, password, business_name, migrate_legacy=False):
    """Legacy account creator retained only for rollback/migration compatibility."""
    email = email.strip().lower()
    business_name = business_name.strip()
    if not email or not password or not business_name:
        raise ValueError('Business name, email and password are required.')
    if len(password) < 8:
        raise ValueError('Password must be at least 8 characters.')
    with platform_db() as conn:
        try:
            cur = conn.execute(
                'INSERT INTO accounts(email,password_hash,business_name,created_at) VALUES(?,?,?,?)',
                (email, generate_password_hash(password), business_name, datetime.now().isoformat(timespec='seconds'))
            )
        except sqlite3.IntegrityError as exc:
            raise ValueError('An account with that email already exists.') from exc
        account_id = cur.lastrowid
    _copy_legacy_data(account_id, migrate_legacy)
    return account_id


def create_oidc_account(auth_subject, email, display_name, business_name=None, migrate_legacy=False):
    auth_subject = (auth_subject or '').strip()
    email = (email or '').strip().lower()
    display_name = (display_name or email or 'Owner').strip()
    business_name = (business_name or display_name or 'Stock Manager').strip()
    if not auth_subject:
        raise ValueError('OIDC subject is required.')
    if not email:
        email = f"oidc-{auth_subject[:12]}@local.invalid"
    with platform_db() as conn:
        cur = conn.execute(
            '''INSERT INTO accounts(email,password_hash,business_name,created_at,last_login_at,auth_subject,display_name)
               VALUES(?,?,?,?,?,?,?)''',
            (email, '!oidc-only', business_name, datetime.now().isoformat(timespec='seconds'),
             datetime.now().isoformat(timespec='seconds'), auth_subject, display_name)
        )
        account_id = cur.lastrowid
    _copy_legacy_data(account_id, migrate_legacy)
    return get_account(account_id)


def link_oidc_identity(auth_subject, email, display_name, auto_provision=True):
    """Resolve an Authentik subject to exactly one local tenant.

    Existing installations are linked once by matching email, after which the
    immutable OIDC `sub` is authoritative. New subjects can be provisioned into
    their own isolated tenant; no password is created or stored.
    """
    auth_subject = (auth_subject or '').strip()
    email = (email or '').strip().lower()
    display_name = (display_name or email or 'Owner').strip()
    if not auth_subject:
        return None
    existing = get_account_by_subject(auth_subject)
    if existing:
        with platform_db() as conn:
            conn.execute('UPDATE accounts SET email=?,display_name=?,last_login_at=? WHERE id=?',
                         (email or existing['email'], display_name, datetime.now().isoformat(timespec='seconds'), existing['id']))
        return get_account(existing['id'])

    legacy = get_account_by_email(email) if email else None
    if legacy and not legacy['auth_subject']:
        with platform_db() as conn:
            conn.execute('UPDATE accounts SET auth_subject=?,display_name=?,last_login_at=? WHERE id=?',
                         (auth_subject, display_name, datetime.now().isoformat(timespec='seconds'), legacy['id']))
        return get_account(legacy['id'])

    if not auto_provision:
        return None
    return create_oidc_account(auth_subject, email, display_name, migrate_legacy=(account_count() == 0))


def verify_login(email, password):
    """Legacy verifier retained for rollback only; OIDC routes do not call it."""
    account = get_account_by_email(email)
    if not account or account['password_hash'] == '!oidc-only' or not check_password_hash(account['password_hash'], password):
        return None
    with platform_db() as conn:
        conn.execute('UPDATE accounts SET last_login_at=? WHERE id=?',
                     (datetime.now().isoformat(timespec='seconds'), account['id']))
    return account


def update_business_name(account_id, business_name):
    business_name = business_name.strip()
    if not business_name:
        raise ValueError('Business name cannot be empty.')
    with platform_db() as conn:
        conn.execute('UPDATE accounts SET business_name=? WHERE id=?', (business_name, int(account_id)))


def update_password(account_id, current_password, new_password):
    """Legacy rollback helper; active password management is delegated to Authentik."""
    account = get_account(account_id)
    if not account or account['password_hash'] == '!oidc-only' or not check_password_hash(account['password_hash'], current_password):
        raise ValueError('Current password is incorrect.')
    if len(new_password) < 8:
        raise ValueError('New password must be at least 8 characters.')
    with platform_db() as conn:
        conn.execute('UPDATE accounts SET password_hash=? WHERE id=?',
                     (generate_password_hash(new_password), int(account_id)))


init_platform()
