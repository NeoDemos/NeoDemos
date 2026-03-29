import requests
import subprocess
import os
from urllib.parse import urlencode

uuid = "6cb015d7-32d8-43e9-ba24-107ea6265dbd"
url = f"https://sdk.companywebcast.com/accessrules/{uuid}"
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"}
resp = requests.get(url, headers=headers)
id_token = resp.json().get("identificationToken")

headers["x-authorization"] = id_token
resp2 = requests.get(url, headers=headers)
ssl_tokens = resp2.json().get("readTokens", {}).get("ssl", {})

# Try to get them directly. If not, use the fallback find_token_block
def get_tokens(key):
    if key in ssl_tokens and "Signature" in ssl_tokens[key]:
        return ssl_tokens[key]
    
    # Fallback to older nested wildcards style
    for v in ssl_tokens.values():
        if isinstance(v, dict) and "Signature" in v:
            return v
    return None

players_tokens = get_tokens("players")
playlist_tokens = get_tokens("playlist")

print("players_tokens:", list(players_tokens.keys()) if players_tokens else None)
print("playlist_tokens:", list(playlist_tokens.keys()) if playlist_tokens else None)

info_url = f"https://sdk.companywebcast.com/players/{uuid}/stream/hls?{urlencode(players_tokens)}"
r = requests.get(info_url, headers=headers)
inner_json = r.json()
if isinstance(inner_json, list):
    inner_json = inner_json[0]
playlist_path = inner_json.get("src")
actual_hls_url = f"https://sdk.companywebcast.com{playlist_path}?{urlencode(playlist_tokens)}"

print("Actual HLS:", actual_hls_url)
cmd = [
    "ffmpeg", "-y", "-headers", "User-Agent: Mozilla/5.0\r\nReferer: https://rotterdamraad.bestuurlijkeinformatie.nl/\r\n",
    "-i", actual_hls_url,
    "-t", "5",
    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "test2.wav"
]
subprocess.run(cmd)
if os.path.exists("test2.wav"):
    print("SUCCESS: test2.wav created. Size:", os.path.getsize("test2.wav"))
else:
    print("FAILED: test2.wav not found.")
