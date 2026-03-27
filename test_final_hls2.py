import requests
import subprocess
from urllib.parse import urlencode

uuid = "6cb015d7-32d8-43e9-ba24-107ea6265dbd"
url = f"https://sdk.companywebcast.com/accessrules/{uuid}"
headers = {"User-Agent": "Mozilla/5.0", "Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"}
resp = requests.get(url, headers=headers)
id_token = resp.json().get("identificationToken")

headers["x-authorization"] = id_token
resp2 = requests.get(url, headers=headers)
ssl_tokens = resp2.json().get("readTokens", {}).get("ssl", {})

# Helper to find tokens by path substring
def get_tokens_for_path(tokens_dict, path_substr):
    for k, v in tokens_dict.items():
        if path_substr in k:
            # The tokens might be nested if it's {"Signature": ...} or {"CloudFront-Signature": ...}
            # For HLS URL params, we want "Signature", "Policy", "Key-Pair-Id"
            if "Signature" in v:
                return v
            # translate CloudFront- to standard if needed
            cf_tokens = {}
            if "CloudFront-Signature" in v:
                cf_tokens["Signature"] = v["CloudFront-Signature"]
                cf_tokens["Policy"] = v["CloudFront-Policy"]
                cf_tokens["Key-Pair-Id"] = v["CloudFront-Key-Pair-Id"]
                return cf_tokens
    return None

players_tokens = get_tokens_for_path(ssl_tokens, "/players/")
playlist_tokens = get_tokens_for_path(ssl_tokens, "/playlist/")

print("Players tokens:", players_tokens)
print("Playlist tokens:", playlist_tokens)

info_url = f"https://sdk.companywebcast.com/players/{uuid}/stream/hls?{urlencode(players_tokens)}"
r = requests.get(info_url, headers=headers)
if r.status_code != 200:
    print("Error fetching stream/hls info", r.status_code)
    exit(1)

inner_json = r.json()
if isinstance(inner_json, list):
    inner_json = inner_json[0]
playlist_path = inner_json.get("src")
actual_hls_url = f"https://sdk.companywebcast.com{playlist_path}?{urlencode(playlist_tokens)}"

cmd = [
    "ffmpeg", "-y", "-headers", "User-Agent: Mozilla/5.0\r\nReferer: https://rotterdamraad.bestuurlijkeinformatie.nl/\r\n",
    "-i", actual_hls_url,
    "-t", "5",
    "-vn", "-acodec", "pcm_s16le", "-ar", "16000", "-ac", "1", "test2.wav"
]
print("Running ffmpeg on", actual_hls_url)
res = subprocess.run(cmd, capture_output=True, text=True)
if res.returncode != 0:
    print("FFMPEG ERROR:")
    print(res.stderr)
else:
    print("SUCCESS, test2.wav created.")
