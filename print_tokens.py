import requests
import json
uuid = "6cb015d7-32d8-43e9-ba24-107ea6265dbd"
url = f"https://sdk.companywebcast.com/accessrules/{uuid}"
headers = {"Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/", "User-Agent": "Mozilla/5.0"}
resp = requests.get(url, headers=headers)
id_token = resp.json().get("identificationToken")

headers["x-authorization"] = id_token
resp2 = requests.get(url, headers=headers)
ssl_tokens = resp2.json().get("readTokens", {}).get("ssl", {})

print(json.dumps(ssl_tokens.get("playlist"), indent=2))
