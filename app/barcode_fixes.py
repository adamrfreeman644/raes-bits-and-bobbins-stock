"""Barcode maintenance fixes for migrated inventory records.

Older products can retain a legacy barcode/inventory_id value after their
physical item barcodes have been migrated into item_barcodes.  The legacy
initialisation code treats those values as migration input, so deleting or
editing the original barcode could otherwise cause it to be recreated on the
next request.
"""

import sqlite3

from flask import abort, flash, redirect, request, url_for


def configure(app, server):
    def clear_legacy_reference(conn, product_id, barcode):
        conn.execute(
            """UPDATE products
               SET barcode=CASE WHEN barcode=? THEN NULL ELSE barcode END,
                   inventory_id=CASE WHEN inventory_id=? THEN NULL ELSE inventory_id END
               WHERE id=?""",
            (barcode, barcode, product_id),
        )

    def delete_item_barcode(barcode_id):
        with server.db() as conn:
            row = conn.execute(
                'SELECT * FROM item_barcodes WHERE id=?', (barcode_id,)
            ).fetchone()
            if not row:
                abort(404)

            product_id = row['product_id']
            old_barcode = row['barcode']
            conn.execute('DELETE FROM item_barcodes WHERE id=?', (barcode_id,))
            clear_legacy_reference(conn, product_id, old_barcode)
            server.sync_product(conn, product_id)

        flash(f'Barcode {old_barcode} removed.', 'success')
        return redirect(url_for('product_detail', product_id=product_id))

    def edit_item_barcode(barcode_id):
        new_barcode = request.form.get('barcode', '').strip()
        with server.db() as conn:
            row = conn.execute(
                'SELECT * FROM item_barcodes WHERE id=?', (barcode_id,)
            ).fetchone()
            if not row:
                abort(404)

            product_id = row['product_id']
            old_barcode = row['barcode']
            if not new_barcode:
                flash('Barcode cannot be empty.', 'error')
                return redirect(url_for('product_detail', product_id=product_id))

            try:
                conn.execute(
                    'UPDATE item_barcodes SET barcode=? WHERE id=?',
                    (new_barcode, barcode_id),
                )
                clear_legacy_reference(conn, product_id, old_barcode)
            except sqlite3.IntegrityError:
                flash(f'Barcode {new_barcode} is already in use.', 'error')
                return redirect(url_for('product_detail', product_id=product_id))

        flash('Barcode updated.', 'success')
        return redirect(url_for('product_detail', product_id=product_id))

    # Replace the legacy handlers without changing route URLs or templates.
    app.view_functions['delete_item_barcode'] = delete_item_barcode
    app.view_functions['edit_item_barcode'] = edit_item_barcode
