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
  seller TEXT NOT NULL DEFAULT '',
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
CREATE TABLE IF NOT EXISTS z2u_offers (
  product_id INTEGER PRIMARY KEY,
  offer_id TEXT,
  manage_url TEXT,
  status TEXT NOT NULL DEFAULT 'prepared',
  listed_price TEXT,
  error TEXT,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS z2u_actions (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  product_id INTEGER NOT NULL,
  action TEXT NOT NULL CHECK(action IN ('deactivate','relist')),
  status TEXT NOT NULL DEFAULT 'pending',
  attempts INTEGER NOT NULL DEFAULT 0,
  error TEXT,
  created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_z2u_actions_status ON z2u_actions(status, id);
"""


class Store:
    def __init__(self, path: str):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        with self.connect() as db:
            db.executescript(SCHEMA)
            columns = {row[1] for row in db.execute("PRAGMA table_info(z2u_offers)")}
            if "manage_url" not in columns:
                db.execute("ALTER TABLE z2u_offers ADD COLUMN manage_url TEXT")
            state_columns = {row[1] for row in db.execute("PRAGMA table_info(product_state)")}
            if "seller" not in state_columns:
                db.execute("ALTER TABLE product_state ADD COLUMN seller TEXT NOT NULL DEFAULT ''")
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
            db.execute("""INSERT INTO product_state(product_id,name,price,currency,stock,url,seller) VALUES(?,?,?,?,?,?,?)
                ON CONFLICT(product_id) DO UPDATE SET name=excluded.name,price=excluded.price,currency=excluded.currency,stock=excluded.stock,url=excluded.url,seller=CASE WHEN excluded.seller<>'' THEN excluded.seller ELSE product_state.seller END,updated_at=CURRENT_TIMESTAMP""",
                (product.id, product.name, str(product.price), product.currency, product.stock, product.url, product.seller))

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

    def save_z2u_offer(self, product_id: int, status: str, offer_id: str | None = None, listed_price: str | None = None, error: str | None = None, manage_url: str | None = None) -> None:
        allowed = {"prepared", "filling", "submitted", "published", "failed", "inactive", "automation_retrying", "automation_failed"}
        if status not in allowed:
            raise ValueError("Invalid Z2U offer status")
        with self.connect() as db:
            db.execute("""INSERT INTO z2u_offers(product_id,offer_id,manage_url,status,listed_price,error) VALUES(?,?,?,?,?,?)
                ON CONFLICT(product_id) DO UPDATE SET
                  offer_id=COALESCE(excluded.offer_id,z2u_offers.offer_id), status=excluded.status,
                  manage_url=COALESCE(excluded.manage_url,z2u_offers.manage_url),
                  listed_price=COALESCE(excluded.listed_price,z2u_offers.listed_price), error=excluded.error,
                  updated_at=CURRENT_TIMESTAMP""", (product_id, offer_id, manage_url, status, listed_price, error))

    def z2u_offer(self, product_id: int):
        with self.connect() as db:
            return db.execute("SELECT * FROM z2u_offers WHERE product_id=?", (product_id,)).fetchone()

    def z2u_offers(self):
        with self.connect() as db:
            return db.execute("SELECT * FROM z2u_offers ORDER BY updated_at DESC").fetchall()

    def queue_z2u_action(self, product_id: int, action: str) -> bool:
        if action not in {"deactivate", "relist"}:
            raise ValueError("Invalid Z2U action")
        with self.connect() as db:
            offer = db.execute("SELECT offer_id,manage_url FROM z2u_offers WHERE product_id=?", (product_id,)).fetchone()
            if not offer or not offer["offer_id"] or not offer["manage_url"]:
                return False
            duplicate = db.execute("SELECT 1 FROM z2u_actions WHERE product_id=? AND action=? AND status IN ('pending','running')", (product_id, action)).fetchone()
            if duplicate:
                return True
            db.execute("UPDATE z2u_actions SET status='superseded',updated_at=CURRENT_TIMESTAMP WHERE product_id=? AND status='pending'", (product_id,))
            db.execute("INSERT INTO z2u_actions(product_id,action) VALUES(?,?)", (product_id, action))
            return True

    def claim_z2u_action(self):
        with self.connect() as db:
            db.execute("UPDATE z2u_actions SET status='pending' WHERE status='running' AND updated_at < datetime('now','-5 minutes')")
            row = db.execute("""SELECT a.*,o.offer_id,o.manage_url FROM z2u_actions a
                JOIN z2u_offers o ON o.product_id=a.product_id
                WHERE a.status='pending' ORDER BY a.id LIMIT 1""").fetchone()
            if not row:
                return None
            db.execute("UPDATE z2u_actions SET status='running',attempts=attempts+1,updated_at=CURRENT_TIMESTAMP WHERE id=?", (row["id"],))
            return dict(row)

    def finish_z2u_action(self, action_id: int, success: bool, error: str | None = None) -> None:
        with self.connect() as db:
            row = db.execute("SELECT product_id,action,attempts FROM z2u_actions WHERE id=?", (action_id,)).fetchone()
            if not row:
                raise ValueError("Unknown Z2U action")
            status = "succeeded" if success else ("pending" if row["attempts"] < 3 else "failed")
            db.execute("UPDATE z2u_actions SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE id=?", (status, error, action_id))
            offer_status = "inactive" if row["action"] == "deactivate" and success else "published" if success else "automation_retrying" if status == "pending" else "automation_failed"
            db.execute("UPDATE z2u_offers SET status=?,error=?,updated_at=CURRENT_TIMESTAMP WHERE product_id=?", (offer_status, error, row["product_id"]))
