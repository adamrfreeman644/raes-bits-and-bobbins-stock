# Rae's Bits and Bobbins Stock v0.0.1

Bare-bones self-hosted inventory database designed for a barcode scanner/Android device and desktop browser.

## Included

- SQLite inventory database
- Card view and Airtable-style table view
- Add/edit/delete products
- Barcode-first registration screen
- Item, main colour, secondary colour, pattern, price, barcode and status
- Available / Sold toggle with sold date
- Up to 5 product photos
- Main photo used as card/table thumbnail
- Search and status filtering
- Responsive scanner/mobile UI
- Persistent database and photo folders separated from application code
- `/health` endpoint

## Install from GitHub

```bash
git clone https://github.com/adamrfreeman644/raes-bits-and-bobbins-stock.git
cd raes-bits-and-bobbins-stock
docker compose up -d --build
```

Open:

```text
http://SERVER-IP:8080
```

Health check:

```text
http://SERVER-IP:8080/health
```

## Persistent data

- `./data/inventory.db` — SQLite database
- `./photos/` — uploaded product images
- `./backups/` — reserved for future backups

These folders are mounted separately so future application updates can replace the app without replacing inventory data.

## Future update path

The project is structured so later versions can use the same GitHub/manual-update model throughout. Inventory data and photos remain persistent and are not stored in the release code.

## v0.0.1 intentionally does not include

- Shopify/Etsy/PayPal integration
- CSV shop export
- GitHub in-app updater
- Login/PIN system
- Configurable dropdown lists
- Automated backups

Those are intended for later versions after the core workflow is tested.
