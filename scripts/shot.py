"""Screenshot a URL for visual inspection. Usage: python scripts/shot.py <url> <out.png> [width]"""
import asyncio
import sys

from playwright.async_api import async_playwright


async def main(url: str, out: str, width: int) -> None:
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        page = await browser.new_page(viewport={"width": width, "height": 1400},
                                      device_scale_factor=2)
        await page.goto(url, wait_until="networkidle", timeout=30000)
        await page.wait_for_timeout(1800)  # let fetches + charts render
        await page.screenshot(path=out, full_page=True)
        await browser.close()
    print("saved", out)


if __name__ == "__main__":
    url, out = sys.argv[1], sys.argv[2]
    w = int(sys.argv[3]) if len(sys.argv) > 3 else 1100
    asyncio.run(main(url, out, w))
