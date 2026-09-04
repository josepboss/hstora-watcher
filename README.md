# HStora Watcher

A lightweight service that scans the official HStora Partner API and sends Telegram notifications when:

- a selected product's price or title changes;
- stock crosses a low-stock threshold, sells out, or comes back;
- a matching product's price drops;
- a newly listed keyword match is cheaper than every previously seen match.

State is kept in SQLite, so restarts do not create duplicate alerts.

It also includes a password-protected dashboard for managing product watches, keyword watches, catalog searches, manual checks, Telegram testing, and recent activity.

The companion Chrome extension in `chrome-extension/` can publish a catalog product as a Twitter/X Accounts offer on Z2U. The dashboard calculates the marked-up price, removes URLs and configured source-store names, and records the Z2U publication status and offer ID.

## Setup

Requirements: Python 3.11+ and an HStora API key/secret from your HStora account.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -e .
cp .env.example .env
```

Edit `.env` with your HStora credentials, Telegram bot token, and chat ID. Create a Telegram bot with `@BotFather`, send the bot a message, then obtain the chat ID from:

```text
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```

Test the connection:

```bash
hstora-watcher test-telegram
```

Set a long random `DASHBOARD_PASSWORD`. The dashboard binds to `127.0.0.1:8787` by default, ready to be placed behind an HTTPS Nginx reverse proxy.

Generate `EXTENSION_SECRET` separately—it is not the dashboard password:

```bash
openssl rand -hex 32
```

## Use

Search every catalog page. Results default to cheapest first and can also be sorted by price or available stock:

```bash
hstora-watcher search gmail aged
hstora-watcher search gmail aged --sort stock_desc
hstora-watcher search gmail aged --sort stock_asc
```

The Partner API currently does not expose a product sales count, so genuine sales sorting is unavailable. The watcher does not treat stock decreases as sales because inventory can also be edited or restocked.

Watch products and keywords:

```bash
hstora-watcher watch-product 381 --threshold 25
hstora-watcher watch-keyword gmail aged
hstora-watcher list
```

Run one check or keep the service running:

```bash
hstora-watcher check
hstora-watcher run
```

To run the dashboard and scheduler together:

```bash
hstora-watcher dashboard
```

On the first keyword check, only the cheapest matching product creates an alert. Existing results are recorded as the baseline. Later, a new listing alerts only if it is below the previous cheapest price. A price drop on any known matching listing also alerts.

Remove watches with `unwatch-product PRODUCT_ID` or `unwatch-keyword WATCH_ID`.

## Run continuously with PM2 (Linux VPS)

Install PM2, then start the included process definition:

```bash
npm install -g pm2
pm2 start ecosystem.config.cjs
pm2 save
pm2 startup
```

Run the command printed by `pm2 startup`. Useful commands:

```bash
pm2 status
pm2 logs hstora-watcher
pm2 restart hstora-watcher
```

## Install the Chrome listing assistant

1. Open `chrome://extensions` in Chrome.
2. Enable **Developer mode**.
3. Click **Load unpacked** and select the `chrome-extension` directory.
4. Open the extension and set the HTTPS backend URL, your Nginx username/password, and the same `EXTENSION_SECRET` used in `.env`, then save.
5. Reload the HStora Watcher dashboard. It should show **Connected and ready**.

Click **List on Z2U** beside any catalog product, enter the profit to add per unit, and confirm. The extension opens Z2U and applies these rules:

- category: Twitter (X) → Twitter/X Accounts;
- price: HStora price plus the chosen fixed profit;
- minimum: 2 units below $1 total unit price, otherwise 1;
- stock: 99999999;
- Brand: Twitter, Full Access: Yes, Country: Other;
- expiration: 30 days; delivery ETA: 15 minutes;
- Order Delivery and seller-policy agreement selected.

The extension then submits the form. Paste the resulting Z2U offer ID and its Z2U manage-listing URL into the extension panel. Both are stored in the backend. `LISTING_BLOCKED_TERMS` is a comma-separated list of source/store names removed from titles and descriptions. URLs are always removed automatically.

### Automatic Z2U stock synchronization

For products with a saved Z2U offer ID and manage-listing URL:

- HStora stock changing from above zero to zero queues **Deactivate**.
- HStora stock changing from zero to above zero queues **Relist**.
- The extension checks the durable backend queue every minute, opens the saved management URL in a background tab, performs the action, waits for Z2U's Success confirmation, and closes the tab.
- Failed actions retry up to three times and remain visible in dashboard activity.

Chrome must be running and signed in to Z2U for queued actions to execute. Actions remain queued while Chrome is closed, so a temporary browser outage does not lose the stock transition.

Proxy an HTTPS domain to `http://127.0.0.1:8787` with Nginx or Caddy. Do not expose the dashboard over plain HTTP because Basic Auth credentials require TLS in transit.

## Alternative: systemd

Create `/etc/systemd/system/hstora-watcher.service` (adjust paths and user):

```ini
[Unit]
Description=HStora product watcher
After=network-online.target

[Service]
Type=simple
User=ubuntu
WorkingDirectory=/opt/hstora-watcher
ExecStart=/opt/hstora-watcher/.venv/bin/hstora-watcher run
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

Then run `sudo systemctl daemon-reload`, `sudo systemctl enable --now hstora-watcher`, and inspect logs with `journalctl -u hstora-watcher -f`.

## Notes

- The official API uses signed requests. Secrets remain server-side and `.env` is ignored by Git.
- `CHECK_INTERVAL_SECONDS` defaults to 300 seconds and cannot be lower than 10 seconds.
- Keyword matching requires every space-separated word to appear in the title, in any order.
- A per-product `--threshold` overrides `LOW_STOCK_THRESHOLD`.
