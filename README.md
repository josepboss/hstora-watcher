# HStora Watcher

A lightweight service that scans the official HStora Partner API and sends Telegram notifications when:

- a selected product's price or title changes;
- stock crosses a low-stock threshold, sells out, or comes back;
- a matching product's price drops;
- a newly listed keyword match is cheaper than every previously seen match.

State is kept in SQLite, so restarts do not create duplicate alerts.

It also includes a password-protected dashboard for managing product watches, keyword watches, catalog searches, manual checks, Telegram testing, and recent activity.

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

## Use

Search every catalog page, with cheapest results first:

```bash
hstora-watcher search gmail aged
```

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
