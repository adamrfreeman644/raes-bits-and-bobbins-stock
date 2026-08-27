# Rae's Bits and Bobbins Stock v0.1.0

Self-hosted stock and sales manager designed for barcode-scanner Android devices, tablets and desktop browsers.

## v0.1.0 highlights

- Parent products with multiple unique physical-item barcodes
- Quantity derived from available physical barcodes
- PayPal POS CSV export/import with one row per physical barcode
- Dashboard with today's sales, revenue, available stock and retail stock value
- Permanent sales history that keeps barcode, product name, sale price, source and event
- Events / pop-up shops with scheduled dates plus manual Start / End controls
- PayPal imports can be assigned to a specific event even when imported later
- Activity log for important stock, sale, event, photo, backup and product changes
- Small Undo control for supported recent actions such as sales and archive
- Normal Inventory search also searches physical barcodes; an exact barcode scan opens the product directly
- Product archive and restore
- Permanent product deletion remains available for genuine mistakes; products with sales history are protected from deletion
- Automatic daily SQLite backups
- Manual Create Backup Now, Download, Restore and Delete controls
- Safety backup created before restoring a backup
- Photo management with Download, Download Original, Set Main, Crop, Reset Crop and Remove
- Cropping preserves the original photo and intentionally provides no further image editing tools
- Photo Shoot workflow continues to resolve any physical item barcode back to its parent product
- In-app updater with database backup before updates

## Install

```bash
git clone https://github.com/adamrfreeman644/raes-bits-and-bobbins-stock.git
cd raes-bits-and-bobbins-stock
docker compose up -d --build
```

Open:

```text
http://SERVER-IP:1975
```

Health check:

```text
http://SERVER-IP:1975/health
```

## Persistent data

- `./data/inventory.db` — SQLite database
- `./photos/` — current product photos and preserved crop originals
- `./backups/` — automatic, manual, updater and pre-restore database backups
- `./updater-state/` — updater status and log files

These directories are mounted separately from the application image so rebuilding or updating the container does not replace inventory data.

## Sales and events

A physical barcode changing from Available to Sold creates a permanent sales transaction. The transaction stores the price at the time of sale rather than reading the product's current price later.

If an event is active, the sale is attached to that event. Scheduled events activate automatically while their date range is current unless manually ended. PayPal CSV imports can also be explicitly assigned to an event so a file imported after a market closes still records those sales against the correct show.

Restoring an item marks the corresponding sale as returned; it does not erase the original transaction.

## Backups

The application creates one automatic backup per day when it is in use. A database backup is also made by the updater before installing new code. The Backups page can create additional backups, download them and restore them. Restoring first creates a safety copy of the current database.

## Updating

The updater checks GitHub for the `VERSION` file but never automatically installs a release. Installation remains a deliberate button press in the web interface.
