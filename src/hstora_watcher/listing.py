from __future__ import annotations

import html
import re
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP

from .api import Product

URL_RE = re.compile(r"(?i)\b(?:https?://|www\.)\S+|\b[a-z0-9.-]+\.(?:com|net|org|io|co|store|shop)\b\S*")


def sanitize_listing_text(value: str, blocked_terms: tuple[str, ...]) -> str:
    text = html.unescape(re.sub(r"<[^>]+>", " ", value or ""))
    text = URL_RE.sub("", text)
    text = re.sub(r"(?i)\bwelcome\s+to\s+[\w][\w .&'-]{1,50}?(?=[.!?,\n]|$)", "", text)
    for term in blocked_terms:
        if term:
            text = re.sub(re.escape(term), "", text, flags=re.IGNORECASE)
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    return text.strip(" -–—|,:\n")


def prepare_listing(product: Product, profit: str, blocked_terms: tuple[str, ...]) -> dict:
    if product.currency.upper() != "USD":
        raise ValueError(f"Cannot list {product.currency} product as USD without an exchange rate")
    try:
        margin = Decimal(str(profit))
    except InvalidOperation as exc:
        raise ValueError("Profit must be a valid amount") from exc
    if margin < 0:
        raise ValueError("Profit cannot be negative")
    price = (product.price + margin).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    effective_terms = blocked_terms + ((product.seller,) if product.seller else ())
    title = sanitize_listing_text(product.name, effective_terms)
    description = sanitize_listing_text(product.description or product.name, effective_terms)
    if not title or not description:
        raise ValueError("Sanitizing removed the entire title or description")
    return {
        "productId": product.id,
        "title": title[:255],
        "description": description,
        "originalPrice": str(product.price),
        "profit": str(margin),
        "price": str(price),
        "minUnits": 2 if price < Decimal("1") else 1,
        "stock": 99999999,
        "currency": "USD",
    }
