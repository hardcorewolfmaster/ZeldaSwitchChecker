import multiprocessing
import os
import re
import sys
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
EVENT_NAME = os.environ.get("EVENT_NAME", "").strip()

# Sierra Vista, AZ coordinates & ZIP for local store pickup checks
LOCAL_ZIP = "85635"
LOCAL_COORDS = {"latitude": 31.5546, "longitude": -110.2584}

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
    {
        "name": "GameStop (Sierra Vista)",
        "url": "https://www.gamestop.com/consoles-hardware/nintendo-switch-2/products/nintendo-switch-2-the-legend-of-zelda-40th-anniversary-edition/451607.html",
        "type": "gamestop",
    },
]

BLOCK_INDICATORS = [
    "access denied",
    "you don't have permission to access",
    "robot check",
    "enter the characters you see below",
    "pardon our interruption",
    "sorry, we just need to make sure you're not a robot",
    "attention required! | cloudflare",
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


def check_gamestop_local(page) -> tuple[bool, str]:
    """Selects Sierra Vista (85635) on GameStop and checks local store pickup status."""
    try:
        body_text = page.locator("body").inner_text().lower()

        # If Sierra Vista isn't already auto-selected via GPS, open the store finder and enter 85635
        if "sierra vista" not in body_text:
            store_triggers = page.locator("button:visible, a:visible").filter(
                has_text=re.compile(
                    r"(pick up|change store|check other stores|select a store|find a store)",
                    re.I,
                )
            )
            if store_triggers.count() > 0:
                store_triggers.first.click(timeout=3000)
                page.wait_for_timeout(1500)

            zip_input = page.locator(
                'input[placeholder*="zip" i]:visible, '
                'input[placeholder*="postal" i]:visible, '
                'input[name*="zip" i]:visible, '
                'input[name*="postal" i]:visible, '
                'input[id*="zip" i]:visible, '
                'input[aria-label*="zip" i]:visible'
            )
            if zip_input.count() > 0:
                zip_input.first.fill(LOCAL_ZIP)
                zip_input.first.press("Enter")
                search_btn = page.locator("button:visible").filter(
                    has_text=re.compile(r"^(search|find|submit|apply|update)$", re.I)
                )
                if search_btn.count() > 0:
                    search_btn.first.click(timeout=2000)
                page.wait_for_timeout(3000)

        # Inspect the store card/container that specifically mentions Sierra Vista
        store_card_text = page.evaluate(
            """() => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    if (node.nodeValue && node.nodeValue.toLowerCase().includes('sierra vista')) {
                        let el = node.parentElement;
                        for (let i = 0; i < 5 && el && el.parentElement; i++) {
                            if (el.innerText && el.innerText.length > 40) break;
                            el = el.parentElement;
                        }
                        return el ? el.innerText.toLowerCase() : '';
                    }
                }
                return null;
            }"""
        )

        if not store_card_text:
            return False, "Sierra Vista store not shown"

        unavailable_phrases = [
            "out of stock",
            "unavailable",
            "not available",
            "sold out",
        ]
        if any(phrase in store_card_text for phrase in unavailable_phrases):
            return False, "Out of stock at Sierra Vista"

        available_phrases = [
            "in stock",
            "low stock",
            "pick up today",
            "pick up in store",
            "ready for pickup",
            "available for pickup",
        ]
        if any(phrase in store_card_text for phrase in available_phrases):
            return True, "IN STOCK at Sierra Vista!"

        return False, "Out of stock at Sierra Vista"

    except Exception as err:
        return False, f"Store check error ({err})"


def is_in_stock(page, store_type: str) -> tuple[bool, str]:
    try:
        if store_type == "nintendo":
            btn = page.locator("button:visible").filter(
                has_text=re.compile(r"^(add to cart|pre-order)$", re.I)
            )
            found = btn.count() > 0 and btn.first.is_enabled(timeout=3000)
            return found, ("IN STOCK!" if found else "Out of stock")

        if store_type == "bestbuy":
            btn = page.locator(
                'button[data-button-state="ADD_TO_CART"]:visible, '
                'button[data-button-state="PRE_ORDER"]:visible, '
                '.fulfillment-add-to-cart-button button:visible'
            ).filter(has_text=re.compile(r"(add to cart|pre-order)", re.I))
            found = btn.count() > 0 and btn.first.is_enabled(timeout=3000)
            return found, ("IN STOCK!" if found else "Out of stock")

        if store_type == "target":
            btn = page.locator(
                'button[data-test="shippingButton"]:visible, '
                'button[data-test="orderPickupButton"]:visible, '
                'button[data-test="deliveryButton"]:visible'
            )
            found = btn.count() > 0 and btn.first.is_enabled(timeout=3000)
            return found, ("IN STOCK!" if found else "Out of stock")

        if store_type == "amazon":
            btn = page.locator(
                "#add-to-cart-button:visible, #buy-now-button:visible"
            )
            found = btn.count() > 0 and btn.first.is_enabled(timeout=3000)
            return found, ("IN STOCK!" if found else "Out of stock")

        if store_type == "gamestop":
            return check_gamestop_local(page)

    except Exception:
        return False, "Out of stock"

    return False, "Out of stock"


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
                geolocation=LOCAL_COORDS,
                permissions=["geolocation"],
            )
            context.set_default_timeout(5000)
            context.add_init_script(
                "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
            )

            page = context.new_page()
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

            in_stock, detail = is_in_stock(page, store_type)
            if in_stock:
                result_queue.put({
                    "in_stock": True,
                    "log": f"[{name}] {detail} Sending Discord alert...",
                    "summary": f"• **{name}**: **{detail}**",
                })
            else:
                result_queue.put({
                    "in_stock": False,
                    "log": f"[{name}] {detail} (HTTP {status}).",
                    "summary": f"• **{name}**: {detail}",
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
        proc.join(timeout=28)

        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)
            if proc.is_alive():
                proc.kill()
            print(f"[{name}] Timed out (hard stop after 28s).", flush=True)
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
