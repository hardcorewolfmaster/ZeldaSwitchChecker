import os
import re
import sys
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
EVENT_NAME = os.environ.get("EVENT_NAME", "").strip()

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


def send_stock_alert(store_name: str, url: str) -> None:
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


def send_manual_summary(status_lines: list[str]) -> None:
    payload = {
        "embeds": [
            {
                "title": "Manual Stock Check Complete",
                "description": "\n".join(status_lines),
                "color": 3447003,
            }
        ],
    }
    response = requests.post(DISCORD_WEBHOOK_URL, json=payload, timeout=10)
    response.raise_for_status()


def is_in_stock(page, store_type: str) -> bool:
    try:
        if store_type == "nintendo":
            btn = page.locator("button:visible").filter(
                has_text=re.compile(r"^(add to cart|pre-order)$", re.I)
            )
            return btn.count() > 0 and btn.first.is_enabled(timeout=3000)

        if store_type == "bestbuy":
            btn = page.locator(
                'button[data-button-state="ADD_TO_CART"]:visible, '
                'button[data-button-state="PRE_ORDER"]:visible, '
                '.fulfillment-add-to-cart-button button:visible'
            ).filter(has_text=re.compile(r"(add to cart|pre-order)", re.I))
            return btn.count() > 0 and btn.first.is_enabled(timeout=3000)

        if store_type == "target":
            btn = page.locator(
                'button[data-test="shippingButton"]:visible, '
                'button[data-test="orderPickupButton"]:visible, '
                'button[data-test="deliveryButton"]:visible'
            )
            return btn.count() > 0 and btn.first.is_enabled(timeout=3000)

        if store_type == "amazon":
            btn = page.locator(
                "#add-to-cart-button:visible, #buy-now-button:visible"
            )
            return btn.count() > 0 and btn.first.is_enabled(timeout=3000)
    except Exception:
        return False

    return False


def get_safe_html(page) -> str:
    try:
        return page.content()[:5000].lower()
    except Exception:
        return ""


def check_all_stores() -> None:
    if not DISCORD_WEBHOOK_URL:
        print("Missing DISCORD_WEBHOOK_URL environment variable.", flush=True)
        sys.exit(1)

    status_report = []

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
        context.set_default_timeout(5000)
        context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        for store in STORES:
            name = store["name"]
            url = store["url"]
            store_type = store["type"]
            print(f"[{name}] Checking...", flush=True)
            page = None
            timed_out = False

            try:
                page = context.new_page()
                response = page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(4000)

                status = response.status if response else "No response"
                html_snippet = get_safe_html(page)

                if any(flag in html_snippet for flag in BLOCK_INDICATORS):
                    print(f"[{name}] BLOCKED / CAPTCHA detected (HTTP {status}).", flush=True)
                    status_report.append(f"• **{name}**: Blocked / CAPTCHA (HTTP {status})")
                    continue

                if is_in_stock(page, store_type):
                    print(f"[{name}] IN STOCK! Sending Discord alert...", flush=True)
                    send_stock_alert(name, url)
                    status_report.append(f"• **{name}**: **IN STOCK!**")
                else:
                    print(f"[{name}] Out of stock (HTTP {status}).", flush=True)
                    status_report.append(f"• **{name}**: Out of stock")

            except PlaywrightTimeout:
                timed_out = True
                if page and is_in_stock(page, store_type):
                    print(f"[{name}] IN STOCK! Sending Discord alert...", flush=True)
                    send_stock_alert(name, url)
                    status_report.append(f"• **{name}**: **IN STOCK!**")
                else:
                    print(f"[{name}] Timed out loading page.", flush=True)
                    status_report.append(f"• **{name}**: Timed out")
            except Exception as err:
                print(f"[{name}] Error: {err}", flush=True)
                status_report.append(f"• **{name}**: Error")
            finally:
                if page and not timed_out:
                    try:
                        page.close()
                    except Exception:
                        pass

        if EVENT_NAME == "workflow_dispatch":
            print("Manual run detected. Sending summary to Discord...", flush=True)
            send_manual_summary(status_report)

        print("All checks finished.", flush=True)
        sys.stdout.flush()
        os._exit(0)


if __name__ == "__main__":
    check_all_stores()
