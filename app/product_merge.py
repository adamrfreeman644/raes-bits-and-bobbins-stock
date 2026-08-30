import sqlite3
from collections import defaultdict
from datetime import datetime

from flask import abort, flash, redirect, render_template, request, url_for


def configure(app, server):
    def db():
        return server.db()

    def norm(value):
        return ' '.join(str(value or '').strip().lower().split())

    def custom_values(conn, product_id):
        try:
            rows = conn.execute(
                'SELECT field_id,value FROM product_custom_values WHERE product_id=?',
                (product_id,),
            ).fetchall()
        except sqlite3.Error:
            return {}
        # Every non-empty custom field participates in the exact-match signature.
        # This includes Tags (and any future tag-like custom fields), so products
        # with different tag sets are never offered for merge.
        return {int(row['field_id']): norm(row['value']) for row in rows if norm(row['value'])}

    def signature(conn, product):
        return (
            norm(product['item']),
            norm(product['main_colour']),
            norm(product['secondary_colour']),
            norm(product['pattern']),
            int(product['price_pence'] or 0),
            norm(product['notes']),
            tuple(sorted(custom_values(conn, product['id']).items())),
        )

    def photo_count(conn, product_id):
        return int(conn.execute('SELECT COUNT(*) n FROM photos WHERE product_id=?', (product_id,)).fetchone()['n'])

    def barcode_counts(conn, product_id):
        row = conn.execute(
            """SELECT COUNT(*) total,
                      SUM(CASE WHEN state='Available' THEN 1 ELSE 0 END) available
               FROM item_barcodes WHERE product_id=?""",
            (product_id,),
        ).fetchone()
        return int(row['total'] or 0), int(row['available'] or 0)

    def decorate(conn, product):
        total, available = barcode_counts(conn, product['id'])
        return {
            'product': product,
            'barcode_count': total,
            'available_count': available,
            'photo_count': photo_count(conn, product['id']),
        }

    def duplicate_groups(conn):
        products = conn.execute(
            'SELECT * FROM products WHERE archived_at IS NULL ORDER BY id'
        ).fetchall()
        grouped = defaultdict(list)
        for product in products:
            grouped[signature(conn, product)].append(product)
        groups = []
        for members in grouped.values():
            if len(members) < 2:
                continue
            members = sorted(members, key=lambda p: int(p['id']))
            groups.append({
                'target': decorate(conn, members[0]),
                'duplicates': [decorate(conn, p) for p in members[1:]],
                'count': len(members),
            })
        groups.sort(key=lambda g: (norm(g['target']['product']['item']), int(g['target']['product']['id'])))
        return groups

    def merge_into_target(conn, product_id, source_ids):
        target = conn.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
        if not target:
            raise LookupError(f'Product #{product_id} was not found.')
        expected = signature(conn, target)
        sources = []
        for source_id in sorted({int(v) for v in source_ids if int(v) != product_id}):
            source = conn.execute('SELECT * FROM products WHERE id=?', (source_id,)).fetchone()
            if not source:
                raise LookupError(f'Product #{source_id} was not found.')
            if source['archived_at'] is not None or signature(conn, source) != expected:
                raise ValueError(f'Product #{source_id} no longer exactly matches product #{product_id}.')
            sources.append(source)

        moved_barcodes = 0
        moved_photos = 0
        moved_sources = []
        for source in sources:
            source_id = int(source['id'])
            moved_sources.append(source_id)

            count = conn.execute('SELECT COUNT(*) n FROM item_barcodes WHERE product_id=?', (source_id,)).fetchone()['n']
            moved_barcodes += int(count or 0)
            conn.execute('UPDATE item_barcodes SET product_id=? WHERE product_id=?', (product_id, source_id))

            max_order = conn.execute(
                'SELECT COALESCE(MAX(sort_order),0) n FROM photos WHERE product_id=?',
                (product_id,),
            ).fetchone()['n']
            photos = conn.execute(
                'SELECT id,sort_order FROM photos WHERE product_id=? ORDER BY sort_order,id',
                (source_id,),
            ).fetchall()
            for offset, photo in enumerate(photos, 1):
                conn.execute(
                    'UPDATE photos SET product_id=?,sort_order=? WHERE id=?',
                    (product_id, int(max_order or 0) + offset, photo['id']),
                )
            moved_photos += len(photos)

            try:
                values = conn.execute(
                    'SELECT field_id,value FROM product_custom_values WHERE product_id=?',
                    (source_id,),
                ).fetchall()
                for value in values:
                    current = conn.execute(
                        'SELECT value FROM product_custom_values WHERE product_id=? AND field_id=?',
                        (product_id, value['field_id']),
                    ).fetchone()
                    if not current:
                        conn.execute(
                            'INSERT INTO product_custom_values(product_id,field_id,value) VALUES(?,?,?)',
                            (product_id, value['field_id'], value['value']),
                        )
                    elif not norm(current['value']) and norm(value['value']):
                        conn.execute(
                            'UPDATE product_custom_values SET value=? WHERE product_id=? AND field_id=?',
                            (value['value'], product_id, value['field_id']),
                        )
                conn.execute('DELETE FROM product_custom_values WHERE product_id=?', (source_id,))
            except sqlite3.Error:
                pass

            for table in ('sales', 'activity_log', 'photo_shoot_scans'):
                try:
                    conn.execute(f'UPDATE {table} SET product_id=? WHERE product_id=?', (product_id, source_id))
                except sqlite3.Error:
                    pass

            conn.execute('DELETE FROM products WHERE id=?', (source_id,))

        server.sync_product(conn, product_id)
        try:
            conn.execute(
                '''INSERT INTO activity_log(created_at,action,description,product_id,undo_kind,undo_payload)
                   VALUES(?,?,?,?,NULL,NULL)''',
                (
                    datetime.now().isoformat(timespec='seconds'),
                    'Product',
                    f"Merged product records {', '.join('#'+str(i) for i in moved_sources)} into #{product_id}; "
                    f"moved {moved_barcodes} barcodes and {moved_photos} photos",
                    product_id,
                ),
            )
        except sqlite3.Error:
            pass
        return {
            'sources': len(moved_sources),
            'barcodes': moved_barcodes,
            'photos': moved_photos,
        }

    @app.get('/products/merge-duplicates')
    def merge_duplicates_global():
        with db() as conn:
            groups = duplicate_groups(conn)
        return render_template('merge_duplicates_global.html', groups=groups)

    @app.post('/products/merge-duplicates')
    def merge_duplicates_global_apply():
        with db() as conn:
            groups = duplicate_groups(conn)
            if not groups:
                flash('No exact duplicate products were found.', 'info')
                return redirect(url_for('merge_duplicates_global'))

            totals = {'groups': 0, 'sources': 0, 'barcodes': 0, 'photos': 0}
            try:
                for group in groups:
                    target_id = int(group['target']['product']['id'])
                    source_ids = [int(item['product']['id']) for item in group['duplicates']]
                    result = merge_into_target(conn, target_id, source_ids)
                    totals['groups'] += 1
                    totals['sources'] += result['sources']
                    totals['barcodes'] += result['barcodes']
                    totals['photos'] += result['photos']
            except (LookupError, ValueError) as exc:
                conn.rollback()
                flash(f'Stock-wide merge stopped safely: {exc} Nothing was merged.', 'error')
                return redirect(url_for('merge_duplicates_global'))

        flash(
            f"Merged {totals['sources']} duplicate product record(s) across {totals['groups']} group(s). "
            f"{totals['barcodes']} barcodes and {totals['photos']} photos were preserved.",
            'success',
        )
        return redirect(url_for('index'))

    @app.get('/product/<int:product_id>/merge')
    def merge_product_picker(product_id):
        with db() as conn:
            target = conn.execute('SELECT * FROM products WHERE id=?', (product_id,)).fetchone()
            if not target:
                abort(404)
            target_signature = signature(conn, target)
            others = conn.execute(
                'SELECT * FROM products WHERE id<>? AND archived_at IS NULL ORDER BY item COLLATE NOCASE,id',
                (product_id,),
            ).fetchall()
            exact = [decorate(conn, p) for p in others if signature(conn, p) == target_signature]
            target_info = decorate(conn, target)
        return render_template('merge_products.html', target_info=target_info, exact_matches=exact)

    @app.post('/product/<int:product_id>/merge')
    def merge_products(product_id):
        raw_ids = request.form.getlist('source_id')
        try:
            source_ids = sorted({int(value) for value in raw_ids if int(value) != product_id})
        except (TypeError, ValueError):
            source_ids = []
        if not source_ids:
            flash('Choose at least one identical product to merge.', 'error')
            return redirect(url_for('merge_product_picker', product_id=product_id))

        with db() as conn:
            try:
                result = merge_into_target(conn, product_id, source_ids)
            except LookupError:
                abort(404)
            except ValueError as exc:
                conn.rollback()
                flash(f'{exc} Nothing was merged.', 'error')
                return redirect(url_for('merge_product_picker', product_id=product_id))

        flash(
            f"Merged {result['sources']} duplicate product record(s). "
            f"{result['barcodes']} barcodes and {result['photos']} photos were preserved.",
            'success',
        )
        return redirect(url_for('product_detail', product_id=product_id))

    return app
