from playwright.sync_api import sync_playwright

def trace():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        def handle_response(response):
            headers = response.headers
            if 'set-cookie' in headers:
                if 'CloudFront' in headers['set-cookie'] or 'cloudfront' in headers['set-cookie'].lower():
                    print(f"BINGO! From {response.url}:\n  {headers['set-cookie']}\n")
        
        page.on("response", handle_response)
        
        url = "https://sdk.companywebcast.com/sdk/player/?id=gemeenterotterdam_20260107_1"
        try:
            print(f"Loading {url}")
            page.goto(url, wait_until="networkidle", timeout=20000)
            page.wait_for_timeout(5000)
            
            # Click play
            for frame in page.frames:
                if frame.locator(".vjs-big-play-button").count() > 0:
                    frame.locator(".vjs-big-play-button").click(timeout=3000)
                    print("Clicked play!")
                    page.wait_for_timeout(5000)
                    break
                    
        except Exception as e:
            print("Done tracing:", e)
            
        print("Final Cookies:", page.context.cookies())
        browser.close()

trace()
