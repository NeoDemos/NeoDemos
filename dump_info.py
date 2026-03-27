import requests
import json
url = f"https://sdk.companywebcast.com/players/gemeenterotterdam_20260204_3/info"
headers = {"Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/", "User-Agent": "Mozilla/5.0"}
resp = requests.get(url, headers=headers)
print("Status:", resp.status_code)
try:
    data = resp.json()
    print(json.dumps(data.get("sources", []), indent=2))
except Exception as e:
    print("Error:", e)
    print("Text:", resp.text[:500])
