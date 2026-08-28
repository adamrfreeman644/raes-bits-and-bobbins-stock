import re
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for


PRESET_FIELDS = [
    ('board_compatibility', 'Board compatibility', 'text'),
    ('cost', 'Cost', 'money'),
    ('supplier', 'Supplier', 'text'),
    ('manufacturer', 'Manufacturer', 'text'),
    ('model_part_number', 'Model / part number', 'text'),
    ('storage_location', 'Storage location', 'text'),
    ('condition', 'Condition', 'text'),
    ('reorder_level', 'Reorder level', 'number'),
]
ALLOWED_TYPES = {'text', 'number', 'money', 'boolean', 'select'}


def configure(app, server):
    def ensure_schema():
        with server.db() as conn:
            conn.executescript('''
            CREATE TABLE IF NOT EXISTS custom_field_definitions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                field_key TEXT NOT NULL UNIQUE,
                label TEXT NOT NULL,
                field_type TEXT NOT NULL DEFAULT 'text',
                options TEXT,
                enabled INTEGER NOT NULL DEFAULT 0,
                is_preset INTEGER NOT NULL DEFAULT 0,
                sort_order INTEGER NOT NULL DEFAULT 100,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS product_custom_values (
                product_id INTEGER NOT NULL,
                field_id INTEGER NOT NULL,
                value TEXT,
                PRIMARY KEY(product_id, field_id),
                FOREIGN KEY(product_id) REFERENCES products(id) ON DELETE CASCADE,
                FOREIGN KEY(field_id) REFERENCES custom_field_definitions(id) ON DELETE CASCADE
            );
            ''')
            for order, (key, label, field_type) in enumerate(PRESET_FIELDS, 10):
                conn.execute('''INSERT OR IGNORE INTO custom_field_definitions
                    (field_key,label,field_type,options,enabled,is_preset,sort_order,created_at)
                    VALUES(?,?,?,NULL,0,1,?,?)''',
                    (key, label, field_type, order, datetime.now().isoformat(timespec='seconds')))

    def fields(enabled_only=False):
        ensure_schema()
        sql = 'SELECT * FROM custom_field_definitions'
        if enabled_only:
            sql += ' WHERE enabled=1'
        sql += ' ORDER BY sort_order,id'
        with server.db() as conn:
            return conn.execute(sql).fetchall()

    def values_for(product_id):
        if not product_id:
            return {}
        ensure_schema()
        with server.db() as conn:
            rows = conn.execute('SELECT field_id,value FROM product_custom_values WHERE product_id=?', (product_id,)).fetchall()
        return {int(r['field_id']): (r['value'] or '') for r in rows}

    def save_values(product_id):
        if not product_id:
            return
        active = fields(enabled_only=True)
        with server.db() as conn:
            for field in active:
                name = f"custom_{field['id']}"
                if field['field_type'] == 'boolean':
                    value = '1' if request.form.get(name) in ('1', 'true', 'on', 'yes') else ''
                else:
                    value = request.form.get(name, '').strip()
                conn.execute('''INSERT INTO product_custom_values(product_id,field_id,value)
                                VALUES(?,?,?)
                                ON CONFLICT(product_id,field_id) DO UPDATE SET value=excluded.value''',
                             (product_id, field['id'], value))

    app.extensions['ensure_custom_fields_schema'] = ensure_schema

    @app.context_processor
    def custom_field_context():
        try:
            active = fields(enabled_only=True)
            product_id = request.view_args.get('product_id') if request.view_args else None
            return {'custom_fields': active, 'custom_values': values_for(product_id)}
        except Exception:
            return {'custom_fields': [], 'custom_values': {}}

    @app.after_request
    def save_product_custom_fields(response):
        try:
            if request.method != 'POST' or response.status_code not in (301, 302, 303):
                return response
            product_id = None
            if request.endpoint == 'edit_product' and request.view_args:
                product_id = request.view_args.get('product_id')
            elif request.endpoint == 'new_product':
                match = re.search(r'/product/(\d+)(?:$|[/?#])', response.headers.get('Location', ''))
                if match:
                    product_id = int(match.group(1))
            if product_id:
                save_values(product_id)
        except Exception:
            app.logger.exception('Could not save custom inventory field values')
        return response

    @app.get('/account/fields')
    def account_fields():
        return render_template('fields.html', fields=fields(enabled_only=False))

    @app.post('/account/fields/toggle/<int:field_id>')
    def toggle_account_field(field_id):
        ensure_schema()
        with server.db() as conn:
            row = conn.execute('SELECT enabled FROM custom_field_definitions WHERE id=?', (field_id,)).fetchone()
            if not row:
                abort(404)
            conn.execute('UPDATE custom_field_definitions SET enabled=? WHERE id=?', (0 if row['enabled'] else 1, field_id))
        flash('Field visibility updated for this account.', 'success')
        return redirect(url_for('account_fields'))

    @app.post('/account/fields/add')
    def add_account_field():
        ensure_schema()
        label = request.form.get('label', '').strip()
        field_type = request.form.get('field_type', 'text').strip().lower()
        options = request.form.get('options', '').strip()
        if not label:
            flash('Field name is required.', 'error')
            return redirect(url_for('account_fields'))
        if field_type not in ALLOWED_TYPES:
            field_type = 'text'
        if field_type == 'select':
            choices = [x.strip() for x in options.split(',') if x.strip()]
            if not choices:
                flash('Dropdown fields need at least one comma-separated option.', 'error')
                return redirect(url_for('account_fields'))
            options = '\n'.join(choices)
        else:
            options = None
        base_key = re.sub(r'[^a-z0-9]+', '_', label.lower()).strip('_') or 'custom_field'
        with server.db() as conn:
            key = base_key
            suffix = 2
            while conn.execute('SELECT 1 FROM custom_field_definitions WHERE field_key=?', (key,)).fetchone():
                key = f'{base_key}_{suffix}'
                suffix += 1
            max_order = conn.execute('SELECT COALESCE(MAX(sort_order),100) FROM custom_field_definitions').fetchone()[0]
            conn.execute('''INSERT INTO custom_field_definitions
                (field_key,label,field_type,options,enabled,is_preset,sort_order,created_at)
                VALUES(?,?,?,?,1,0,?,?)''',
                (key, label, field_type, options, int(max_order) + 10, datetime.now().isoformat(timespec='seconds')))
        flash(f'{label} added and enabled for this account.', 'success')
        return redirect(url_for('account_fields'))

    @app.post('/account/fields/delete/<int:field_id>')
    def delete_account_field(field_id):
        ensure_schema()
        with server.db() as conn:
            row = conn.execute('SELECT is_preset FROM custom_field_definitions WHERE id=?', (field_id,)).fetchone()
            if not row:
                abort(404)
            if row['is_preset']:
                flash('Built-in optional fields can be switched off, but not deleted.', 'error')
                return redirect(url_for('account_fields'))
            conn.execute('DELETE FROM custom_field_definitions WHERE id=?', (field_id,))
        flash('Custom field deleted.', 'success')
        return redirect(url_for('account_fields'))
