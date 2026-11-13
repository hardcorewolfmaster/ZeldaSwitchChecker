import multiprocessing
import os
import re
import sys
import requests
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

DISCORD_WEBHOOK_URL = os.environ.get("DISCORD_WEBHOOK_URL", "").strip()
EVENT_NAME = os.environ.get("EVENT_NAME", "").strip()

# Exact location for 480 N Highway 90 Byp Ste A3, Sierra Vista, AZ 85635
LOCAL_ZIP = "85635"
LOCAL_COORDS = {"latitude": 31.5586, "longitude": -110.2564}

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
        "name": "GameStop (Sierra Vista - In-Store)",
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


def dismiss_popups(page) -> None:
    for selector in [
        "#onetrust-accept-btn-handler",
        'button:has-text("Accept All")',
        'button[aria-label*="close" i]',
    ]:
        try:
            btn = page.locator(selector).first
            if btn.is_visible(timeout=700):
                btn.click(timeout=1000)
                page.wait_for_timeout(400)
        except Exception:
            pass


def set_and_search_gamestop_store(page) -> None:
    """Opens GameStop's store selector/availability modal and searches 85635."""
    try:
        triggers = page.locator('button:visible, a:visible, [role="button"]:visible').filter(
            has_text=re.compile(
                r"(check store availability|find in store|select a store|set store|change store|check other stores)",
                re.I,
            )
        )
        if triggers.count() > 0:
            triggers.first.click(timeout=2500)
            page.wait_for_timeout(1200)

        zip_input = page.locator(
            'input[placeholder*="zip" i]:visible, '
            'input[placeholder*="city" i]:visible, '
            'input[placeholder*="postal" i]:visible, '
            'input[name*="zip" i]:visible, '
            'input[id*="zip" i]:visible, '
            'input[aria-label*="zip" i]:visible'
        )
        if zip_input.count() > 0:
            zip_input.first.fill(LOCAL_ZIP)
            zip_input.first.press("Enter")
            search_btn = page.locator("button:visible").filter(
                has_text=re.compile(r"^(search|find|submit|apply|update|check)$", re.I)
            )
            if search_btn.count() > 0:
                try:
                    search_btn.first.click(timeout=1500)
                except Exception:
                    pass
            page.wait_for_timeout(2200)

            make_my_store = page.locator("button:visible").filter(
                has_text=re.compile(r"(make my store|set as my store|set store|select store)", re.I)
            )
            if make_my_store.count() > 0:
                try:
                    make_my_store.first.click(timeout=1500)
                    page.wait_for_timeout(1500)
                except Exception:
                    pass
    except Exception:
        pass


def check_gamestop(page, api_responses: list[str]) -> tuple[bool, str]:
    """Checks GameStop's internal inventory API responses and local store status for Sierra Vista."""
    try:
        dismiss_popups(page)
        set_and_search_gamestop_store(page)

        # 1. Inspect captured JSON API responses for Sierra Vista / 480 N Highway 90 / 85635
        for raw_json in api_responses:
            lower_json = raw_json.lower()
            if any(loc in lower_json for loc in ["480 n highway 90", "sierra vista", "85635"]):
                # Extract the slice near the Sierra Vista store entry
                idx = max(
                    lower_json.find("480 n highway 90"),
                    lower_json.find("sierra vista"),
                    lower_json.find("85635"),
                )
                window = lower_json[max(0, idx - 300) : idx + 600]

                if (
                    '"instock":true' in window.replace(" ", "")
                    or '"available":true' in window.replace(" ", "")
                    or re.search(r'"ats"\s*:\s*[1-9]\d*', window)
                    or re.search(r'"quantity"\s*:\s*[1-9]\d*', window)
                    or any(p in window for p in ["in stock", "low stock", "limited stock"])
                ):
                    return True, "IN STOCK at 480 N Hwy 90 Byp!"

                if (
                    '"instock":false' in window.replace(" ", "")
                    or '"available":false' in window.replace(" ", "")
                    or re.search(r'"ats"\s*:\s*0\b', window)
                    or any(p in window for p in ["out of stock", "not available", "unavailable"])
                ):
                    return False, "Out of stock at 480 N Hwy 90 Byp"

        # 2. Check any visible store card/modal on the page for 480 N Highway 90 / Sierra Vista
        store_card_text = page.evaluate(
            """() => {
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const txt = (node.nodeValue || '').toLowerCase();
                    if (
                        txt.includes('480 n highway 90') ||
                        txt.includes('highway 90 byp') ||
                        txt.includes('sierra vista')
                    ) {
                        let el = node.parentElement;
                        for (let i = 0; i < 6 && el && el.parentElement; i++) {
                            if (el.innerText && el.innerText.length > 50) break;
                            el = el.parentElement;
                        }
                        return el ? el.innerText.toLowerCase() : '';
                    }
                }
                return null;
            }"""
        )

        if store_card_text:
            if any(p in store_card_text for p in ["out of stock", "unavailable", "not available", "sold out"]):
                return False, "Out of stock at 480 N Hwy 90 Byp"
            if any(p in store_card_text for p in ["in stock", "low stock", "limited stock", "available"]):
                return True, "IN STOCK at 480 N Hwy 90 Byp!"

        # 3. Inspect the main product status button/badge for SKU 451607 (top of page only)
        sku_btn = page.locator('button[data-pid="451607"]:visible')
        if sku_btn.count() > 0:
            label = sku_btn.first.inner_text().strip()
            if re.search(r"^(add to cart|pre-order|buy now|in-store only|in stock)$", label, re.I):
                return True, f"AVAILABLE ({label})!"
            return False, f"Out of stock ({label})"

        candidates = page.locator("button:visible").filter(
            has_text=re.compile(
                r"^(add to cart|pre-order|buy now|in-store only|not available|coming soon|out of stock|sold out)$",
                re.I,
            )
        )
        for i in range(min(candidates.count(), 6)):
            btn = candidates.nth(i)
            box = btn.bounding_box()
            if not box or box["y"] > 950:
                continue
            label = btn.inner_text().strip()
            if re.match(r"^(add to cart|pre-order|buy now|in-store only)$", label, re.I):
                return True, f"AVAILABLE ({label})!"
            return False, f"Out of stock ({label})"

        return False, "Out of stock"

    except Exception as err:
        return False, f"Check error ({err})"


def is_in_stock(page, store_type: str, api_responses: list[str]) -> tuple[bool, str]:
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
            return check_gamestop(page, api_responses)

    except Exception:
        return False, "Out of stock"

    return False, "Out of stock"


def check_single_store(store: dict, result_queue: multiprocessing.Queue) -> None:
    name = store["name"]
    url = store["url"]
    store_type = store["type"]
    api_responses: list[str] = []

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

            if store_type == "gamestop":
                def capture_json(resp):
                    try:
                        if resp.status == 200 and resp.request.resource_type in ("xhr", "fetch"):
                            ct = resp.headers.get("content-type", "")
                            if "json" in ct or any(k in resp.url.lower() for k in ["store", "product", "avail", "invent"]):
                                api_responses.append(resp.text()[:15000])
                    except Exception:
                        pass

                page.on("response", capture_json)

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

            in_stock, detail = is_in_stock(page, store_type, api_responses)
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
        proc.join(timeout=35 if store["type"] == "gamestop" else 28)

        if proc.is_alive():
            proc.terminate()
            proc.join(timeout=3)
            if proc.is_alive():
                proc.kill()
            print(f"[{name}] Timed out (hard stop).", flush=True)
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
