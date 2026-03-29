import sys
import json
from pipeline.scraper import RoyalcastScraper

scraper = RoyalcastScraper()
code = "gemeenterotterdam_20260204_3"
print(f"Fetching metadata for {code}")
# Call the method manually to get the raw data
url = f"https://sdk.companywebcast.com/players/{code}/info"
resp = scraper.session.get(url)
data = resp.json()

print(json.dumps(data.get("videoSettings"), indent=2))
print(json.dumps([s for s in data.get("sources", []) if s.get("streamType") == "hls"], indent=2))
