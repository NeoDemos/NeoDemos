from playwright.sync_api import sync_playwright
import json
import time

def get_cloudfront_cookies(webcast_code):
    print(f"Starting browser to get cookies for {webcast_code}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = context.new_page()
        url = f"https://sdk.companywebcast.com/sdk/player/?id={webcast_code}"
        
        print(f"Loading {url}")
        page.goto(url, wait_until="domcontentloaded", timeout=15000)
        
        print("Waiting 3s for player to initialize...")
        page.wait_for_timeout(3000)
        
        # Click the play button to trigger stream authorization if necessary
        try:
            print("Looking for play button...")
            locator = page.locator(".vjs-big-play-button")
            if locator.is_visible():
                locator.click(timeout=3000)
                print("Clicked play button, waiting 3s...")
                page.wait_for_timeout(3000)
        except Exception as e:
            print("Play button error:", e)
            
        cookies = context.cookies()
        browser.close()
        
        print(f"Found {len(cookies)} total cookies")
        cf_cookies = {c['name']: c['value'] for c in cookies if c['name'].startswith('CloudFront')}
        return cf_cookies

if __name__ == "__main__":
    cookies = get_cloudfront_cookies("gemeenterotterdam_20260107_1")
    print("\n--- CLOUDFRONT COOKIES ---")
    print(json.dumps(cookies, indent=2))
