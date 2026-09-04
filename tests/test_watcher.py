import tempfile
import unittest
from decimal import Decimal

from hstora_watcher.api import Product, sort_products
from hstora_watcher.listing import prepare_listing, sanitize_listing_text
from hstora_watcher.storage import Store
from hstora_watcher.watcher import Watcher


class FakeApi:
    def __init__(self, products): self.items = {p.id: p for p in products}
    def product(self, product_id): return self.items[product_id]
    def catalog(self): return iter(self.items.values())


class FakeNotifier:
    def __init__(self): self.messages = []
    def send(self, text): self.messages.append(text)


def product(id=1, name="Old account", price="2.00", stock=20):
    return Product(id, name, Decimal(price), "USD", stock, f"https://hstora.com/en/product/{id}")


class WatcherTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = Store(self.temp.name + "/test.db")
        self.notify = FakeNotifier()

    def tearDown(self): self.temp.cleanup()

    def watcher(self, items): return Watcher(FakeApi(items), self.store, self.notify, 10)

    def test_product_change_and_restock_alerts(self):
        self.store.add_product(1)
        self.store.save_state(product(stock=0))
        self.watcher([product(name="New account", price="1.50", stock=5)]).check_products()
        self.assertEqual(len(self.notify.messages), 1)
        self.assertIn("Title changed", self.notify.messages[0])
        self.assertIn("Price changed", self.notify.messages[0])
        self.assertIn("Restocked", self.notify.messages[0])

    def test_low_stock_only_alerts_when_crossing_threshold(self):
        self.store.add_product(1)
        self.store.save_state(product(stock=11))
        self.watcher([product(stock=10)]).check_products()
        self.assertIn("Low stock", self.notify.messages[0])
        self.notify.messages.clear()
        self.watcher([product(stock=9)]).check_products()
        self.assertEqual(self.notify.messages, [])

    def test_new_keyword_match_must_be_below_existing_floor(self):
        self.store.add_keyword("gmail aged")
        watcher = self.watcher([product(1, "Aged Gmail", "3"), product(2, "Gmail aged new", "5")])
        watcher.check_keywords()
        self.assertEqual(len(self.notify.messages), 1)
        self.notify.messages.clear()
        watcher.api.items[3] = product(3, "Gmail aged cheap", "2")
        watcher.check_keywords()
        self.assertEqual(len(self.notify.messages), 1)
        self.assertIn("New lowest", self.notify.messages[0])

    def test_keyword_requires_all_words_case_insensitively(self):
        self.assertTrue(Watcher.matches("Premium Aged Gmail Account", "gmail aged"))
        self.assertFalse(Watcher.matches("Premium Gmail Account", "gmail aged"))

    def test_catalog_sorting_by_price_and_stock(self):
        items = [product(1, price="4", stock=8), product(2, price="2", stock=3), product(3, price="3", stock=20)]
        self.assertEqual([p.id for p in sort_products(items, "price_asc")], [2, 3, 1])
        self.assertEqual([p.id for p in sort_products(items, "stock_desc")], [3, 1, 2])
        self.assertEqual([p.id for p in sort_products(items, "stock_asc")], [2, 1, 3])

    def test_z2u_listing_sanitizes_and_calculates_rules(self):
        item = Product(9, "HStora Twitter https://hstora.com/item", Decimal("0.40"), "USD", 8, "", description="Buy from HStore at www.hstora.com now")
        listing = prepare_listing(item, "0.20", ("HStora", "HStore"))
        self.assertEqual(listing["price"], "0.60")
        self.assertEqual(listing["minUnits"], 2)
        self.assertEqual(listing["stock"], 99999999)
        self.assertNotIn("hstora", (listing["title"] + listing["description"]).lower())

    def test_z2u_minimum_is_one_at_one_dollar(self):
        listing = prepare_listing(product(price="0.80"), "0.20", ())
        self.assertEqual(listing["minUnits"], 1)

    def test_stock_actions_require_linked_z2u_offer(self):
        self.assertFalse(self.store.queue_z2u_action(1, "deactivate"))
        self.store.save_z2u_offer(1, "published", offer_id="9281536", manage_url="https://www.z2u.com/sell/manageList?service=5&game=15142")
        self.assertTrue(self.store.queue_z2u_action(1, "deactivate"))
        action = self.store.claim_z2u_action()
        self.assertEqual(action["action"], "deactivate")
        self.assertEqual(action["offer_id"], "9281536")
        self.store.finish_z2u_action(action["id"], True)
        self.assertEqual(self.store.z2u_offer(1)["status"], "inactive")


if __name__ == "__main__": unittest.main()
