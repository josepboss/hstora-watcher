from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Iterator


class ApiError(RuntimeError):
    pass


@dataclass(frozen=True)
class Product:
    id: int
    name: str
    price: Decimal
    currency: str
    stock: int
    url: str
    updated_at: str = ""
    description: str = ""
    seller: str = ""

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Product":
        seller_data = item.get("seller") or item.get("vendor") or item.get("store") or {}
        if not isinstance(seller_data, dict):
            seller_data = {}
        seller = str(item.get("seller_name") or item.get("vendor_name") or item.get("store_name") or item.get("shop_name") or seller_data.get("name") or "")
        description = str(item.get("description") or item.get("short_description") or "")
        if not seller:
            welcome = re.search(r"(?i)\bwelcome\s+to\s+([\w][\w .&'-]{1,50}?)(?=[.!?,<\n]|$)", description)
            seller = welcome.group(1).strip() if welcome else ""
        return cls(
            id=int(item["id"]),
            name=str(item.get("name", "")),
            price=Decimal(str(item.get("price", 0))),
            currency=str(item.get("currency", "USD")),
            stock=int(item.get("stock_available") or 0),
            url=str(item.get("product_url", "")),
            updated_at=str(item.get("updated_at", "")),
            description=description,
            seller=seller,
        )


SORT_OPTIONS = {"price_asc", "price_desc", "stock_asc", "stock_desc"}


def sort_products(products: list[Product], order: str = "price_asc") -> list[Product]:
    """Sort catalog products with stable product-ID tie breaking."""
    if order not in SORT_OPTIONS:
        raise ValueError(f"Unknown sort order: {order}")
    field, direction = order.split("_", 1)
    value = (lambda p: p.price) if field == "price" else (lambda p: p.stock)
    sign = -1 if direction == "desc" else 1
    return sorted(products, key=lambda p: (sign * value(p), p.id))


class HstoraClient:
    def __init__(self, api_key: str, api_secret: str, base_url: str, timeout: int = 30):
        self.api_key = api_key
        self.api_secret = api_secret.encode()
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _get(self, path: str, params: list[tuple[str, str]] | None = None) -> dict[str, Any]:
        query = urllib.parse.urlencode(params or [])
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        canonical = "\n".join(["GET", path, query, timestamp, nonce, ""])
        signature = hmac.new(self.api_secret, canonical.encode(), hashlib.sha256).hexdigest()
        url = self.base_url + path.removeprefix("/api/v1")
        if query:
            url += "?" + query
        request = urllib.request.Request(url, headers={
            "Accept": "application/json",
            "User-Agent": "hstora-watcher/0.1",
            "X-API-Key": self.api_key,
            "X-Timestamp": timestamp,
            "X-Nonce": nonce,
            "X-Signature": signature,
        })
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                payload = json.load(response)
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise ApiError(f"HStora API returned HTTP {exc.code}: {detail[:500]}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise ApiError(f"Could not reach HStora API: {exc}") from exc
        if not payload.get("success"):
            raise ApiError(str(payload.get("message") or payload.get("error") or payload))
        return payload["data"]

    def product(self, product_id: int) -> Product:
        return Product.from_api(self._get(f"/api/v1/products/{product_id}"))

    def catalog(self, page_size: int = 100) -> Iterator[Product]:
        page = 1
        while True:
            data = self._get("/api/v1/catalog", [("page", str(page)), ("limit", str(page_size))])
            for item in data.get("items", []):
                yield Product.from_api(item)
            pagination = data.get("pagination", {})
            if page >= int(pagination.get("pages", page)):
                return
            page += 1

    def seller_catalog(self, seller: str, max_pages: int = 50) -> tuple[str, list[Product]]:
        """Discover a public seller's product IDs, then load live details via the API."""
        raw = seller.strip()
        if "/store/" in raw:
            slug = urllib.parse.urlparse(raw).path.split("/store/", 1)[1].strip("/").split("/", 1)[0]
        else:
            slug = re.sub(r"[^a-z0-9]+", "-", raw.casefold()).strip("-")
        if not slug or not re.fullmatch(r"[a-z0-9-]+", slug):
            raise ValueError("Enter a valid HStora seller name, slug, or store URL")
        site_root = self.base_url.split("/api/v1", 1)[0]
        product_ids: list[int] = []
        seller_name = raw if "/store/" not in raw else slug.replace("-", " ").title()
        for page in range(1, max_pages + 1):
            url = f"{site_root}/en/store/{slug}" + (f"?page={page}" if page > 1 else "")
            request = urllib.request.Request(url, headers={"Accept": "text/html", "User-Agent": "hstora-watcher/0.2"})
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    html_text = response.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                if exc.code == 404: raise ApiError(f"HStora seller not found: {slug}") from exc
                raise ApiError(f"HStora seller page returned HTTP {exc.code}") from exc
            heading = re.search(r"<h1[^>]*>(.*?)</h1>", html_text, re.I | re.S)
            if heading:
                seller_name = re.sub(r"<[^>]+>", "", heading.group(1)).strip()
            page_ids = [int(x) for x in re.findall(r"/en/product/(\d+)(?:[-/\"'])", html_text)]
            fresh = [pid for pid in page_ids if pid not in product_ids]
            product_ids.extend(fresh)
            has_next = bool(re.search(rf"/en/store/{re.escape(slug)}\?page={page + 1}\b", html_text))
            if not has_next or not fresh:
                break
        products = []
        for product_id in product_ids:
            product = self.product(product_id)
            products.append(Product(**{**product.__dict__, "seller": seller_name}))
        return seller_name, products
