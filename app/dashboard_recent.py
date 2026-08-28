from datetime import date

from flask import render_template


def configure(app, server):
    """Add tenant-safe recently-added products to the existing dashboard."""

    def current_event(conn):
        manual = conn.execute(
            'SELECT * FROM events WHERE manual_active=1 ORDER BY id DESC LIMIT 1'
        ).fetchone()
        if manual:
            return manual
        today = date.today().isoformat()
        return conn.execute(
            '''SELECT * FROM events
               WHERE manual_active<>-1 AND start_date<=? AND end_date>=?
               ORDER BY id DESC LIMIT 1''',
            (today, today),
        ).fetchone()

    def dashboard():
        today = date.today().isoformat()
        with server.db() as conn:
            active = current_event(conn)
            totals = conn.execute(
                '''SELECT COUNT(*) sales, COALESCE(SUM(price_pence),0) revenue
                   FROM sales
                   WHERE returned_at IS NULL AND substr(sold_at,1,10)=?''',
                (today,),
            ).fetchone()
            stock = conn.execute(
                '''SELECT COALESCE(SUM(quantity),0) qty,
                          COALESCE(SUM(quantity*price_pence),0) value
                   FROM products WHERE archived_at IS NULL'''
            ).fetchone()
            recent_sales = conn.execute(
                '''SELECT s.*,e.name event_name
                   FROM sales s LEFT JOIN events e ON e.id=s.event_id
                   ORDER BY s.id DESC LIMIT 12'''
            ).fetchall()
            recent_activity = conn.execute(
                'SELECT * FROM activity_log ORDER BY id DESC LIMIT 10'
            ).fetchall()
            recent_products = conn.execute(
                '''SELECT p.*,
                          ph.filename AS main_photo,
                          (SELECT MIN(ib.barcode)
                           FROM item_barcodes ib
                           WHERE ib.product_id=p.id) AS first_barcode
                   FROM products p
                   LEFT JOIN photos ph ON ph.id=(
                       SELECT id FROM photos
                       WHERE product_id=p.id
                       ORDER BY sort_order,id LIMIT 1
                   )
                   WHERE p.archived_at IS NULL
                   ORDER BY p.id DESC
                   LIMIT 6'''
            ).fetchall()
            event_totals = None
            if active:
                event_totals = conn.execute(
                    '''SELECT COUNT(*) sales,COALESCE(SUM(price_pence),0) revenue
                       FROM sales
                       WHERE event_id=? AND returned_at IS NULL''',
                    (active['id'],),
                ).fetchone()

        return render_template(
            'dashboard.html',
            active_event=active,
            today_sales=totals,
            stock=stock,
            recent_sales=recent_sales,
            recent_activity=recent_activity,
            recent_products=recent_products,
            event_totals=event_totals,
        )

    app.view_functions['dashboard'] = dashboard
