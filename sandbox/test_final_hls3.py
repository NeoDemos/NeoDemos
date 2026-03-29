import requests
import json
from urllib.parse import urlencode

uuid = "6cb015d7-32d8-43e9-ba24-107ea6265dbd"
url = f"https://sdk.companywebcast.com/accessrules/{uuid}"
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"}
resp = requests.get(url, headers=headers)
id_token = resp.json().get("identificationToken")

headers["x-authorization"] = id_token
resp2 = requests.get(url, headers=headers)
ssl_tokens = resp2.json().get("readTokens", {}).get("ssl", {})

def get_actual_tokens(block):
    # The block might be an inner dictionary keyed by an arbitrary url like "https://.../*"
    for v in block.values():
        if isinstance(v, dict):
            if "Signature" in v:
                return v
            elif "CloudFront-Signature" in v:
                return {
                    "Signature": v["CloudFront-Signature"],
                    "Policy": v["CloudFront-Policy"],
                    "Key-Pair-Id": v["CloudFront-Key-Pair-Id"]
                }
    return None

players_tokens = get_actual_tokens(ssl_tokens.get("players", {}))
playlist_tokens = get_actual_tokens(ssl_tokens.get("playlist", {}))

print("players_tokens:", players_tokens)
print("playlist_tokens:", playlist_tokens)

info_url = f"https://sdk.companywebcast.com/players/{uuid}/stream/hls?{urlencode(players_tokens)}"
r = requests.get(info_url, headers=headers)
inner_json = r.json()
if isinstance(inner_json, list):
    inner_json = inner_json[0]
playlist_path = inner_json.get("src")
actual_hls_url = f"https://sdk.companywebcast.com{playlist_path}?{urlencode(playlist_tokens)}"

print("Fetching URL:", actual_hls_url)
import subprocess
cmd = [
    "ffmpeg", "-y", "-headers", "User-Agent: Mozilla/5.0\r\nReferer: https://rotterdamraad.bestuurlijkeinformatie.nl/\r\n",
    "-i", actual_hls_url,
    "-t", "5",
    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "test2.wav"
]
subprocess.run(cmd)
