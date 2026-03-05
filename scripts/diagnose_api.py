import httpx
import asyncio
import json

async def diagnose():
    url = "https://api.openraadsinformatie.nl/v1/elastic"
    index = "ori_rotterdam_20250629013104"
    
    print("Checking AgendaItem structure for Rotterdam...")
    query = {
        "query": {
            "bool": {
                "must": [
                    { "term": { "_index": index } },
                    { "term": { "@type": "AgendaItem" } }
                ]
            }
        },
        "size": 1
    }
    
    async with httpx.AsyncClient() as client:
        resp = await client.post(f"{url}/_search", json=query)
        if resp.status_code == 200:
            hits = resp.json().get("hits", {}).get("hits", [])
            if hits:
                print(json.dumps(hits[0].get("_source"), indent=2))
                print(f"Parent meeting link suspect: {hits[0].get('_source').get('is_part_of')}")
            else:
                print("No AgendaItem found.")
        else:
            print(f"Failed: {resp.status_code}, {resp.text}")

if __name__ == "__main__":
    asyncio.run(diagnose())
