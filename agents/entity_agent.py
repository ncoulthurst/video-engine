from utils.llm_utils import ask_llm

def extract_entities(topic):
    print(f"[*] Entity Agent identifying subjects from: {topic}")
    prompt = f"""
    Extract the primary football player(s) or entity(s) from this video title idea: "{topic}".
    
    RULES:
    1. If it is a single-person documentary, return just the name (e.g. "Wayne Rooney").
    2. If it is a comparison or list, return a comma-separated list of ALL relevant names (e.g. "Erling Haaland, Sergio Aguero, Harry Kane").
    3. Return ONLY the names, no extra text.
    """
    entities = ask_llm(prompt).strip()
    print(f"    -> Identified: {entities}")
    return entities
