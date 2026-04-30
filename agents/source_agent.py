import json
import os
from utils.llm_utils import ask_llm
from utils.file_utils import save_json, load_text
from utils.search_utils import web_search

def find_sources(output_dir):
    print(f"[*] Source Agent finding URLs for script claims in {output_dir}...")
    script = load_text(f"{output_dir}/script_draft.md")
    
    # 1. Extract claims
    prompt = f"""
    Analyze this YouTube script and extract 5 specific factual claims or headlines that need external source URLs (e.g., transfer fees, awards, specific quotes, match results).
    Return a JSON list of strings.

    SCRIPT:
    {script[:5000]}
    """
    
    res = ask_llm(prompt, expect_json=True)
    try:
        parsed = json.loads(res)
        if isinstance(parsed, list):
            claims = [c for c in parsed if isinstance(c, str)]
        elif isinstance(parsed, dict):
            claims = next((v for v in parsed.values() if isinstance(v, list)), [])
            claims = [c for c in claims if isinstance(c, str)]
        else:
            claims = []
    except Exception as e:
        print(f"    [!] Failed to parse claims: {e}")
        claims = []

    # 2. Search for each claim
    sources = []
    for claim in claims:
        print(f"    Searching for: {claim}")
        results = web_search(claim, num_results=1)
        if results:
            sources.append({
                "claim": claim,
                "title": results[0]["title"],
                "url": results[0]["link"],
                "source": results[0].get("source", "Web")
            })
    
    save_json(f"{output_dir}/sources.json", sources)
    
    # 3. Create a summary md for easy reading
    summary = "# SOURCES FOUND\n\n"
    for s in sources:
        summary += f"- **Claim:** {s['claim']}\n- **Source:** [{s['title']}]({s['url']}) ({s['source']})\n\n"
    
    with open(f"{output_dir}/sources.md", "w") as f:
        f.write(summary)
        
    return sources
