import requests
import json
url = "https://sdk.companywebcast.com/players/6cb015d7-32d8-43e9-ba24-107ea6265dbd/info"
resp = requests.get(url, headers={"Referer": "https://rotterdamraad.bestuurlijkeinformatie.nl/"})
with open("info_output.json", "w") as f:
    json.dump(resp.json(), f, indent=2)
print("Done")
