import requests
import time
import json

BASE_URL = "http://localhost:8000"

def test_query(q):
    start_time = time.time()
    try:
        response = requests.get(f"{BASE_URL}/api/search", params={"q": q})
        duration = time.time() - start_time
        if response.status_code == 200:
            data = response.json()
            results_count = len(data.get("results", []))
            print(f"Query: '{q}' | Results: {results_count} | Time: {duration:.4f}s")
            if results_count > 0:
                first_result = data["results"][0]
                print(f"  First result: {first_result['name']} ({first_result['meeting_date']})")
                print(f"  Snippet: {first_result['snippet'][:100]}...")
        else:
            print(f"Query: '{q}' | Status: {response.status_code} | Time: {duration:.4f}s")
    except Exception as e:
        print(f"Query: '{q}' | Error: {e}")

if __name__ == "__main__":
    # Test cases
    queries = [
        "fraude",
        "klimaat",
        "woningbouw",
        "Rotterdam",
        "bestemmingsplan",
        "verkeer",
        "armoede"
    ]
    
    print("Testing Search Performance (Ensure server is running on port 8000)")
    for q in queries:
        test_query(q)
    
    # Large query test
    print("\n--- Large Query Test ---")
    test_query("de") # Should return 50 if limit is 50
