import os
import re
import sys
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL")

STORES = [
    {
        "name": "Nintendo Store",
        "url": "https://www.nintendo.com/us/store/products/nintendo-switch-2-the-legend-of-zelda-40th-anniversary-edition-121642/",
        "type": "nintendo",
    },
    {
        "name": "Best Buy",
        "url": "https://www.bestbuy.com/product/switch-2-the-legend-of-zelda-40th-anniversary-edition/J7GSL57HTY",
        "type": "bestbuy",
    },
    {
        "name": "Target",
        "url": "https://www.target.com/p/nintendo-8482-switch-2-the-legend-of-zelda-40th-anniversary-edition-console-system/-/A-1013322047",
        "type": "target",
    },
    {
        "name": "Amazon",
        "url": "https://www.amazon.com/dp/B0HJ6F8L6V",
        "type": "amazon",
    },
]

BLOCK_INDICATORS = [
    "access denied",
    "robot check",
    "enter the characters you see below",
    "pardon our interruption",
    "sorry, we just need to make sure you're not a robot",
]


def send_notification(store_name: str, url: str) -> None:
    payload = {
        "content": f"@everyone **Zelda 40th Switch 2** is in stock at **{store_name}**!\n{url}",
        "embeds": [
            {
                "title": f"IN STOCK: {store_name}",
                "description": f"[Click here to open the {store_name} product page]({url})",
                "url": url,
                "color": 5763719,
            }
        ],
    }
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    response.raise_for_status()


def is_in_stock(page, store_type: str) -> bool:
    if store_type == "nintendo":
        btn = page.locator("button:visible").filter(
            has_text=re.compile(r"^(add to cart|pre-order)$", re.I)
        )
        return btn.count() > 0 and btn.first.is_enabled()

    if store_type == "bestbuy":
        btn = page.locator(
            'button[data-button-state="ADD_TO_CART"]:visible, '
            'button[data-button-state="PRE_ORDER"]:visible, '
            '.fulfillment-add-to-cart-button button:visible'
        ).filter(has_text=re.compile(r"(add to cart|pre-order)", re.I))
        return btn.count() > 0 and btn.first.is_enabled()

    if store_type == "target":
        btn = page.locator(
            'button[data-test="shippingButton"]:visible, '
            'button[data-test="orderPickupButton"]:visible, '
            'button[data-test="deliveryButton"]:visible'
        )
        return btn.count() > 0 and btn.first.is_enabled()

    if store_type == "amazon":
        btn = page.locator(
            "#add-to-cart-button:visible, #buy-now-button:visible"
        )
        return btn.count() > 0 and btn.first.is_enabled()

    return False


def check_all_stores() -> None:
    if not DISCORD_WEBHOOK_URL:
        print("Missing DISCORD_WEBHOOK_URL environment variable.")
        sys.exit(1)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            args=[
                "--disable-blink-features=AutomationControlled",
                "--disable-http2",
            ],
        )
        context = browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
        )
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        for store in STORES:
            name = store["name"]
            url = store["url"]
            store_type = store["type"]
            page = None

            try:
                page = context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=30000)
                page.wait_for_timeout(4000)

                status = response.status if response else "No response"
                title = page.title().lower()
                body_snippet = page.locator("body").inner_text()[:1500].lower()

                if any(flag in title or flag in body_snippet for flag in BLOCK_INDICATORS):
                    print(f"[{name}] BLOCKED / CAPTCHA detected (HTTP {status}).")
                    continue

                if is_in_stock(page, store_type):
                    print(f"[{name}] IN STOCK! Sending Discord alert...")
                    send_notification(name, url)
                else:
                    print(f"[{name}] Out of stock (HTTP {status}).")

            except PlaywrightTimeout:
                print(f"[{name}] Timed out loading page.")
            except Exception as err:
                print(f"[{name}] Error: {err}")
            finally:
                if page:
                    page.close()

        browser.close()


if __name__ == "__main__":
    check_all_stores()
