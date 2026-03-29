import requests
import json
from urllib.parse import urlencode

uuid = "6cb015d7-32d8-43e9-ba24-107ea6265dbd"
url = f"https://sdk.companywebcast.com/accessrules/{uuid}"
headers = {"Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/", "User-Agent": "Mozilla/5.0"}
resp = requests.get(url, headers=headers)
id_token = resp.json().get("identificationToken")

headers["x-authorization"] = id_token
resp2 = requests.get(url, headers=headers)
read_tokens = resp2.json().get("readTokens", {})

def find_token_block(d):
    if not isinstance(d, dict): return None
    if 'Signature' in d and 'Policy' in d: return d
    for v in d.values():
        res = find_token_block(v)
        if res: return res
    return None

tokens = find_token_block(read_tokens)
params = urlencode(tokens)

playable_url = f"https://sdk.companywebcast.com/players/{uuid}/stream/hls/playable?{params}"
print("Testing playable URL:", playable_url)
r = requests.get(playable_url, headers=headers)
print("Status:", r.status_code)
print("Headers:", dict(r.headers))
print("Content:", r.text[:500])
