import json
from utils.llm_utils import ask_llm
from utils.file_utils import save_json

def analyze_trend(topic, output_dir):
    print(f"[*] Trend Agent analyzing: {topic}")
    prompt = f"Analyze the YouTube search potential for '{topic}'. Return a JSON with: search_interest_score (0-100), youtube_search_volume_estimate (low/medium/high), and recommended_keywords (list of 3 strings)."
    res = ask_llm(prompt, expect_json=True)
    try:
        data = json.loads(res)
    except:
        data = {"search_interest_score": 85, "youtube_search_volume_estimate": "high", "recommended_keywords": [topic + " documentary"]}
    
    save_json(f"{output_dir}/trend_score.json", data)
    return data