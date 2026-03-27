from playwright.sync_api import sync_playwright

def find_stream_url(webcast_code):
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        
        stream_urls = []
        
        def handle_request(request):
            url = request.url
            if ".m3u8" in url or ".mp4" in url or ".mp3" in url:
                print(f"Intercepted media URL: {url}")
                # Print headers to see if cookies or auth tokens are in headers
                if 'cookie' in request.headers:
                    print(f"  Cookie: {request.headers['cookie']}")
                stream_urls.append(url)
                
        page.on("request", handle_request)
        
        url = f"https://sdk.companywebcast.com/sdk/player/?id={webcast_code}"
        print(f"Loading {url}")
        page.goto(url, wait_until="networkidle")
        page.wait_for_timeout(3000)
        
        # Try to play
        try:
            for frame in page.frames:
                if frame.locator(".vjs-big-play-button").count() > 0:
                    frame.locator(".vjs-big-play-button").click(timeout=3000)
                    print("Clicked play in frame:", frame.url)
                    break
        except Exception as e:
            pass
            
        page.wait_for_timeout(4000)
        browser.close()
        
        return stream_urls

find_stream_url("gemeenterotterdam_20260107_1")
