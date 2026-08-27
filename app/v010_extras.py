import sqlite3
from datetime import date, datetime

from flask import render_template, request


def configure(app, server):
    def db():
        conn = sqlite3.connect(server.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn

    # Give already-sold physical items a history entry without pretending we know
    # which event they were sold at. This runs once because barcode_id is checked.
    try:
        with db() as conn:
            sold = conn.execute("SELECT ib.*,p.item,p.price_pence FROM item_barcodes ib JOIN products p ON p.id=ib.product_id WHERE ib.state='Sold'").fetchall()
            for row in sold:
                exists = conn.execute('SELECT 1 FROM sales WHERE barcode_id=? LIMIT 1', (row['id'],)).fetchone()
                if not exists:
                    conn.execute('''INSERT INTO sales(product_id,barcode_id,barcode,product_name,price_pence,sold_at,source,event_id)
                                    VALUES(?,?,?,?,?,?,?,NULL)''',
                                 (row['product_id'], row['id'], row['barcode'], row['item'], row['price_pence'],
                                  row['sold_at'] or datetime.now().isoformat(timespec='seconds'), 'Before v0.1.0'))
    except sqlite3.Error:
        pass

    def paypal_page():
        today = date.today().isoformat()
        with db() as conn:
            events = conn.execute('SELECT * FROM events ORDER BY start_date DESC,id DESC').fetchall()
            active = conn.execute('SELECT * FROM events WHERE manual_active=1 ORDER BY id DESC LIMIT 1').fetchone()
            if not active:
                active = conn.execute('SELECT * FROM events WHERE manual_active<>-1 AND start_date<=? AND end_date>=? ORDER BY id DESC LIMIT 1', (today, today)).fetchone()
        return render_template('paypal.html', events=events, active_event=active)
    app.view_functions['paypal'] = paypal_page

    original_import = app.view_functions.get('paypal_import')
    if original_import:
        def paypal_import_with_event():
            event_id = request.form.get('event_id', '').strip()
            if not event_id.isdigit():
                return original_import()
            event_id = int(event_id)
            with db() as conn:
                states = [(r['id'], r['manual_active']) for r in conn.execute('SELECT id,manual_active FROM events').fetchall()]
                chosen = conn.execute('SELECT 1 FROM events WHERE id=?', (event_id,)).fetchone()
                if not chosen:
                    return original_import()
                conn.execute('UPDATE events SET manual_active=0')
                conn.execute('UPDATE events SET manual_active=1 WHERE id=?', (event_id,))
            try:
                return original_import()
            finally:
                with db() as conn:
                    for eid, state in states:
                        conn.execute('UPDATE events SET manual_active=? WHERE id=?', (state, eid))
        app.view_functions['paypal_import'] = paypal_import_with_event
