import os
import sqlite3
import uuid
import urllib.request
from datetime import datetime
from pathlib import Path
from flask import Flask, render_template, request, redirect, url_for, flash, send_from_directory, abort
from werkzeug.utils import secure_filename

BASE_DIR = Path(__file__).resolve().parent.parent
DATA_DIR = Path(os.environ.get('DATA_DIR', BASE_DIR / 'data'))
PHOTO_DIR = Path(os.environ.get('PHOTO_DIR', BASE_DIR / 'photos'))
UPDATER_DIR = Path(os.environ.get('UPDATER_DIR', BASE_DIR / 'updater-state'))
DB_PATH = DATA_DIR / 'inventory.db'
VERSION = '0.0.4'
LATEST_URL = 'https://raw.githubusercontent.com/adamrfreeman644/raes-bits-and-bobbins-stock/main/VERSION'
ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'heic', 'heif'}
MAX_PHOTOS = 5

for p in (DATA_DIR, PHOTO_DIR, UPDATER_DIR):
    p.mkdir(parents=True, exist_ok=True)

app = Flask(__name__)
app.secret_key = os.environ.get('SECRET_KEY', 'change-this-before-production')
app.config['MAX_CONTENT_LENGTH'] = 40 * 1024 * 1024


def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with db() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS products (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inventory_id TEXT UNIQUE,
            item TEXT NOT NULL,
            main_colour TEXT,
            secondary_colour TEXT,
            pattern TEXT,
            price_pence INTEGER NOT NULL DEFAULT 0,
            barcode TEXT UNIQUE,
            quantity INTEGER NOT NULL DEFAULT 1,
            status TEXT NOT NULL DEFAULT 'Available' CHECK(status IN ('Available','Sold')),
            date_added TEXT NOT NULL,
            date_sold TEXT,
            notes TEXT
        );
        CREATE TABLE IF NOT EXISTS photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            product_id INTEGER NOT NULL,
            filename TEXT NOT NULL,
            sort_order INTEGER NOT NULL DEFAULT 1,
            FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE
        );
        ''')
        columns = {r['name'] for r in conn.execute('PRAGMA table_info(products)').fetchall()}
        if 'quantity' not in columns:
            conn.execute('ALTER TABLE products ADD COLUMN quantity INTEGER NOT NULL DEFAULT 1')
        # v0.0.4: there is only one user-facing identifier. The inventory ID is the barcode.
        # Clear legacy barcode values first to avoid UNIQUE collisions during migration.
        conn.execute('UPDATE products SET barcode=NULL')
        conn.execute('UPDATE products SET barcode=inventory_id WHERE inventory_id IS NOT NULL')


def next_inventory_id(conn):
    row = conn.execute('SELECT COALESCE(MAX(id),0)+1 AS n FROM products').fetchone()
    return f"INV-{row['n']:04d}"


def allowed_file(filename):
    return '.' in filename and filename.rsplit('.',1)[1].lower() in ALLOWED_EXTENSIONS


def save_uploaded_photos(product_id, files, start_order=1):
    saved, order = [], start_order
    for file in files:
        if not file or not file.filename or not allowed_file(file.filename):
            continue
        ext = secure_filename(file.filename).rsplit('.',1)[1].lower()
        filename = f"p{product_id}_{uuid.uuid4().hex}.{ext}"
        file.save(PHOTO_DIR / filename)
        saved.append((filename, order))
        order += 1
    return saved


def product_with_photos(conn, product_id):
    product = conn.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
    if not product:
        return None, []
    photos = conn.execute('SELECT * FROM photos WHERE product_id=? ORDER BY sort_order,id',(product_id,)).fetchall()
    return product, photos


def latest_version():
    try:
        req = urllib.request.Request(LATEST_URL, headers={'User-Agent':'raes-stock-updater'})
        with urllib.request.urlopen(req, timeout=5) as response:
            return response.read().decode('utf-8').strip()
    except Exception:
        return None


def version_tuple(value):
    try:
        return tuple(int(x) for x in value.strip().lstrip('v').split('.'))
    except Exception:
        return (0,)


def tail(path, limit=80):
    try:
        return '\n'.join(path.read_text(errors='replace').splitlines()[-limit:])
    except OSError:
        return ''


@app.template_filter('money')
def money(pence):
    return f"£{(pence or 0)/100:.2f}"


@app.route('/')
def index():
    view = request.args.get('view','cards')
    q = request.args.get('q','').strip()
    status = request.args.get('status','').strip()
    sql = '''SELECT p.*, ph.filename AS main_photo FROM products p LEFT JOIN photos ph ON ph.id=(SELECT id FROM photos WHERE product_id=p.id ORDER BY sort_order,id LIMIT 1) WHERE 1=1'''
    params = []
    if q:
        like = f'%{q}%'
        sql += ' AND (p.item LIKE ? OR p.main_colour LIKE ? OR p.secondary_colour LIKE ? OR p.pattern LIKE ? OR p.inventory_id LIKE ?)'
        params.extend([like]*5)
    if status in ('Available','Sold'):
        sql += ' AND p.status=?'
        params.append(status)
    sql += ' ORDER BY p.id DESC'
    with db() as conn:
        products = conn.execute(sql,params).fetchall()
        counts = {r['status']:r['c'] for r in conn.execute('SELECT status,COUNT(*) c FROM products GROUP BY status').fetchall()}
    return render_template('index.html',products=products,view=view,q=q,status=status,available=counts.get('Available',0),sold=counts.get('Sold',0))


@app.route('/product/new',methods=['GET','POST'])
def new_product():
    if request.method == 'GET':
        return render_template('form.html',product=None,photos=[])
    item = request.form.get('item','').strip()
    if not item:
        flash('Item is required.','error')
        return render_template('form.html',product=None,photos=[])
    try:
        price_pence = round(float(request.form.get('price','0') or 0)*100)
        quantity = max(0,int(request.form.get('quantity','1') or 1))
    except ValueError:
        flash('Price and quantity must be valid numbers.','error')
        return render_template('form.html',product=None,photos=[])
    status = request.form.get('status','Available')
    if quantity == 0:
        status = 'Sold'
    files = [request.files.get(f'photo{i}') for i in range(1,MAX_PHOTOS+1)]
    try:
        with db() as conn:
            inventory_id = next_inventory_id(conn)
            cur = conn.execute('''INSERT INTO products (inventory_id,item,main_colour,secondary_colour,pattern,price_pence,barcode,quantity,status,date_added,date_sold,notes) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)''',(
                inventory_id,item,request.form.get('main_colour','').strip(),request.form.get('secondary_colour','').strip(),request.form.get('pattern','').strip(),price_pence,inventory_id,quantity,status,datetime.now().isoformat(timespec='seconds'),datetime.now().isoformat(timespec='seconds') if status=='Sold' else None,request.form.get('notes','').strip()))
            product_id = cur.lastrowid
            for filename,order in save_uploaded_photos(product_id,files):
                conn.execute('INSERT INTO photos(product_id,filename,sort_order) VALUES(?,?,?)',(product_id,filename,order))
    except sqlite3.IntegrityError:
        flash('Could not assign a unique product barcode. Please try again.','error')
        return render_template('form.html',product=None,photos=[])
    return redirect(url_for('product_detail',product_id=product_id))


@app.route('/product/<int:product_id>')
def product_detail(product_id):
    with db() as conn:
        product,photos = product_with_photos(conn,product_id)
    if not product:
        abort(404)
    return render_template('detail.html',product=product,photos=photos)


@app.route('/product/<int:product_id>/edit',methods=['GET','POST'])
def edit_product(product_id):
    with db() as conn:
        product,photos = product_with_photos(conn,product_id)
    if not product:
        abort(404)
    if request.method == 'GET':
        return render_template('form.html',product=product,photos=photos)
    try:
        price_pence = round(float(request.form.get('price','0') or 0)*100)
        quantity = max(0,int(request.form.get('quantity',product['quantity']) or 0))
    except ValueError:
        flash('Price and quantity must be valid numbers.','error')
        return render_template('form.html',product=product,photos=photos)
    status = request.form.get('status','Available')
    if quantity == 0:
        status = 'Sold'
    date_sold = product['date_sold']
    if status == 'Sold' and product['status'] != 'Sold':
        date_sold = datetime.now().isoformat(timespec='seconds')
    elif status == 'Available':
        date_sold = None
    with db() as conn:
        conn.execute('''UPDATE products SET item=?,main_colour=?,secondary_colour=?,pattern=?,price_pence=?,barcode=inventory_id,quantity=?,status=?,date_sold=?,notes=? WHERE id=?''',(
            request.form.get('item','').strip(),request.form.get('main_colour','').strip(),request.form.get('secondary_colour','').strip(),request.form.get('pattern','').strip(),price_pence,quantity,status,date_sold,request.form.get('notes','').strip(),product_id))
        existing = conn.execute('SELECT COUNT(*) c FROM photos WHERE product_id=?',(product_id,)).fetchone()['c']
        files = [request.files.get(f'photo{i}') for i in range(1,MAX_PHOTOS+1)][:max(0,MAX_PHOTOS-existing)]
        for filename,order in save_uploaded_photos(product_id,files,existing+1):
            conn.execute('INSERT INTO photos(product_id,filename,sort_order) VALUES(?,?,?)',(product_id,filename,order))
    return redirect(url_for('product_detail',product_id=product_id))


@app.post('/product/<int:product_id>/toggle')
def toggle_status(product_id):
    with db() as conn:
        product = conn.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        if not product:
            abort(404)
        if product['status'] == 'Available':
            conn.execute('UPDATE products SET status="Sold",date_sold=? WHERE id=?',(datetime.now().isoformat(timespec='seconds'),product_id))
        elif product['quantity'] > 0:
            conn.execute('UPDATE products SET status="Available",date_sold=NULL WHERE id=?',(product_id,))
        else:
            flash('Add stock before marking this product Available.','error')
    return redirect(request.referrer or url_for('index'))


@app.post('/product/<int:product_id>/sell-one')
def sell_one(product_id):
    with db() as conn:
        product = conn.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        if not product:
            abort(404)
        quantity = max(0,product['quantity']-1)
        if quantity == 0:
            conn.execute('UPDATE products SET quantity=0,status="Sold",date_sold=? WHERE id=?',(datetime.now().isoformat(timespec='seconds'),product_id))
        else:
            conn.execute('UPDATE products SET quantity=? WHERE id=?',(quantity,product_id))
    return redirect(request.referrer or url_for('product_detail',product_id=product_id))


@app.post('/product/<int:product_id>/restock-one')
def restock_one(product_id):
    with db() as conn:
        product = conn.execute('SELECT * FROM products WHERE id=?',(product_id,)).fetchone()
        if not product:
            abort(404)
        new_quantity = product['quantity'] + 1
        conn.execute('UPDATE products SET quantity=?,status="Available",date_sold=NULL WHERE id=?',(new_quantity,product_id))
    return redirect(request.referrer or url_for('product_detail',product_id=product_id))


@app.post('/product/<int:product_id>/delete')
def delete_product(product_id):
    with db() as conn:
        photos = conn.execute('SELECT filename FROM photos WHERE product_id=?',(product_id,)).fetchall()
        conn.execute('DELETE FROM photos WHERE product_id=?',(product_id,))
        conn.execute('DELETE FROM products WHERE id=?',(product_id,))
    for photo in photos:
        try:
            (PHOTO_DIR/photo['filename']).unlink(missing_ok=True)
        except OSError:
            pass
    return redirect(url_for('index'))


@app.post('/photo/<int:photo_id>/delete')
def delete_photo(photo_id):
    with db() as conn:
        photo = conn.execute('SELECT * FROM photos WHERE id=?',(photo_id,)).fetchone()
        if not photo:
            abort(404)
        product_id = photo['product_id']
        conn.execute('DELETE FROM photos WHERE id=?',(photo_id,))
        remaining = conn.execute('SELECT id FROM photos WHERE product_id=? ORDER BY sort_order,id',(product_id,)).fetchall()
        for i,row in enumerate(remaining,1):
            conn.execute('UPDATE photos SET sort_order=? WHERE id=?',(i,row['id']))
    try:
        (PHOTO_DIR/photo['filename']).unlink(missing_ok=True)
    except OSError:
        pass
    return redirect(url_for('edit_product',product_id=product_id))


@app.route('/photos/<path:filename>')
def photo_file(filename):
    return send_from_directory(PHOTO_DIR,filename)


@app.route('/updates')
def updates():
    latest = latest_version()
    try:
        updater_status = (UPDATER_DIR/'status').read_text().strip() or 'unknown'
    except OSError:
        updater_status = 'not started'
    return render_template('updates.html',current_version=VERSION,latest_version=latest,update_available=bool(latest and version_tuple(latest)>version_tuple(VERSION)),updater_status=updater_status,update_log=tail(UPDATER_DIR/'update.log'))


@app.post('/updates/install')
def install_update():
    latest = latest_version()
    if not latest:
        flash('Could not contact GitHub. Update was not requested.','error')
        return redirect(url_for('updates'))
    if version_tuple(latest) <= version_tuple(VERSION):
        flash('This installation is already up to date.','info')
        return redirect(url_for('updates'))
    try:
        (UPDATER_DIR/'update.request').write_text(datetime.now().isoformat(timespec='seconds'))
        flash(f'Update to v{latest} requested. The app may briefly restart.','success')
    except OSError as exc:
        flash(f'Could not request update: {exc}','error')
    return redirect(url_for('updates'))


@app.route('/health')
def health():
    return {'service':'raes-bits-and-bobbins-stock','version':VERSION,'status':'ok'}


init_db()

if __name__=='__main__':
    app.run(host='0.0.0.0',port=8080,debug=False)
