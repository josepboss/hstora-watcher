from __future__ import annotations

import logging
from decimal import Decimal

from .api import HstoraClient, Product
from .storage import Store
from .telegram import TelegramNotifier

log = logging.getLogger(__name__)


class Watcher:
    def __init__(self, api: HstoraClient, store: Store, notifier: TelegramNotifier, default_threshold: int):
        self.api = api
        self.store = store
        self.notifier = notifier
        self.default_threshold = default_threshold

    def _send(self, heading: str, product: Product, details: list[str]) -> None:
        message = [heading, product.name, f"Price: {product.price} {product.currency}", f"Stock: {product.stock}", *details]
        if product.url:
            message.append(product.url)
        rendered = "\n".join(message)
        self.notifier.send(rendered)
        self.store.add_activity("alert", f"{heading}: {product.name}")

    def check_products(self) -> int:
        alerts = 0
        for watch in self.store.products():
            try:
                product = self.api.product(watch["product_id"])
                old = self.store.state(product.id)
                threshold = watch["low_stock_threshold"] if watch["low_stock_threshold"] is not None else self.default_threshold
                if old:
                    details = []
                    if old["name"] != product.name:
                        details.append(f"Title changed:\n{old['name']}\n→ {product.name}")
                    if Decimal(old["price"]) != product.price:
                        details.append(f"Price changed: {old['price']} → {product.price} {product.currency}")
                    if old["stock"] == 0 and product.stock > 0:
                        details.append(f"Restocked: 0 → {product.stock}")
                        queued = self.store.queue_z2u_action(product.id, "relist")
                        details.append("Z2U activation queued" if queued else "Z2U activation not queued: add offer ID and manage URL")
                    elif old["stock"] > threshold >= product.stock > 0:
                        details.append(f"Low stock: {old['stock']} → {product.stock} (threshold {threshold})")
                    elif old["stock"] > 0 and product.stock == 0:
                        details.append("Out of stock")
                        queued = self.store.queue_z2u_action(product.id, "deactivate")
                        details.append("Z2U deactivation queued" if queued else "Z2U deactivation not queued: add offer ID and manage URL")
                    if details:
                        self._send("🔔 HStora product update", product, details)
                        alerts += 1
                self.store.save_state(product)
            except Exception:
                log.exception("Failed checking product %s", watch["product_id"])
        return alerts

    @staticmethod
    def matches(name: str, keywords: str) -> bool:
        haystack = name.casefold()
        return all(word.casefold() in haystack for word in keywords.split())

    def check_keywords(self) -> int:
        watches = self.store.keywords()
        if not watches:
            return 0
        alerts = 0
        products = list(self.api.catalog())
        for watch in watches:
            matches = sorted((p for p in products if self.matches(p.name, watch["keywords"])), key=lambda p: (p.price, p.id))
            existing_prices = [Decimal(row["price"]) for p in matches if (row := self.store.keyword_match(watch["id"], p.id))]
            old_floor = min(existing_prices) if existing_prices else None
            for position, product in enumerate(matches):
                old = self.store.keyword_match(watch["id"], product.id)
                is_new = old is None
                # On the initial baseline, notify only for the cheapest result.
                # On later scans, compare new listings with the pre-scan floor.
                is_cheaper_new = is_new and (
                    (old_floor is None and position == 0)
                    or (old_floor is not None and product.price < old_floor)
                )
                price_dropped = old is not None and product.price < Decimal(old["price"])
                if is_cheaper_new:
                    self._send(f"🆕 New lowest match for: {watch['keywords']}", product, [])
                    alerts += 1
                elif price_dropped:
                    self._send(f"📉 Keyword match price drop: {watch['keywords']}", product, [f"Old price: {old['price']} {product.currency}"])
                    alerts += 1
                self.store.save_keyword_match(watch["id"], product)
        return alerts

    def check(self) -> int:
        return self.check_products() + self.check_keywords()
