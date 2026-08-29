import re
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for


CORE_FIELDS = [
    ('barcode', 'Barcode', 'Barcode', True),
    ('item', 'Item', 'Text', True),
    ('main_colour', 'Main colour', 'Text', False),
    ('secondary_colour', 'Secondary colour', 'Text', False),
    ('pattern', 'Pattern', 'Text', False),
    ('price', 'Price', 'Money', False),
    ('notes', 'Notes', 'Long text', False),
    ('photos', 'Photos', 'Images', False),
]

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

TABLE_COLUMNS = [
    ('photo', 'Photo', False),
    ('item', 'Item', True),
    ('main_colour', 'Main colour', False),
    ('secondary_colour', 'Secondary colour', False),
    ('pattern', 'Pattern', False),
    ('price', 'Price', False),
    ('quantity', 'Available Qty', False),
    ('barcodes', 'Barcodes', False),
    ('status', 'Status', False),
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
            CREATE TABLE IF NOT EXISTS core_field_settings (
                field_key TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100
            );
            CREATE TABLE IF NOT EXISTS inventory_table_columns (
                field_key TEXT PRIMARY KEY,
                enabled INTEGER NOT NULL DEFAULT 1,
                sort_order INTEGER NOT NULL DEFAULT 100
            );
            ''')
            core_cols = {r['name'] for r in conn.execute('PRAGMA table_info(core_field_settings)').fetchall()}
            if 'sort_order' not in core_cols:
                conn.execute('ALTER TABLE core_field_settings ADD COLUMN sort_order INTEGER NOT NULL DEFAULT 100')
            for order, (key, _label, _field_type, _required) in enumerate(CORE_FIELDS, 10):
                conn.execute('INSERT OR IGNORE INTO core_field_settings(field_key,enabled,sort_order) VALUES(?,1,?)', (key, order))
            for order, (key, label, field_type) in enumerate(PRESET_FIELDS, 100):
                conn.execute('''INSERT OR IGNORE INTO custom_field_definitions
                    (field_key,label,field_type,options,enabled,is_preset,sort_order,created_at)
                    VALUES(?,?,?,NULL,0,1,?,?)''',
                    (key, label, field_type, order, datetime.now().isoformat(timespec='seconds')))
            for order, (key, _label, _required) in enumerate(TABLE_COLUMNS, 10):
                conn.execute('INSERT OR IGNORE INTO inventory_table_columns(field_key,enabled,sort_order) VALUES(?,1,?)', (key, order))

    def core_fields_for_settings():
        ensure_schema()
        with server.db() as conn:
            settings = {r['field_key']: r for r in conn.execute('SELECT * FROM core_field_settings').fetchall()}
        result = []
        for key, label, field_type, required in CORE_FIELDS:
            row = settings.get(key)
            result.append({
                'kind': 'core', 'id': key, 'field_key': key, 'label': label,
                'field_type': field_type, 'required': required,
                'enabled': True if required else bool(row['enabled']) if row else True,
                'sort_order': int(row['sort_order']) if row else 100,
            })
        return result

    def fields(enabled_only=False):
        ensure_schema()
        sql = 'SELECT * FROM custom_field_definitions'
        if enabled_only:
            sql += ' WHERE enabled=1'
        sql += ' ORDER BY sort_order,id'
        with server.db() as conn:
            return conn.execute(sql).fetchall()

    def ordered_product_fields(enabled_only=True):
        result = core_fields_for_settings()
        for row in fields(enabled_only=False):
            result.append({
                'kind': 'custom', 'id': int(row['id']), 'field_key': row['field_key'],
                'label': row['label'], 'field_type': row['field_type'], 'options': row['options'],
                'required': False, 'enabled': bool(row['enabled']),
                'is_preset': bool(row['is_preset']), 'sort_order': int(row['sort_order']),
            })
        if enabled_only:
            result = [f for f in result if f['enabled']]
        return sorted(result, key=lambda f: (f['sort_order'], 0 if f['kind'] == 'core' else 1, str(f['id'])))

    def table_columns_for_settings(enabled_only=False):
        ensure_schema()
        with server.db() as conn:
            settings = {r['field_key']: r for r in conn.execute('SELECT * FROM inventory_table_columns').fetchall()}
        result = []
        for key, label, required in TABLE_COLUMNS:
            row = settings.get(key)
            enabled = True if required else bool(row['enabled']) if row else True
            result.append({'field_key': key, 'label': label, 'required': required, 'enabled': enabled,
                           'sort_order': int(row['sort_order']) if row else 100})
        if enabled_only:
            result = [c for c in result if c['enabled']]
        return sorted(result, key=lambda c: (c['sort_order'], c['field_key']))

    def core_visibility():
        return {f['field_key']: f['enabled'] for f in core_fields_for_settings()}

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
    app.extensions['core_field_visibility'] = core_visibility
    app.extensions['ordered_product_fields'] = ordered_product_fields
    app.extensions['table_columns'] = table_columns_for_settings

    @app.context_processor
    def custom_field_context():
        try:
            product_id = request.view_args.get('product_id') if request.view_args else None
            return {
                'product_fields': ordered_product_fields(enabled_only=True),
                'custom_fields': fields(enabled_only=True),
                'custom_values': values_for(product_id),
                'field_visibility': core_visibility(),
                'table_columns': table_columns_for_settings(enabled_only=True),
            }
        except Exception:
            return {'product_fields': [], 'custom_fields': [], 'custom_values': {}, 'field_visibility': {}, 'table_columns': []}

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
        return render_template('fields.html',
                               product_fields=ordered_product_fields(enabled_only=False),
                               table_fields=table_columns_for_settings(enabled_only=False))

    @app.post('/account/fields/core/toggle/<field_key>')
    def toggle_account_core_field(field_key):
        definition = next((f for f in CORE_FIELDS if f[0] == field_key), None)
        if not definition:
            abort(404)
        if definition[3]:
            flash(f'{definition[1]} is required by the stock system and must stay on.', 'info')
            return redirect(url_for('account_fields'))
        ensure_schema()
        with server.db() as conn:
            row = conn.execute('SELECT enabled FROM core_field_settings WHERE field_key=?', (field_key,)).fetchone()
            enabled = bool(row['enabled']) if row else True
            conn.execute('UPDATE core_field_settings SET enabled=? WHERE field_key=?', (0 if enabled else 1, field_key))
        return redirect(url_for('account_fields'))

    @app.post('/account/fields/toggle/<int:field_id>')
    def toggle_account_field(field_id):
        ensure_schema()
        with server.db() as conn:
            row = conn.execute('SELECT enabled FROM custom_field_definitions WHERE id=?', (field_id,)).fetchone()
            if not row:
                abort(404)
            conn.execute('UPDATE custom_field_definitions SET enabled=? WHERE id=?', (0 if row['enabled'] else 1, field_id))
        return redirect(url_for('account_fields'))

    @app.post('/account/fields/table/toggle/<field_key>')
    def toggle_table_field(field_key):
        definition = next((f for f in TABLE_COLUMNS if f[0] == field_key), None)
        if not definition:
            abort(404)
        if definition[2]:
            flash(f'{definition[1]} must stay visible in the table.', 'info')
            return redirect(url_for('account_fields'))
        ensure_schema()
        with server.db() as conn:
            row = conn.execute('SELECT enabled FROM inventory_table_columns WHERE field_key=?', (field_key,)).fetchone()
            enabled = bool(row['enabled']) if row else True
            conn.execute('UPDATE inventory_table_columns SET enabled=? WHERE field_key=?', (0 if enabled else 1, field_key))
        return redirect(url_for('account_fields'))

    @app.post('/account/fields/order')
    def save_field_order():
        ensure_schema()
        kind = request.form.get('kind', '')
        keys = [k for k in request.form.get('order', '').split(',') if k]
        with server.db() as conn:
            if kind == 'product':
                for order, token in enumerate(keys, 10):
                    if token.startswith('core:'):
                        conn.execute('UPDATE core_field_settings SET sort_order=? WHERE field_key=?', (order, token[5:]))
                    elif token.startswith('custom:'):
                        try:
                            conn.execute('UPDATE custom_field_definitions SET sort_order=? WHERE id=?', (order, int(token[7:])))
                        except ValueError:
                            pass
            elif kind == 'table':
                for order, key in enumerate(keys, 10):
                    conn.execute('UPDATE inventory_table_columns SET sort_order=? WHERE field_key=?', (order, key))
            else:
                abort(400)
        return ('', 204)

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
