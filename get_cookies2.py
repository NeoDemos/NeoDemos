from playwright.sync_api import sync_playwright
import json

def get_cloudfront_cookies(webcast_code):
    print(f"Starting browser to get cookies for {webcast_code}...")
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
        )
        page = context.new_page()
        
        # Go to the actual channel page, which sets the cookies
        url = f"https://channel.royalcast.com/gemeenterotterdam/#!/history/20260107_1"
        try:
            print(f"Loading {url}")
            page.goto(url, wait_until="networkidle", timeout=30000)
            page.wait_for_timeout(3000)
            
            # Find the iframe and click play if it's there
            frames = page.frames
            print(f"Found {len(frames)} frames")
            
            cookies = context.cookies()
            print(f"Found {len(cookies)} total cookies")
            
            c_dict = {c['name']: c['value'] for c in cookies if c['name'].startswith('Cloud')}
            print("Cloud cookies:", c_dict)
            
        except Exception as e:
            print("Error:", e)
        finally:
            browser.close()

if __name__ == "__main__":
    get_cloudfront_cookies("gemeenterotterdam_20260107_1")
