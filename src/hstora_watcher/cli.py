from __future__ import annotations

import argparse
import logging
import signal
import sys
import time

from .api import HstoraClient, SORT_OPTIONS, sort_products
from .config import Config
from .storage import Store
from .telegram import TelegramNotifier
from .watcher import Watcher


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="hstora-watcher", description="Watch HStora products and keyword searches")
    sub = p.add_subparsers(dest="command", required=True)
    sub.add_parser("check", help="Run one check now")
    sub.add_parser("run", help="Run checks continuously")
    sub.add_parser("dashboard", help="Run the web dashboard and background checks")
    sub.add_parser("test-telegram", help="Send a test notification")
    search = sub.add_parser("search", help="Search the full current catalog, cheapest first")
    search.add_argument("keywords", nargs="+")
    search.add_argument("--limit", type=int, default=30)
    search.add_argument("--sort", choices=sorted(SORT_OPTIONS), default="price_asc")
    watch = sub.add_parser("watch-product", help="Track a product ID")
    watch.add_argument("product_id", type=int)
    watch.add_argument("--threshold", type=int)
    unwatch = sub.add_parser("unwatch-product", help="Stop tracking a product")
    unwatch.add_argument("product_id", type=int)
    keyword = sub.add_parser("watch-keyword", help="Track products containing all supplied words")
    keyword.add_argument("keywords", nargs="+")
    remove_keyword = sub.add_parser("unwatch-keyword", help="Remove a keyword watch by ID")
    remove_keyword.add_argument("watch_id", type=int)
    sub.add_parser("list", help="List product and keyword watches")
    return p


def build(config: Config):
    api = HstoraClient(config.api_key, config.api_secret, config.base_url, config.timeout)
    store = Store(config.db_path)
    notifier = TelegramNotifier(config.telegram_token, config.telegram_chat_id, config.timeout)
    return api, store, notifier, Watcher(api, store, notifier, config.low_stock_threshold)


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        config = Config.from_env(
            require_telegram=args.command in {"check", "run", "dashboard", "test-telegram"},
            require_dashboard=args.command == "dashboard",
        )
        api, store, notifier, watcher = build(config)
        if args.command == "watch-product":
            product = api.product(args.product_id)
            store.add_product(args.product_id, args.threshold)
            store.save_state(product)
            print(f"Watching #{product.id}: {product.name} ({product.price} {product.currency}, stock {product.stock})")
        elif args.command == "unwatch-product":
            store.remove_product(args.product_id)
            print(f"Stopped watching product #{args.product_id}")
        elif args.command == "watch-keyword":
            words = " ".join(args.keywords)
            store.add_keyword(words)
            print(f"Watching keyword query: {words}")
        elif args.command == "unwatch-keyword":
            store.remove_keyword(args.watch_id)
            print(f"Removed keyword watch #{args.watch_id}")
        elif args.command == "list":
            print("Products:")
            for row in store.products():
                print(f"  #{row['product_id']}  threshold={row['low_stock_threshold'] if row['low_stock_threshold'] is not None else 'default'}")
            print("Keywords:")
            for row in store.keywords():
                print(f"  #{row['id']}  {row['keywords']}")
        elif args.command == "search":
            words = " ".join(args.keywords)
            matches = sort_products([p for p in api.catalog() if watcher.matches(p.name, words)], args.sort)
            for product in matches[:max(0, args.limit)]:
                print(f"{product.price:>10} {product.currency} | stock {product.stock:>6} | #{product.id} | {product.name}\n  {product.url}")
            print(f"{len(matches)} matching product(s)")
        elif args.command == "test-telegram":
            notifier.send("✅ HStora Watcher is connected.")
            print("Test notification sent")
        elif args.command == "check":
            print(f"Check complete: {watcher.check()} alert(s) sent")
        elif args.command == "run":
            running = True
            def stop(*_):
                nonlocal running
                running = False
            signal.signal(signal.SIGINT, stop)
            signal.signal(signal.SIGTERM, stop)
            logging.info("Watcher started; interval=%ss", config.interval)
            while running:
                started = time.monotonic()
                try:
                    logging.info("Check complete: %s alert(s)", watcher.check())
                except Exception:
                    logging.exception("Check cycle failed")
                remaining = max(0, config.interval - (time.monotonic() - started))
                end = time.monotonic() + remaining
                while running and time.monotonic() < end:
                    time.sleep(min(1, end - time.monotonic()))
        elif args.command == "dashboard":
            from .dashboard import serve
            serve(config, api, store, notifier, watcher)
        return 0
    except (ValueError, RuntimeError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
