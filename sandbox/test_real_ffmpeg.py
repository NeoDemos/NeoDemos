import requests
import subprocess
import sys
from urllib.parse import urlencode

uuid = "6cb015d7-32d8-43e9-ba24-107ea6265dbd"
url = f"https://sdk.companywebcast.com/accessrules/{uuid}"
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"}
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

info_url = f"https://sdk.companywebcast.com/players/{uuid}/stream/hls?{params}"
print("Fetching stream/hls json:", info_url)
r = requests.get(info_url, headers=headers)
if r.status_code != 200:
    print("Error fetching stream/hls", r.status_code, r.text)
    sys.exit(1)

inner_json = r.json()
print("Inner JSON:", inner_json)
if isinstance(inner_json, list):
    inner_json = inner_json[0]
playlist_path = inner_json.get("src")
actual_hls_url = f"https://sdk.companywebcast.com{playlist_path}?{params}"
print("Actual HLS URL:", actual_hls_url)

cmd = [
    "ffmpeg", "-y", "-headers", "User-Agent: Mozilla/5.0\r\nReferer: https://rotterdamraad.bestuurlijkeinformatie.nl/\r\n",
    "-i", actual_hls_url,
    "-t", "5",
    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "test2.wav"
]
print("Running ffmpeg...")
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode != 0:
    print("FFMPEG ERROR:")
    print(res.stderr)
else:
    print("SUCCESS")
