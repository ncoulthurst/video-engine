import json
import time
import requests
from utils.llm_utils import ask_llm
from utils.file_utils import save_json

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
}


def _wikipedia_full_article(player_name):
    """
    Fetch the full Wikipedia article text for a player.
    Wikipedia's API is open, no key needed, no bot protection.
    The full article includes career stats sections in plain text.
    """
    try:
        title = player_name.replace(" ", "_")
        url = (
            "https://en.wikipedia.org/w/api.php"
            f"?action=query&prop=extracts&explaintext=true"
            f"&titles={requests.utils.quote(title)}&format=json&redirects=1"
        )
        resp = requests.get(url, headers=HEADERS, timeout=15)
        pages = resp.json().get("query", {}).get("pages", {})
        page = next(iter(pages.values()), {})
        extract = page.get("extract", "")
        if extract and len(extract) > 200:
            print(f"    -> Wikipedia article: {len(extract)} chars for {player_name}")
            return extract
    except Exception as e:
        print(f"    [!] Wikipedia error for {player_name}: {e}")
    return ""


def _extract_stats_from_text(player_name, article_text):
    """
    Use the LLM to extract structured stats from real Wikipedia text.
    Much more accurate than pure hallucination since it's summarising real content.
    """
    # Truncate to stay within token limits
    text = article_text[:8000]
    prompt = f"""
From the Wikipedia article text below, extract career statistics for {player_name}.

Return JSON with these fields:
{{
  "name": "",
  "career_goals": 0,
  "career_assists": 0,
  "career_appearances": 0,
  "clubs": [
    {{"club": "", "years": "", "appearances": 0, "goals": 0}}
  ],
  "national_team": {{"caps": 0, "goals": 0}},
  "major_trophies": [],
  "individual_awards": [],
  "best_season": {{"season": "", "club": "", "goals": 0, "assists": 0}},
  "notable_stats": []
}}

RULES:
- Only use numbers that explicitly appear in the article text below.
- If a number is not in the text, use null.
- Do not invent or estimate any statistics.

ARTICLE TEXT:
{text}
"""
    res = ask_llm(prompt, expect_json=True)
    try:
        parsed = json.loads(res)
        if isinstance(parsed, dict):
            return parsed
    except Exception:
        pass
    return {"name": player_name, "error": "Could not parse stats"}


def fetch_data(entities, output_dir):
    print(f"[*] Data Agent pulling statistics for: {entities}")

    names = [n.strip() for n in entities.split(",")]
    all_data = {}

    for name in names:
        print(f"    -> Fetching Wikipedia article for: {name}")
        article = _wikipedia_full_article(name)
        time.sleep(1)

        if article:
            stats = _extract_stats_from_text(name, article)
            # Attach raw article snippet for the script agent to reference
            stats["_wikipedia_excerpt"] = article[:3000]
            all_data[name] = stats
        else:
            print(f"    [!] No Wikipedia data for {name}. Using LLM fallback.")
            prompt = (
                f"Provide accurate career statistics for footballer '{name}' in JSON format. "
                f"Include: name, career_goals, career_assists, clubs (list), "
                f"best_season, major_trophies. Only include facts you are highly confident about."
            )
            res = ask_llm(prompt, expect_json=True)
            try:
                parsed = json.loads(res)
                all_data[name] = parsed if isinstance(parsed, dict) else {"name": name}
            except Exception:
                all_data[name] = {"name": name, "error": "Data unavailable"}

    save_json(f"{output_dir}/player_data.json", all_data)
    return all_data
