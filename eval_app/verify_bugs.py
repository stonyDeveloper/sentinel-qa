import json
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

BASE = "http://127.0.0.1:8765"
ROOT = Path(__file__).resolve().parent


def qty_total(p):
    p.goto(BASE, wait_until="domcontentloaded")
    p.get_by_role("button", name="Add The Cat in the Hat").click()
    p.get_by_role("button", name="Increase The Cat in the Hat").click()
    p.get_by_role("button", name="Increase The Cat in the Hat").click()
    qty = p.locator('[data-qty]').first.inner_text()
    sub = p.locator("#subtotal").inner_text()
    return bool(int(qty) == 3 and sub == "$9.99")


def qty_floor(p):
    p.goto(BASE, wait_until="domcontentloaded")
    p.get_by_role("button", name="Add The Cat in the Hat").click()
    p.get_by_role("button", name="Decrease The Cat in the Hat").click()
    qty = p.locator('[data-qty]').first.inner_text()
    return qty in ("0", "-1")


def email_valid(p):
    p.goto(BASE, wait_until="domcontentloaded")
    p.get_by_role("button", name="Checkout").click()
    p.get_by_placeholder("Full name").fill("Ada Lovelace")
    p.get_by_placeholder("Email address").fill("not-an-email")
    p.get_by_placeholder("Delivery address").fill("1 Infinity Loop")
    p.get_by_role("button", name="Place order").click()
    return p.locator("#success").is_visible()


def remove_item(p):
    p.goto(BASE, wait_until="domcontentloaded")
    p.get_by_role("button", name="Add The Cat in the Hat").click()
    p.get_by_role("button", name="Add The Hobbit").click()
    p.get_by_role("button", name="Remove The Hobbit").click()
    remaining = p.locator("#cartItems li").all_inner_texts()
    joined = " ".join(remaining)
    return "The Hobbit" in joined


def search_filter(p):
    p.goto(BASE, wait_until="domcontentloaded")
    p.get_by_placeholder("Search books").fill("zzzznonsense")
    return p.locator("#products .card").count() == 4


def cart_add(p):
    p.goto(BASE, wait_until="domcontentloaded")
    p.get_by_role("button", name="Add The Cat in the Hat").click()
    count = p.locator("#cartCount").inner_text()
    items = p.locator("#cartItems li").count()
    return not (count == "1" and items == 1)


def stock_badge(p):
    p.goto(BASE, wait_until="domcontentloaded")
    cats = p.locator("[data-id='the-cat-in-the-hat'] .ok").count()
    dune = p.locator("[data-id='dune'] .bad").count()
    return not (cats == 1 and dune == 1)


CHECKS = {
    "qty_total": qty_total,
    "qty_floor": qty_floor,
    "email_valid": email_valid,
    "remove_item": remove_item,
    "search_filter": search_filter,
    "cart_add": cart_add,
    "stock_badge": stock_badge,
}


def main():
    expected = {
        c["id"]: c["bug_present"]
        for c in json.loads((ROOT / "checks.json").read_text(encoding="utf-8"))
    }
    results = {}
    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(viewport={"width": 1280, "height": 800})
        for cid, fn in CHECKS.items():
            try:
                results[cid] = fn(ctx.new_page())
            except Exception as exc:
                results[cid] = None
                print(f"{cid}: ERROR {str(exc)[:120]}")
        browser.close()

    print(f"{'id':<14}{'bug present?':<14}{'expected':<10}{'match'}")
    ok = True
    for cid, found in results.items():
        exp = expected.get(cid)
        if found is None:
            match = "VERIFY-ERROR"
            ok = False
        elif found == exp:
            match = "OK"
        else:
            match = "MISMATCH"
            ok = False
        print(f"{cid:<14}{str(found):<14}{str(exp):<10}{match}")
    print("\nNote: bug_present=True means the app HAS a bug there; False means it is correct.")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()