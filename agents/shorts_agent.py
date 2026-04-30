from utils.llm_utils import ask_llm
from utils.file_utils import save_text

def generate_shorts(script, output_dir):
    print(f"[*] Shorts Agent extracting viral clips...")
    prompt = f"""You are a YouTube Shorts specialist. Extract and rewrite 3 highly engaging, 60-second Shorts scripts from the documentary script below.

RULES:
- Each Short must open with a hook in the first 3 seconds (a shocking stat, a confrontational question, or a vivid scene drop).
- Optimise for vertical video: no [TACTICAL MAP] tags, only [CLIP:] and [STAT GRAPHIC:] visuals.
- Each Short must be self-contained — a viewer who hasn't seen the main video must still understand it.
- End each Short with a payoff, not a cliffhanger.
- Label them: SHORT 1, SHORT 2, SHORT 3.

DOCUMENTARY SCRIPT:
{script}"""
    res = ask_llm(prompt)
    save_text(f"{output_dir}/shorts_scripts.txt", res)
    return res