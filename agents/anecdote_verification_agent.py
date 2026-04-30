from utils.llm_utils import ask_llm
from utils.file_utils import save_json, save_text, load_json
import json

def verify_anecdotes(output_dir):
    print(f"[*] Verification Agent cross-referencing claims...")
    
    anecdotes = load_json(f"{output_dir}/anecdotes.json")
    if not anecdotes:
        print("    [!] No anecdotes found to verify.")
        return []

    prompt = f"""
    You are a professional fact-checker for a high-end sports documentary.

    TASK: Assess the confidence level of each anecdote below. You are not expected to have perfect knowledge — use your best judgement on what is widely documented vs likely fabricated.

    ANECDOTES TO VERIFY:
    {json.dumps(anecdotes, indent=2)}

    CRITICAL CHECKS (apply to all subjects):
    1. QUOTES: Flag any quote that sounds paraphrased, composite, or suspiciously polished as LOW CONFIDENCE.
    2. CLAIMS: Any claim without a named primary source (autobiography, named interview, named article) should be flagged MEDIUM or LOW.
    3. STATS: Any statistic phrased loosely (e.g., "one of the best ever") without a specific number should be flagged and tightened.
    4. FABRICATION RISK: If an anecdote reads like a plausible but unverifiable "locker room legend", flag it LOW CONFIDENCE.

    OUTPUT FORMAT (JSON object with a "verified" list):
    {{
      "verified": [
        {{
          "anecdote": "Original title",
          "verified_text": "Corrected/refined version",
          "source": "Primary source (e.g. Sky Sports, Autobiography)",
          "confidence": "HIGH/MEDIUM/LOW",
          "correction_notes": "What was changed or flagged?"
        }}
      ]
    }}
    """
    
    res = ask_llm(prompt, expect_json=True)
    try:
        parsed = json.loads(res)
        if isinstance(parsed, dict):
            verified_data = next((v for v in parsed.values() if isinstance(v, list)), anecdotes)
        elif isinstance(parsed, list):
            verified_data = parsed
        else:
            verified_data = anecdotes
        verified_data = [item for item in verified_data if isinstance(item, dict)]
    except Exception as e:
        verified_data = anecdotes
        print(f"    [!] Failed to parse verification JSON: {e}")
    
    save_json(f"{output_dir}/anecdotes_verified.json", verified_data)
    
    # Save a verification report
    report = "# ANECDOTE VERIFICATION REPORT\n\n"
    for item in verified_data:
        report += f"## {item.get('anecdote')}\n"
        report += f"- **Status:** {item.get('confidence')}\n"
        report += f"- **Verified Narrative:** {item.get('verified_text')}\n"
        report += f"- **Source:** {item.get('source')}\n"
        if item.get('correction_notes'):
            report += f"- **Notes:** {item.get('correction_notes')}\n"
        report += "\n"
    
    save_text(f"{output_dir}/verification_report.md", report)
    return verified_data
