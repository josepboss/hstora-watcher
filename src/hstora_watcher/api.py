from __future__ import annotations

import hashlib
import hmac
import json
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

    @classmethod
    def from_api(cls, item: dict[str, Any]) -> "Product":
        return cls(
            id=int(item["id"]),
            name=str(item.get("name", "")),
            price=Decimal(str(item.get("price", 0))),
            currency=str(item.get("currency", "USD")),
            stock=int(item.get("stock_available") or 0),
            url=str(item.get("product_url", "")),
            updated_at=str(item.get("updated_at", "")),
        )


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

