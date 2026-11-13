import multiprocessing
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
    "you don't have permission to access",
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


def check_single_store(store: dict, result_queue: multiprocessing.Queue) -> None:
    name = store["name"]
    url = store["url"]
    store_type = store["type"]

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--disable-http2",
                    "--no-sandbox",
                    "--disable-dev-shm-usage",
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

            page = context.new_page()
            # Block heavy media/fonts so pages load fast and don't stall memory
            page.route(
                re.compile(r"\.(png|jpg|jpeg|gif|webp|svg|mp4|woff|woff2)(\?.*)?$", re.I),
                lambda route: route.abort(),
            )

            try:
                response = page.goto(url, wait_until="domcontentloaded", timeout=14000)
                page.wait_for_timeout(3500)
                status = response.status if response else "No response"
            except PlaywrightTimeout:
                status = "Partial load"

            html_snippet = ""
            try:
                html_snippet = page.content()[:5000].lower()
            except Exception:
                pass

            if any(flag in html_snippet for flag in BLOCK_INDICATORS):
                result_queue.put({
                    "in_stock": False,
                    "log": f"[{name}] BLOCKED / CAPTCHA detected (HTTP {status}).",
                    "summary": f"• **{name}**: Blocked / CAPTCHA (HTTP {status})",
                })
                return

            if is_in_stock(page, store_type):
                result_queue.put({
                    "in_stock": True,
                    "log": f"[{name}] IN STOCK! Sending Discord alert...",
                    "summary": f"• **{name}**: **IN STOCK!**",
                })
            else:
                result_queue.put({
                    "in_stock": False,
                    "log": f"[{name}] Out of stock (HTTP {status}).",
                    "summary": f"• **{name}**: Out of stock",
                })

            browser.close()

    except Exception as err:
        result_queue.put({
            "in_stock": False,
            "log": f"[{name}] Error: {err}",
            "summary": f"• **{name}**: Error",
        })


def check_all_stores() -> None:
    if not DISCORD_WEBHOOK_URL:
        print("Missing DISCORD_WEBHOOK_URL environment variable.", flush=True)
        sys.exit(1)

    status_report = []

    for store in STORES:
        name = store["name"]
        url = store["url"]
        print(f"[{name}] Checking...", flush=True)

        q = multiprocessing.Queue()
        proc = multiprocessing.Process(target=check_single_store, args=(store, q))
        proc.start()
        proc.join(timeout=22)

        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)
            if proc.is_alive():
                proc.kill()
            print(f"[{name}] Timed out (hard stop after 22s).", flush=True)
            status_report.append(f"• **{name}**: Timed out (blocked cloud IP)")
        elif not q.empty():
            res = q.get_nowait()
            print(res["log"], flush=True)
            status_report.append(res["summary"])
            if res["in_stock"]:
                send_stock_alert(name, url)
        else:
            print(f"[{name}] Process exited unexpectedly.", flush=True)
            status_report.append(f"• **{name}**: Error")

    if EVENT_NAME == "workflow_dispatch":
        print("Manual run detected. Sending summary to Discord...", flush=True)
        send_manual_summary(status_report)

    print("All checks finished.", flush=True)


if __name__ == "__main__":
    check_all_stores()
