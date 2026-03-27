import requests
uuid = "6cb015d7-32d8-43e9-ba24-107ea6265dbd"
url = f"https://sdk.companywebcast.com/accessrules/{uuid}"
resp = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"})
id_token = resp.json().get("identificationToken")

resp2 = requests.get(url, headers={"User-Agent": "Mozilla/5.0", "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/", "x-authorization": id_token})
read_tokens = resp2.json().get("readTokens", {})
def find_token_block(d):
    if not isinstance(d, dict): return None
    if 'Signature' in d and 'Policy' in d: return d
    for v in d.values():
        res = find_token_block(v)
        if res: return res
    return None

tokens = find_token_block(read_tokens)
from urllib.parse import urlencode
params = urlencode(tokens)
hls_url = f"https://sdk.companywebcast.com/players/{uuid}/stream/hls?{params}"

headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"}
print(f"Requesting: {hls_url}")
r = requests.get(hls_url, headers=headers)
print("Status:", r.status_code)
print("Content-Type:", r.headers.get("Content-Type"))
print("Text:", r.text[:500])
