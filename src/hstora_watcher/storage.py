from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

from .api import Product


SCHEMA = """
CREATE TABLE IF NOT EXISTS product_watches (
  product_id INTEGER PRIMARY KEY,
  low_stock_threshold INTEGER,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS keyword_watches (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  keywords TEXT NOT NULL UNIQUE COLLATE NOCASE,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS product_state (
  product_id INTEGER PRIMARY KEY,
  name TEXT NOT NULL,
  price TEXT NOT NULL,
  currency TEXT NOT NULL,
  stock INTEGER NOT NULL,
  url TEXT NOT NULL,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS keyword_matches (
  watch_id INTEGER NOT NULL,
  product_id INTEGER NOT NULL,
  price TEXT NOT NULL,
  PRIMARY KEY (watch_id, product_id),
  FOREIGN KEY (watch_id) REFERENCES keyword_watches(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS activity (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  kind TEXT NOT NULL,
  message TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_activity_created_at ON activity(created_at DESC);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript(SCHEMA)
            db.execute("PRAGMA optimize")

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        db = sqlite3.connect(self.path)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            yield db
            db.commit()
        finally:
            db.close()

    def add_product(self, product_id: int, threshold: int | None = None) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO product_watches(product_id, low_stock_threshold) VALUES (?, ?) ON CONFLICT(product_id) DO UPDATE SET low_stock_threshold=excluded.low_stock_threshold", (product_id, threshold))

    def remove_product(self, product_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM product_watches WHERE product_id=?", (product_id,))

    def products(self):
        with self.connect() as db:
            return db.execute("SELECT * FROM product_watches ORDER BY product_id").fetchall()

    def add_keyword(self, keywords: str) -> None:
        normalized = " ".join(keywords.lower().split())
        with self.connect() as db:
            db.execute("INSERT OR IGNORE INTO keyword_watches(keywords) VALUES (?)", (normalized,))

    def remove_keyword(self, watch_id: int) -> None:
        with self.connect() as db:
            db.execute("DELETE FROM keyword_watches WHERE id=?", (watch_id,))

    def keywords(self):
        with self.connect() as db:
            return db.execute("SELECT * FROM keyword_watches ORDER BY id").fetchall()

    def state(self, product_id: int):
        with self.connect() as db:
            return db.execute("SELECT * FROM product_state WHERE product_id=?", (product_id,)).fetchone()

    def save_state(self, product: Product) -> None:
        with self.connect() as db:
            db.execute("""INSERT INTO product_state(product_id,name,price,currency,stock,url) VALUES(?,?,?,?,?,?)
                ON CONFLICT(product_id) DO UPDATE SET name=excluded.name,price=excluded.price,currency=excluded.currency,stock=excluded.stock,url=excluded.url,updated_at=CURRENT_TIMESTAMP""",
                (product.id, product.name, str(product.price), product.currency, product.stock, product.url))

    def keyword_match(self, watch_id: int, product_id: int):
        with self.connect() as db:
            return db.execute("SELECT * FROM keyword_matches WHERE watch_id=? AND product_id=?", (watch_id, product_id)).fetchone()

    def save_keyword_match(self, watch_id: int, product: Product) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO keyword_matches(watch_id,product_id,price) VALUES(?,?,?) ON CONFLICT(watch_id,product_id) DO UPDATE SET price=excluded.price", (watch_id, product.id, str(product.price)))

    def add_activity(self, kind: str, message: str) -> None:
        with self.connect() as db:
            db.execute("INSERT INTO activity(kind,message) VALUES(?,?)", (kind, message[:1000]))
            db.execute("DELETE FROM activity WHERE id NOT IN (SELECT id FROM activity ORDER BY id DESC LIMIT 200)")

    def activity(self, limit: int = 30):
        with self.connect() as db:
            return db.execute("SELECT * FROM activity ORDER BY id DESC LIMIT ?", (limit,)).fetchall()

    def counts(self) -> dict[str, int]:
        with self.connect() as db:
            products = db.execute("SELECT COUNT(*) FROM product_watches").fetchone()[0]
            keywords = db.execute("SELECT COUNT(*) FROM keyword_watches").fetchone()[0]
            tracked = db.execute("SELECT COUNT(*) FROM product_state").fetchone()[0]
        return {"products": products, "keywords": keywords, "tracked": tracked}
