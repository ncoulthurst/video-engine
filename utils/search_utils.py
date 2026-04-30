import os
import requests
import json

def web_search(query, num_results=3):
    """
    Performs a web search using SerpApi (requires SERPAPI_KEY).
    Returns a list of results with title, link, and snippet.
    """
    api_key = os.environ.get("SERPAPI_KEY")
    if not api_key:
        print("Warning: SERPAPI_KEY not found. Search will return empty results.")
        return []

    url = "https://serpapi.com/search"
    params = {
        "q": query,
        "api_key": api_key,
        "engine": "google",
        "num": num_results
    }

    try:
        response = requests.get(url, params=params)
        response.raise_for_status()
        data = response.json()
        
        results = []
        for result in data.get("organic_results", []):
            results.append({
                "title": result.get("title"),
                "link": result.get("link"),
                "snippet": result.get("snippet"),
                "source": result.get("source")
            })
        return results
    except Exception as e:
        print(f"Error during search: {e}")
        return []

if __name__ == "__main__":
    # Test search
    res = web_search("Manchester City wins 2024 Premier League")
    print(json.dumps(res, indent=4))
