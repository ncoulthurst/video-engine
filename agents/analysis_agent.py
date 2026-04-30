from utils.llm_utils import ask_llm
from utils.file_utils import save_text

def analyze_data(entity, data, output_dir):
    print(f"[*] Analysis Agent generating insights for: {entity}")
    prompt = f"Convert these raw stats into 3 compelling, data-driven narrative insights for a YouTube script: {data}"
    res = ask_llm(prompt)
    save_text(f"{output_dir}/analysis.md", res)
    return res