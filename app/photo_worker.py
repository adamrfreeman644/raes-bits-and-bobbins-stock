import json
import os
import shutil
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path

from PIL import Image, ExifTags

try:
    from pillow_heif import register_heif_opener
    register_heif_opener()
except ImportError:
    pass

from app import tenant

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'heic', 'heif'}
POLL_SECONDS = max(1, int(os.getenv('PHOTO_WORKER_POLL_SECONDS', '2')))


def connect(path):
    conn = sqlite3.connect(path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA foreign_keys=ON')
    return conn


def parse_exif_datetime(raw):
    if not raw:
        return None
    for fmt in ('%Y:%m:%d %H:%M:%S', '%Y-%m-%d %H:%M:%S'):
        try:
            return datetime.strptime(str(raw), fmt)
        except ValueError:
            pass
    return None


def exif_taken_at(path):
    try:
        with Image.open(path) as img:
            exif = img.getexif()
            if not exif:
                return None
            try:
                exif_ifd = exif.get_ifd(ExifTags.IFD.Exif)
            except Exception:
                exif_ifd = {}
            for key in (36867, 36868):
                taken = parse_exif_datetime(exif_ifd.get(key))
                if taken:
                    return taken
            tags = {ExifTags.TAGS.get(k, k): v for k, v in exif.items()}
            return parse_exif_datetime(tags.get('DateTimeOriginal') or tags.get('DateTimeDigitized') or tags.get('DateTime'))
    except Exception:
        return None


def account_ids():
    with tenant.platform_db() as conn:
        return [int(r['id']) for r in conn.execute('SELECT id FROM accounts ORDER BY id').fetchall()]


def recover_processing_jobs(conn):
    conn.execute("UPDATE photo_upload_jobs SET status='Queued',started_at=NULL WHERE status='Processing'")


def next_job(account_id):
    db_path = tenant.account_db_path(account_id)
    if not db_path.exists():
        return None
    try:
        with connect(db_path) as conn:
            recover_processing_jobs(conn)
            row = conn.execute("SELECT id FROM photo_upload_jobs WHERE status='Queued' ORDER BY id LIMIT 1").fetchone()
            return int(row['id']) if row else None
    except sqlite3.Error:
        return None


def result_json(**kwargs):
    return json.dumps(kwargs, separators=(',', ':'))


def process_job(account_id, job_id):
    db_path = tenant.account_db_path(account_id)
    photo_dir = tenant.account_photo_dir(account_id)
    staging_dir = photo_dir / '.photo-shoot-staging' / f'job-{job_id}'

    with connect(db_path) as conn:
        job = conn.execute('SELECT * FROM photo_upload_jobs WHERE id=?', (job_id,)).fetchone()
        if not job or job['status'] not in ('Queued', 'Processing'):
            return
        conn.execute("UPDATE photo_upload_jobs SET status='Processing',started_at=?,error=NULL WHERE id=?",
                     (datetime.now().isoformat(timespec='seconds'), job_id))
        shoot = conn.execute('SELECT * FROM photo_shoot_sessions WHERE id=?', (job['session_id'],)).fetchone()
        scans = conn.execute('''SELECT s.*,p.item,
                                (SELECT ib.barcode FROM item_barcodes ib WHERE ib.product_id=p.id ORDER BY ib.id LIMIT 1) AS inventory_id
                                FROM photo_shoot_scans s JOIN products p ON p.id=s.product_id
                                WHERE s.session_id=? ORDER BY s.scanned_at''', (job['session_id'],)).fetchall()
        items = conn.execute("SELECT * FROM photo_upload_items WHERE job_id=? AND status='Staged' ORDER BY order_index,id", (job_id,)).fetchall()

    if not shoot or not scans:
        with connect(db_path) as conn:
            conn.execute("UPDATE photo_upload_jobs SET status='Failed',error=?,finished_at=? WHERE id=?",
                         ('Shoot or scan markers no longer exist.', datetime.now().isoformat(timespec='seconds'), job_id))
        return

    markers = [(datetime.fromisoformat(s['scanned_at']), s) for s in scans]
    session_end = datetime.fromisoformat(shoot['ended_at']) if shoot['ended_at'] else datetime.now()
    server_offset = datetime.now().astimezone().utcoffset() or timedelta(0)
    server_offset_minutes = int(server_offset.total_seconds() // 60)
    device_offset = int(job['device_tz_offset'] or 0)

    for item in items:
        staged = staging_dir / item['staged_name']
        if not staged.exists():
            with connect(db_path) as conn:
                conn.execute("UPDATE photo_upload_items SET status='Unmatched',result_json=? WHERE id=?",
                             (result_json(reason='Staged file is missing'), item['id']))
                conn.execute("UPDATE photo_upload_jobs SET processed_files=processed_files+1,unmatched_count=unmatched_count+1 WHERE id=?", (job_id,))
            continue

        taken = exif_taken_at(staged)
        source = 'camera Date Taken'
        if taken:
            taken = taken + timedelta(minutes=device_offset + server_offset_minutes)
        elif item['modified_ms']:
            try:
                taken = datetime.fromtimestamp(float(item['modified_ms']) / 1000.0)
                source = 'file timestamp fallback'
            except (ValueError, OSError, OverflowError):
                taken = None

        target = None
        if taken:
            for i, (start, scan) in enumerate(markers):
                end = markers[i + 1][0] if i + 1 < len(markers) else session_end
                if start <= taken < end:
                    target = scan
                    break

        if not taken or not target:
            reason = 'No readable camera or file timestamp' if not taken else f'Outside scan window ({taken.strftime("%Y-%m-%d %H:%M:%S")})'
            with connect(db_path) as conn:
                conn.execute("UPDATE photo_upload_items SET status='Unmatched',result_json=? WHERE id=?",
                             (result_json(reason=reason, timestamp_source=source), item['id']))
                conn.execute("UPDATE photo_upload_jobs SET processed_files=processed_files+1,unmatched_count=unmatched_count+1 WHERE id=?", (job_id,))
            continue

        ext = staged.suffix.lower().lstrip('.')
        if ext not in ALLOWED_EXTENSIONS:
            with connect(db_path) as conn:
                conn.execute("UPDATE photo_upload_items SET status='Unmatched',result_json=? WHERE id=?",
                             (result_json(reason='Unsupported image type'), item['id']))
                conn.execute("UPDATE photo_upload_jobs SET processed_files=processed_files+1,unmatched_count=unmatched_count+1 WHERE id=?", (job_id,))
            continue

        filename = f"p{target['product_id']}_shoot{job_id}_{item['id']}.{ext}"
        final_path = photo_dir / filename
        if not final_path.exists():
            shutil.copy2(staged, final_path)

        with connect(db_path) as conn:
            exists = conn.execute('SELECT 1 FROM photos WHERE filename=? LIMIT 1', (filename,)).fetchone()
            if not exists:
                order = conn.execute('SELECT COALESCE(MAX(sort_order),0)+1 AS n FROM photos WHERE product_id=?', (target['product_id'],)).fetchone()['n']
                conn.execute('INSERT INTO photos(product_id,filename,sort_order) VALUES(?,?,?)', (target['product_id'], filename, order))
            conn.execute("UPDATE photo_upload_items SET status='Assigned',result_json=? WHERE id=?",
                         (result_json(inventory_id=target['inventory_id'], item=target['item'], taken=taken.isoformat(timespec='seconds'), timestamp_source=source), item['id']))
            conn.execute("UPDATE photo_upload_jobs SET processed_files=processed_files+1,assigned_count=assigned_count+1 WHERE id=?", (job_id,))
        try:
            staged.unlink()
        except OSError:
            pass

    with connect(db_path) as conn:
        remaining = conn.execute("SELECT COUNT(*) AS n FROM photo_upload_items WHERE job_id=? AND status='Staged'", (job_id,)).fetchone()['n']
        if remaining == 0:
            conn.execute("UPDATE photo_upload_jobs SET status='Complete',finished_at=? WHERE id=?",
                         (datetime.now().isoformat(timespec='seconds'), job_id))


def run_forever():
    print('StockTake Photo Worker started', flush=True)
    while True:
        worked = False
        for account_id in account_ids():
            job_id = next_job(account_id)
            if not job_id:
                continue
            worked = True
            try:
                process_job(account_id, job_id)
            except Exception as exc:
                try:
                    with connect(tenant.account_db_path(account_id)) as conn:
                        conn.execute("UPDATE photo_upload_jobs SET status='Failed',error=?,finished_at=? WHERE id=?",
                                     (str(exc)[:1000], datetime.now().isoformat(timespec='seconds'), job_id))
                except Exception:
                    pass
                print(f'Photo job {job_id} failed: {exc}', flush=True)
        if not worked:
            time.sleep(POLL_SECONDS)


if __name__ == '__main__':
    run_forever()
