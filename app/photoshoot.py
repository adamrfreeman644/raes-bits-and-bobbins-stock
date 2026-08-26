import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, render_template, request, redirect, url_for, flash
from PIL import Image, ExifTags
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data'))
PHOTO_DIR = Path(os.environ.get('PHOTO_DIR', BASE_DIR / 'photos'))
DB_PATH = DATA_DIR / 'inventory.db'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'heic', 'heif'}

bp = Blueprint('photoshoot', __name__, url_prefix='/photo-shoot')


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_tables():
    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS photo_shoot_sessions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            started_at TEXT NOT NULL,
            ended_at TEXT,
            status TEXT NOT NULL DEFAULT 'Active'
        );
        CREATE TABLE IF NOT EXISTS photo_shoot_scans (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            product_id INTEGER NOT NULL,
            scanned_at TEXT NOT NULL,
            FOREIGN KEY(session_id) REFERENCES photo_shoot_sessions(id),
            FOREIGN KEY(product_id) REFERENCES products(id)
        );
        ''')


def exif_taken_at(file_storage):
    try:
        file_storage.stream.seek(0)
        with Image.open(file_storage.stream) as img:
            exif = img.getexif()
            if not exif:
                return None
            tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            raw = tags.get('DateTimeOriginal') or tags.get('DateTimeDigitized') or tags.get('DateTime')
            if not raw:
                return None
            try:
                return datetime.strptime(str(raw), '%Y:%m:%d %H:%M:%S')
            except ValueError:
                return None
    except Exception:
        return None


def save_photo(product_id, file_storage, sort_order):
    original = secure_filename(file_storage.filename or '')
    if '.' not in original:
        return None
    ext = original.rsplit('.', 1)[1].lower()
    if ext not in ALLOWED_EXTENSIONS:
        return None
    filename = f"p{product_id}_{uuid.uuid4().hex}.{ext}"
    file_storage.stream.seek(0)
    file_storage.save(PHOTO_DIR / filename)
    return filename


@bp.route('/')
def photo_shoot():
    init_tables()
    with db() as conn:
        session = conn.execute("SELECT * FROM photo_shoot_sessions WHERE status='Active' ORDER BY id DESC LIMIT 1").fetchone()
        scans = []
        current = None
        if session:
            scans = conn.execute('''SELECT s.*, p.inventory_id, p.item FROM photo_shoot_scans s
                                    JOIN products p ON p.id=s.product_id
                                    WHERE s.session_id=? ORDER BY s.scanned_at DESC''', (session['id'],)).fetchall()
            current = scans[0] if scans else None
        recent = conn.execute("SELECT * FROM photo_shoot_sessions ORDER BY id DESC LIMIT 8").fetchall()
    return render_template('photoshoot.html', session=session, scans=scans, current=current, recent=recent, results=None)


@bp.post('/start')
def start_session():
    init_tables()
    now = datetime.now().isoformat(timespec='seconds')
    with db() as conn:
        conn.execute("UPDATE photo_shoot_sessions SET status='Ended', ended_at=COALESCE(ended_at,?) WHERE status='Active'", (now,))
        conn.execute("INSERT INTO photo_shoot_sessions(started_at,status) VALUES(?, 'Active')", (now,))
    flash('Photo shoot started. Scan the first product.', 'success')
    return redirect(url_for('photoshoot.photo_shoot'))


@bp.post('/scan')
def scan_product():
    init_tables()
    code = request.form.get('barcode', '').strip().upper()
    if not code:
        return redirect(url_for('photoshoot.photo_shoot'))
    with db() as conn:
        session = conn.execute("SELECT * FROM photo_shoot_sessions WHERE status='Active' ORDER BY id DESC LIMIT 1").fetchone()
        if not session:
            flash('Start a photo shoot first.', 'error')
            return redirect(url_for('photoshoot.photo_shoot'))
        product = conn.execute('SELECT * FROM products WHERE UPPER(inventory_id)=? OR UPPER(barcode)=?', (code, code)).fetchone()
        if not product:
            flash(f'Barcode {code} was not found.', 'error')
            return redirect(url_for('photoshoot.photo_shoot'))
        conn.execute('INSERT INTO photo_shoot_scans(session_id,product_id,scanned_at) VALUES(?,?,?)',
                     (session['id'], product['id'], datetime.now().isoformat(timespec='seconds')))
    return redirect(url_for('photoshoot.photo_shoot'))


@bp.post('/end')
def end_session():
    init_tables()
    now = datetime.now().isoformat(timespec='seconds')
    with db() as conn:
        conn.execute("UPDATE photo_shoot_sessions SET status='Ended', ended_at=? WHERE status='Active'", (now,))
    flash('Photo shoot ended. You can now upload the full photo batch.', 'success')
    return redirect(url_for('photoshoot.photo_shoot'))


@bp.post('/upload/<int:session_id>')
def upload_batch(session_id):
    init_tables()
    files = request.files.getlist('photos')
    if not files:
        flash('Choose one or more photos.', 'error')
        return redirect(url_for('photoshoot.photo_shoot'))

    with db() as conn:
        session = conn.execute('SELECT * FROM photo_shoot_sessions WHERE id=?', (session_id,)).fetchone()
        scans = conn.execute('''SELECT s.*, p.inventory_id, p.item FROM photo_shoot_scans s
                                JOIN products p ON p.id=s.product_id
                                WHERE s.session_id=? ORDER BY s.scanned_at''', (session_id,)).fetchall()
        if not session or not scans:
            flash('That photo shoot has no scan markers.', 'error')
            return redirect(url_for('photoshoot.photo_shoot'))

        markers = [(datetime.fromisoformat(s['scanned_at']), s) for s in scans]
        session_end = datetime.fromisoformat(session['ended_at']) if session['ended_at'] else datetime.now()
        assigned = []
        unmatched = []

        for f in files:
            taken = exif_taken_at(f)
            if not taken:
                unmatched.append({'name': f.filename, 'reason': 'No readable EXIF Date Taken timestamp'})
                continue

            target = None
            for i, (start, scan) in enumerate(markers):
                end = markers[i + 1][0] if i + 1 < len(markers) else session_end
                if start <= taken < end:
                    target = scan
                    break
            if not target:
                unmatched.append({'name': f.filename, 'reason': f'Outside scan window ({taken})'})
                continue

            existing = conn.execute('SELECT COALESCE(MAX(sort_order),0) AS n FROM photos WHERE product_id=?', (target['product_id'],)).fetchone()['n']
            filename = save_photo(target['product_id'], f, existing + 1)
            if not filename:
                unmatched.append({'name': f.filename, 'reason': 'Unsupported image type'})
                continue
            conn.execute('INSERT INTO photos(product_id,filename,sort_order) VALUES(?,?,?)',
                         (target['product_id'], filename, existing + 1))
            assigned.append({'name': f.filename, 'taken': taken, 'inventory_id': target['inventory_id'], 'item': target['item']})

    with db() as conn:
        recent = conn.execute("SELECT * FROM photo_shoot_sessions ORDER BY id DESC LIMIT 8").fetchall()
    return render_template('photoshoot.html', session=None, scans=[], current=None, recent=recent,
                           results={'assigned': assigned, 'unmatched': unmatched, 'session_id': session_id})
