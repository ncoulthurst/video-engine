import os
import re
import sys
import json
import time
import requests
from groq import Groq

GROQ_MODEL = "llama-3.3-70b-versatile"
GEMINI_MODEL = "gemini-2.5-flash-lite"


def _load_env():
    env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
    if os.path.exists(env_path):
        with open(env_path, "r") as f:
            for line in f:
                line = line.strip()
                if "=" in line and not line.startswith("#"):
                    key, val = line.split("=", 1)
                    os.environ.setdefault(key, val)

_load_env()


def ask_gemini(prompt):
    """Gemini 2.5 Flash — high quality creative writing (script agent) and Groq fallback."""
    api_key = os.environ.get("GOOGLE_API_KEY")
    if not api_key:
        print("    [!] GOOGLE_API_KEY not found.")
        sys.exit(1)

    url = (
        f"https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_MODEL}:generateContent?key={api_key}"
    )
    payload = {"contents": [{"parts": [{"text": prompt}]}]}

    import re
    for attempt in range(5):
        try:
            resp = requests.post(url, json=payload, timeout=120)
            data = resp.json()

            if "error" in data:
                msg = data["error"].get("message", "Unknown error")
                code = data["error"].get("code", 0)
                print(f"    [!] Gemini API error ({code}): {msg}")
                if code == 429:
                    wait = 15
                    match = re.search(r"retry in ([\d.]+)s", msg)
                    if match:
                        wait = int(float(match.group(1))) + 2
                    print(f"    [!] Gemini rate limit. Waiting {wait}s... (attempt {attempt+1}/5)")
                    time.sleep(wait)
                    continue
                if attempt < 2:
                    time.sleep(5)
                    continue
                print("    [!] Gemini fatal error — returning empty.")
                return ""

            raw = data["candidates"][0]["content"]["parts"][0]["text"]
            # Strip markdown code fences Gemini sometimes wraps JSON in
            raw = re.sub(r'^```(?:json)?\s*', '', raw.strip(), flags=re.IGNORECASE)
            raw = re.sub(r'\s*```$', '', raw.strip())
            return raw

        except (KeyError, IndexError):
            # Candidates blocked or empty — treat as empty response
            print("    [!] Gemini returned no content (blocked/empty)")
            return ""
        except Exception as e:
            print(f"    [!] Gemini request error: {e}")
            if attempt < 2:
                time.sleep(5)
                continue
            return ""

    print("    [!] Gemini exhausted all retries.")
    return ""


def ask_llm(prompt, expect_json=False):
    """Groq / Llama — fast and free for research, data, and analysis agents.
    Automatically falls back to Gemini if the daily token limit is reached."""
    api_key = os.environ.get("GROQ_API_KEY")
    if not api_key:
        print("    [!] GROQ_API_KEY not found. Using Gemini.")
        return ask_gemini(prompt)

    client = Groq(api_key=api_key)
    kwargs = {
        "model": GROQ_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.7,
        "max_tokens": 8000 if not expect_json else 2048,
    }
    if expect_json:
        kwargs["response_format"] = {"type": "json_object"}

    try:
        completion = client.chat.completions.create(**kwargs)
        return completion.choices[0].message.content
    except Exception as e:
        err = str(e)
        if "429" in err or "rate_limit" in err.lower():
            print("    [!] Groq rate limit hit. Falling back to Gemini...")
            return ask_gemini(prompt)
        if "json_validate_failed" in err or "text_too_long" in err:
            # JSON mode failed (LLM produced invalid JSON) — retry without json_object constraint
            print(f"    [!] Groq JSON validation failed — retrying as plain text")
            kwargs.pop("response_format", None)
            try:
                completion = client.chat.completions.create(**kwargs)
                return completion.choices[0].message.content
            except Exception as e2:
                print(f"    [!] Groq retry failed: {e2}. Falling back to Gemini...")
                return ask_gemini(prompt)
        print(f"    [!] Groq API error: {e}. Falling back to Gemini...")
        return ask_gemini(prompt)


# ---------------------------------------------------------------------------
# _cached_infer — factual inference with strict grounding + result cache
# ---------------------------------------------------------------------------

_INFER_CACHE: dict = {}


def _cached_infer(query: str, expected_type: str = "list", fallback=None):
    """Ask the LLM a factual inference question using only widely recognised context.

    Returns:
        list  — if expected_type == "list" and model is confident
        str   — if expected_type == "str"  and model is confident
        fallback (default None) — if model is uncertain or output is invalid

    Args:
        query:         The factual question (e.g. "Name the main rivals of
                       Liverpool FC in the 2013/14 Premier League season")
        expected_type: "list" | "str"
        fallback:      Value to return on uncertainty / parse failure
    """
    cache_key = f"{expected_type}:{query}"
    if cache_key in _INFER_CACHE:
        return _INFER_CACHE[cache_key]

    if expected_type == "list":
        format_instruction = (
            'Respond ONLY with a JSON array of strings. '
            'Example: ["Item1", "Item2"]. '
            'If uncertain about ANY item, respond with exactly the word: NONE'
        )
    else:
        format_instruction = (
            'Respond ONLY with a short plain string (no JSON, no markdown). '
            'If uncertain, respond with exactly the word: NONE'
        )

    prompt = (
        "Based ONLY on widely recognised, publicly known context — not speculation "
        "or inference:\n\n"
        f"{query}\n\n"
        f"{format_instruction}"
    )

    raw = ask_llm(prompt, expect_json=(expected_type == "list"))
    if raw:
        raw = raw.strip()

    # Treat empty or explicit uncertainty as fallback
    if not raw or raw.upper() == "NONE":
        _INFER_CACHE[cache_key] = fallback
        return fallback

    if expected_type == "list":
        try:
            cleaned = re.sub(r'^```(?:json)?\s*', '', raw, flags=re.IGNORECASE)
            cleaned = re.sub(r'\s*```$', '', cleaned.strip())
            result = json.loads(cleaned)
            if not isinstance(result, list) or not result:
                _INFER_CACHE[cache_key] = fallback
                return fallback
            _INFER_CACHE[cache_key] = result
            return result
        except (json.JSONDecodeError, ValueError):
            _INFER_CACHE[cache_key] = fallback
            return fallback
    else:
        _INFER_CACHE[cache_key] = raw
        return raw
