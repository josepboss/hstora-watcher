from __future__ import annotations

import base64
import hmac
import json
import logging
import threading
import time
import urllib.parse
from dataclasses import asdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

from .api import HstoraClient, SORT_OPTIONS, sort_products
from .config import Config
from .listing import prepare_listing
from .storage import Store
from .telegram import TelegramNotifier
from .watcher import Watcher

log = logging.getLogger(__name__)


class Runtime:
    def __init__(self, config: Config, api: HstoraClient, store: Store, notifier: TelegramNotifier, watcher: Watcher):
        self.config, self.api, self.store = config, api, store
        self.notifier, self.watcher = notifier, watcher
        self.started_at = time.time()
        self.last_check: float | None = None
        self.next_check: float | None = time.time()
        self.last_alerts = 0
        self.last_error = ""
        self.check_lock = threading.Lock()
        self.stop = threading.Event()

    def check(self) -> dict:
        if not self.check_lock.acquire(blocking=False):
            return {"running": True, "message": "A check is already running"}
        try:
            self.last_error = ""
            alerts = self.watcher.check()
            self.last_alerts = alerts
            self.last_check = time.time()
            self.next_check = self.last_check + self.config.interval
            self.store.add_activity("check", f"Catalog check completed — {alerts} alert(s)")
            return {"running": False, "alerts": alerts}
        except Exception as exc:
            log.exception("Dashboard check failed")
            self.last_error = str(exc)
            self.last_check = time.time()
            self.next_check = self.last_check + self.config.interval
            self.store.add_activity("error", f"Check failed: {exc}")
            raise
        finally:
            self.check_lock.release()

    def scheduler(self) -> None:
        while not self.stop.is_set():
            if self.next_check is None or time.time() >= self.next_check:
                try:
                    self.check()
                except Exception:
                    pass
            self.stop.wait(1)


def handler(runtime: Runtime):
    class Handler(BaseHTTPRequestHandler):
        server_version = "HStoraWatcher/0.2"

        def log_message(self, fmt, *args):
            log.info("dashboard %s", fmt % args)

        def authenticated(self) -> bool:
            header = self.headers.get("Authorization", "")
            expected = base64.b64encode(f"{runtime.config.dashboard_username}:{runtime.config.dashboard_password}".encode()).decode()
            return header.startswith("Basic ") and hmac.compare_digest(header[6:], expected)

        def extension_authenticated(self) -> bool:
            supplied = self.headers.get("X-Extension-Secret", "")
            return bool(runtime.config.extension_secret) and hmac.compare_digest(supplied, runtime.config.extension_secret)

        def require_auth(self) -> bool:
            if self.authenticated():
                return True
            self.send_response(HTTPStatus.UNAUTHORIZED)
            self.send_header("WWW-Authenticate", 'Basic realm="HStora Watcher", charset="UTF-8"')
            self.send_header("Content-Length", "0")
            self.end_headers()
            return False

        def json_response(self, payload, status=200):
            body = json.dumps(payload, default=str).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def read_json(self):
            length = int(self.headers.get("Content-Length", "0"))
            if length > 16_384:
                raise ValueError("Request is too large")
            return json.loads(self.rfile.read(length) or b"{}")

        def same_origin(self) -> bool:
            origin = self.headers.get("Origin")
            if not origin:
                return True
            return urllib.parse.urlparse(origin).netloc == self.headers.get("Host", "")

        def do_GET(self):
            if not self.require_auth(): return
            parsed = urllib.parse.urlparse(self.path)
            try:
                if parsed.path == "/":
                    body = files("hstora_watcher.web").joinpath("index.html").read_bytes()
                    self.send_response(200)
                    self.send_header("Content-Type", "text/html; charset=utf-8")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Content-Length", str(len(body)))
                    self.end_headers(); self.wfile.write(body)
                elif parsed.path.startswith("/assets/"):
                    name = parsed.path.removeprefix("/assets/")
                    if name not in {"app.js", "style.css"}: return self.send_error(404)
                    body = files("hstora_watcher.web").joinpath(name).read_bytes()
                    mime = "text/css" if name.endswith(".css") else "text/javascript"
                    self.send_response(200); self.send_header("Content-Type", mime + "; charset=utf-8")
                    self.send_header("Content-Length", str(len(body))); self.end_headers(); self.wfile.write(body)
                elif parsed.path == "/api/status":
                    self.json_response({"ok": True, "uptime": int(time.time()-runtime.started_at), "lastCheck": runtime.last_check, "nextCheck": runtime.next_check, "lastAlerts": runtime.last_alerts, "lastError": runtime.last_error, "checking": runtime.check_lock.locked(), "interval": runtime.config.interval, **runtime.store.counts()})
                elif parsed.path == "/api/watches":
                    offer_map = {row["product_id"]: dict(row) for row in runtime.store.z2u_offers()}
                    products = [dict(row) for row in runtime.store.products()]
                    for p in products:
                        state = runtime.store.state(p["product_id"])
                        p["state"] = dict(state) if state else None
                        p["z2u"] = offer_map.get(p["product_id"])
                    self.json_response({"products": products, "keywords": [dict(r) for r in runtime.store.keywords()]})
                elif parsed.path == "/api/activity":
                    self.json_response({"items": [dict(r) for r in runtime.store.activity()]})
                elif parsed.path == "/api/search":
                    params = urllib.parse.parse_qs(parsed.query)
                    query = params.get("q", [""])[0].strip()
                    order = params.get("sort", ["price_asc"])[0]
                    if not query: raise ValueError("Enter at least one keyword")
                    if order not in SORT_OPTIONS: raise ValueError("Invalid sort option")
                    matches = sort_products([p for p in runtime.api.catalog() if runtime.watcher.matches(p.name, query)], order)
                    offer_map = {row["product_id"]: dict(row) for row in runtime.store.z2u_offers()}
                    self.json_response({"items": [{**asdict(p), "price": str(p.price), "z2u": offer_map.get(p.id)} for p in matches[:100]], "total": len(matches), "sort": order})
                elif parsed.path.startswith("/api/products/") and parsed.path.endswith("/listing"):
                    parts = parsed.path.strip("/").split("/")
                    product_id = int(parts[2])
                    profit = urllib.parse.parse_qs(parsed.query).get("profit", [""])[0]
                    product = runtime.api.product(product_id)
                    listing = prepare_listing(product, profit, runtime.config.listing_blocked_terms)
                    runtime.store.add_product(product_id)
                    runtime.store.save_state(product)
                    runtime.store.save_z2u_offer(product_id, "prepared", listed_price=listing["price"])
                    self.json_response(listing)
                elif parsed.path == "/api/z2u/offers":
                    self.json_response({"items": [dict(r) for r in runtime.store.z2u_offers()]})
                elif parsed.path == "/api/seller-products":
                    params = urllib.parse.parse_qs(parsed.query)
                    seller_query = params.get("seller", [""])[0].strip()
                    order = params.get("sort", ["price_asc"])[0]
                    if not seller_query: raise ValueError("Enter a seller name, slug, or HStora store URL")
                    if order not in SORT_OPTIONS: raise ValueError("Invalid sort option")
                    seller_name, products = runtime.api.seller_catalog(seller_query)
                    products = sort_products(products, order)
                    offer_map = {row["product_id"]: dict(row) for row in runtime.store.z2u_offers()}
                    self.json_response({"seller": seller_name, "items": [{**asdict(p), "price": str(p.price), "z2u": offer_map.get(p.id)} for p in products], "total": len(products)})
                else: self.send_error(404)
            except ValueError as exc: self.json_response({"error": str(exc)}, 400)
            except Exception as exc:
                log.exception("GET %s failed", parsed.path)
                self.json_response({"error": str(exc)}, 500)

        def do_POST(self):
            is_extension = self.path.startswith("/api/z2u/") and self.extension_authenticated()
            if not is_extension and not self.require_auth(): return
            if not is_extension and not self.same_origin(): return self.json_response({"error": "Cross-origin request rejected"}, 403)
            try:
                data = self.read_json()
                if self.path == "/api/products":
                    product_id = int(data.get("productId", 0)); threshold = data.get("threshold")
                    if product_id <= 0: raise ValueError("Enter a valid product ID")
                    if threshold not in (None, ""): threshold = max(0, int(threshold))
                    else: threshold = None
                    product = runtime.api.product(product_id)
                    runtime.store.add_product(product_id, threshold); runtime.store.save_state(product)
                    runtime.store.add_activity("watch", f"Now watching #{product.id}: {product.name}")
                    self.json_response({"ok": True, "product": {**asdict(product), "price": str(product.price)}})
                elif self.path == "/api/keywords":
                    words = " ".join(str(data.get("keywords", "")).split())
                    if not words: raise ValueError("Enter at least one keyword")
                    runtime.store.add_keyword(words); runtime.store.add_activity("watch", f"Now watching keywords: {words}")
                    self.json_response({"ok": True})
                elif self.path == "/api/check":
                    if runtime.check_lock.locked(): return self.json_response({"error": "A check is already running"}, 409)
                    threading.Thread(target=runtime.check, daemon=True).start()
                    self.json_response({"ok": True, "message": "Check started"}, 202)
                elif self.path == "/api/telegram/test":
                    runtime.notifier.send("✅ HStora Watcher dashboard is connected.")
                    runtime.store.add_activity("telegram", "Telegram test notification sent")
                    self.json_response({"ok": True})
                elif self.path == "/api/z2u/offers":
                    product_id = int(data.get("productId", 0))
                    if product_id <= 0: raise ValueError("Invalid product ID")
                    if not runtime.store.state(product_id):
                        product = runtime.api.product(product_id)
                        runtime.store.add_product(product_id)
                        runtime.store.save_state(product)
                    offer_id = str(data.get("offerId", "")).strip() or None
                    manage_url = str(data.get("manageUrl", "")).strip() or None
                    if manage_url:
                        parsed_url = urllib.parse.urlparse(manage_url)
                        if parsed_url.scheme != "https" or parsed_url.netloc != "www.z2u.com" or not parsed_url.path.startswith("/sell/manage"):
                            raise ValueError("Management URL must be an HTTPS Z2U manage-listing URL")
                    status = "published" if offer_id else str(data.get("status", "submitted"))
                    runtime.store.save_z2u_offer(product_id, status, offer_id=offer_id, listed_price=data.get("listedPrice"), error=data.get("error"), manage_url=manage_url)
                    current = runtime.store.state(product_id)
                    if offer_id and manage_url and current and current["stock"] == 0:
                        runtime.store.queue_z2u_action(product_id, "deactivate")
                    runtime.store.add_activity("z2u", f"Z2U product #{product_id}: {status}" + (f" — offer {offer_id}" if offer_id else ""))
                    self.json_response({"ok": True, "status": status})
                elif self.path == "/api/z2u/actions/next":
                    self.json_response({"action": runtime.store.claim_z2u_action()})
                elif self.path.startswith("/api/z2u/actions/"):
                    action_id = int(self.path.rsplit("/", 1)[1])
                    success = bool(data.get("success"))
                    runtime.store.finish_z2u_action(action_id, success, str(data.get("error", ""))[:1000] or None)
                    runtime.store.add_activity("z2u", f"Z2U stock action #{action_id} " + ("succeeded" if success else "failed"))
                    self.json_response({"ok": True})
                else: self.send_error(404)
            except (ValueError, TypeError) as exc: self.json_response({"error": str(exc)}, 400)
            except Exception as exc:
                log.exception("POST %s failed", self.path); self.json_response({"error": str(exc)}, 500)

        def do_DELETE(self):
            if not self.require_auth(): return
            if not self.same_origin(): return self.json_response({"error": "Cross-origin request rejected"}, 403)
            try:
                parts = self.path.strip("/").split("/")
                if len(parts) != 3 or parts[0] != "api": return self.send_error(404)
                item_id = int(parts[2])
                if parts[1] == "products": runtime.store.remove_product(item_id)
                elif parts[1] == "keywords": runtime.store.remove_keyword(item_id)
                else: return self.send_error(404)
                runtime.store.add_activity("watch", f"Removed {parts[1][:-1]} watch #{item_id}")
                self.json_response({"ok": True})
            except Exception as exc: self.json_response({"error": str(exc)}, 400)
    return Handler


def serve(config: Config, api: HstoraClient, store: Store, notifier: TelegramNotifier, watcher: Watcher) -> None:
    runtime = Runtime(config, api, store, notifier, watcher)
    scheduler = threading.Thread(target=runtime.scheduler, name="watcher-scheduler", daemon=True)
    scheduler.start()
    server = ThreadingHTTPServer((config.dashboard_host, config.dashboard_port), handler(runtime))
    log.info("Dashboard listening on http://%s:%s", config.dashboard_host, config.dashboard_port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Dashboard stopping")
    finally:
        runtime.stop.set(); server.server_close()
