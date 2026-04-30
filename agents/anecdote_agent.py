from utils.llm_utils import ask_llm
from utils.file_utils import save_json, save_text
import json

def research_anecdotes(entity, output_dir):
    print(f"[*] Anecdote Agent searching for human moments: {entity}")
    
    prompt = f"""
    Find 5-10 verified, high-impact anecdotes and human moments for the football subject: '{entity}'.
    Focus on autobiographies, iconic interviews, and major journalism (The Athletic, Guardian, ESPN, BBC Sport).

    REQUIREMENTS:
    - Look for defining career moments: turning points, controversies, personal revelations.
    - Look for "locker room" moments or specific quotes that reveal genuine personality.
    - Cite the source type (e.g., 'Autobiography', 'Post-match Press Conference', 'ESPN Documentary').
    - Never fabricate quotes. If a quote is paraphrased, begin the string value with "PARAPHRASED: " inside the JSON string — never put text outside the quote string value.

    OUTPUT FORMAT (JSON object with an "anecdotes" list):
    {{
      "anecdotes": [
        {{
          "anecdote": "Short title",
          "year": "YYYY",
          "source_type": "Source name/type",
          "quote": "Exact quote if available, otherwise null",
          "context": "Briefly describe the human tension",
          "narrative_purpose": "Why does this keep viewers watching?"
        }}
      ]
    }}
    """
    
    res = ask_llm(prompt, expect_json=True)
    try:
        parsed = json.loads(res)
        # Groq json_object mode can't return a top-level array, so unwrap if needed
        if isinstance(parsed, dict):
            # Find the first list value in the dict
            data = next((v for v in parsed.values() if isinstance(v, list)), [])
        elif isinstance(parsed, list):
            data = parsed
        else:
            data = []
        # Filter out any non-dict items
        data = [item for item in data if isinstance(item, dict)]
    except Exception as e:
        data = []
        print(f"    [!] Failed to parse anecdotes JSON: {e}")

    save_json(f"{output_dir}/anecdotes.json", data)
    
    # Also save a readable version for reference
    readable = "# VERIFIED ANECDOTES\n\n"
    for item in data:
        readable += f"## {item.get('anecdote')} ({item.get('year')})\n"
        readable += f"- **Source:** {item.get('source_type')}\n"
        if item.get('quote'):
            readable += f"- **Quote:** \"{item.get('quote')}\"\n"
        readable += f"- **Context:** {item.get('context')}\n"
        readable += f"- **Purpose:** {item.get('narrative_purpose')}\n\n"
    
    save_text(f"{output_dir}/anecdotes_dossier.md", readable)
    return data
