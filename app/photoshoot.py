import json
import os
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

from flask import Blueprint, jsonify, render_template, request, redirect, url_for, flash
from PIL import Image, ExifTags
from werkzeug.utils import secure_filename

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data'))
PHOTO_DIR = Path(os.environ.get('PHOTO_DIR', BASE_DIR / 'photos'))
DB_PATH = DATA_DIR / 'inventory.db'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'heic', 'heif'}

bp = Blueprint('photoshoot', __name__, url_prefix='/photo-shoot')


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
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
        CREATE TABLE IF NOT EXISTS photo_upload_jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id INTEGER NOT NULL,
            status TEXT NOT NULL DEFAULT 'Uploading',
            device_tz_offset INTEGER NOT NULL DEFAULT 0,
            created_at TEXT NOT NULL,
            queued_at TEXT,
            started_at TEXT,
            finished_at TEXT,
            total_files INTEGER NOT NULL DEFAULT 0,
            uploaded_files INTEGER NOT NULL DEFAULT 0,
            processed_files INTEGER NOT NULL DEFAULT 0,
            assigned_count INTEGER NOT NULL DEFAULT 0,
            unmatched_count INTEGER NOT NULL DEFAULT 0,
            error TEXT,
            FOREIGN KEY(session_id) REFERENCES photo_shoot_sessions(id)
        );
        CREATE TABLE IF NOT EXISTS photo_upload_items (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            job_id INTEGER NOT NULL,
            order_index INTEGER NOT NULL,
            original_name TEXT NOT NULL,
            staged_name TEXT NOT NULL,
            modified_ms REAL,
            size_bytes INTEGER NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'Staged',
            result_json TEXT,
            FOREIGN KEY(job_id) REFERENCES photo_upload_jobs(id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_photo_jobs_status ON photo_upload_jobs(status,id);
        CREATE INDEX IF NOT EXISTS idx_photo_items_job ON photo_upload_items(job_id,order_index,id);
        ''')


def _staging_dir(job_id):
    path = Path(PHOTO_DIR) / '.photo-shoot-staging' / f'job-{int(job_id)}'
    path.mkdir(parents=True, exist_ok=True)
    return path


def _job_payload(conn, job_id, include_items=False):
    job = conn.execute('SELECT * FROM photo_upload_jobs WHERE id=?', (job_id,)).fetchone()
    if not job:
        return None
    payload = {k: job[k] for k in job.keys()}
    if include_items:
        rows = conn.execute('SELECT * FROM photo_upload_items WHERE job_id=? ORDER BY order_index,id', (job_id,)).fetchall()
        items = []
        for row in rows:
            item = {k: row[k] for k in row.keys()}
            try:
                item['result'] = json.loads(item.pop('result_json') or '{}')
            except (TypeError, ValueError, json.JSONDecodeError):
                item['result'] = {}
            items.append(item)
        payload['items'] = items
    return payload


@bp.route('/')
def photo_shoot():
    init_tables()
    with db() as conn:
        session = conn.execute("SELECT * FROM photo_shoot_sessions WHERE status='Active' ORDER BY id DESC LIMIT 1").fetchone()
        scans = []
        current = None
        if session:
            scans = conn.execute('''SELECT s.*, p.item,
                                    (SELECT ib.barcode FROM item_barcodes ib WHERE ib.product_id=p.id ORDER BY ib.id LIMIT 1) AS inventory_id
                                    FROM photo_shoot_scans s JOIN products p ON p.id=s.product_id
                                    WHERE s.session_id=? ORDER BY s.scanned_at DESC''', (session['id'],)).fetchall()
            current = scans[0] if scans else None
        recent = conn.execute("SELECT * FROM photo_shoot_sessions ORDER BY id DESC LIMIT 8").fetchall()
        jobs = {}
        for shoot in recent:
            job = conn.execute('SELECT * FROM photo_upload_jobs WHERE session_id=? ORDER BY id DESC LIMIT 1', (shoot['id'],)).fetchone()
            if job:
                jobs[shoot['id']] = {k: job[k] for k in job.keys()}
    return render_template('photoshoot.html', session=session, scans=scans, current=current, recent=recent, jobs=jobs)


@bp.post('/start')
def start_session():
    init_tables()
    now = datetime.now().isoformat(timespec='seconds')
    with db() as conn:
        conn.execute("UPDATE photo_shoot_sessions SET status='Ended', ended_at=COALESCE(ended_at,?) WHERE status='Active'", (now,))
        conn.execute("INSERT INTO photo_shoot_sessions(started_at,status) VALUES(?, 'Active')", (now,))
    flash('Photo shoot started. Scan the first physical item barcode.', 'success')
    return redirect(url_for('photoshoot.photo_shoot'))


@bp.post('/scan')
def scan_product():
    init_tables()
    code = request.form.get('barcode', '').strip()
    if not code:
        return redirect(url_for('photoshoot.photo_shoot'))
    with db() as conn:
        session = conn.execute("SELECT * FROM photo_shoot_sessions WHERE status='Active' ORDER BY id DESC LIMIT 1").fetchone()
        if not session:
            flash('Start a photo shoot first.', 'error')
            return redirect(url_for('photoshoot.photo_shoot'))
        product = conn.execute('''SELECT p.* FROM item_barcodes ib JOIN products p ON p.id=ib.product_id
                                  WHERE ib.barcode=? LIMIT 1''', (code,)).fetchone()
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
    flash('Photo shoot ended. Select the batch and StockTake will stage it safely before assigning in the background.', 'success')
    return redirect(url_for('photoshoot.photo_shoot'))


@bp.post('/upload/start/<int:session_id>')
def start_batch_upload(session_id):
    init_tables()
    try:
        offset = int(request.form.get('device_tz_offset', '0'))
    except ValueError:
        offset = 0
    with db() as conn:
        shoot = conn.execute('SELECT * FROM photo_shoot_sessions WHERE id=?', (session_id,)).fetchone()
        scans = conn.execute('SELECT COUNT(*) AS n FROM photo_shoot_scans WHERE session_id=?', (session_id,)).fetchone()['n']
        if not shoot or not scans:
            return jsonify({'ok': False, 'error': 'That shoot has no scan markers.'}), 400
        cur = conn.execute('''INSERT INTO photo_upload_jobs(session_id,status,device_tz_offset,created_at)
                              VALUES(?, 'Uploading', ?, ?)''',
                           (session_id, offset, datetime.now().isoformat(timespec='seconds')))
        job_id = cur.lastrowid
    _staging_dir(job_id)
    return jsonify({'ok': True, 'job_id': job_id})


@bp.post('/upload/file/<int:job_id>')
def stage_batch_file(job_id):
    init_tables()
    uploaded = request.files.get('photo')
    if not uploaded or not uploaded.filename:
        return jsonify({'ok': False, 'error': 'No photo was supplied.'}), 400
    original = secure_filename(uploaded.filename) or f'photo-{uuid.uuid4().hex}'
    if '.' not in original or original.rsplit('.', 1)[1].lower() not in ALLOWED_EXTENSIONS:
        return jsonify({'ok': False, 'error': f'{uploaded.filename}: unsupported image type'}), 400
    try:
        order_index = int(request.form.get('order_index', '0'))
    except ValueError:
        order_index = 0
    try:
        modified_ms = float(request.form.get('modified_ms', '0') or 0)
    except ValueError:
        modified_ms = 0

    with db() as conn:
        job = conn.execute('SELECT * FROM photo_upload_jobs WHERE id=?', (job_id,)).fetchone()
        if not job or job['status'] != 'Uploading':
            return jsonify({'ok': False, 'error': 'This upload job is no longer accepting files.'}), 409

    suffix = Path(original).suffix.lower()
    staged_name = f'{order_index:06d}-{uuid.uuid4().hex}{suffix}'
    destination = _staging_dir(job_id) / staged_name
    uploaded.save(destination)
    size_bytes = destination.stat().st_size

    with db() as conn:
        conn.execute('''INSERT INTO photo_upload_items(job_id,order_index,original_name,staged_name,modified_ms,size_bytes,status)
                        VALUES(?,?,?,?,?,?,'Staged')''',
                     (job_id, order_index, original, staged_name, modified_ms, size_bytes))
        conn.execute('UPDATE photo_upload_jobs SET uploaded_files=uploaded_files+1 WHERE id=?', (job_id,))
        count = conn.execute('SELECT uploaded_files FROM photo_upload_jobs WHERE id=?', (job_id,)).fetchone()['uploaded_files']
    return jsonify({'ok': True, 'uploaded_files': count, 'size_bytes': size_bytes})


@bp.post('/upload/finish/<int:job_id>')
def finish_batch_upload(job_id):
    init_tables()
    with db() as conn:
        job = conn.execute('SELECT * FROM photo_upload_jobs WHERE id=?', (job_id,)).fetchone()
        if not job or job['status'] != 'Uploading':
            return jsonify({'ok': False, 'error': 'Upload job not found or already queued.'}), 409
        total = conn.execute('SELECT COUNT(*) AS n FROM photo_upload_items WHERE job_id=?', (job_id,)).fetchone()['n']
        if not total:
            return jsonify({'ok': False, 'error': 'No photos were uploaded.'}), 400
        conn.execute('''UPDATE photo_upload_jobs SET status='Queued',total_files=?,uploaded_files=?,queued_at=? WHERE id=?''',
                     (total, total, datetime.now().isoformat(timespec='seconds'), job_id))
    return jsonify({'ok': True, 'job_id': job_id, 'status': 'Queued', 'total_files': total})


@bp.get('/upload/status/<int:job_id>')
def batch_upload_status(job_id):
    init_tables()
    with db() as conn:
        payload = _job_payload(conn, job_id, include_items=True)
    if not payload:
        return jsonify({'ok': False, 'error': 'Upload job not found.'}), 404
    payload['ok'] = True
    return jsonify(payload)
