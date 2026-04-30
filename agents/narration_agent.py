import re
import os
import json
import base64
import requests
import xml.etree.ElementTree as ET
from utils.file_utils import load_text, save_text

# ── Track C — pronunciation registry import (compile-isolated) ──────────────
try:
    from templates.pronunciation import PRONUNCIATION
except Exception:
    PRONUNCIATION = {}


def pronunciation_replace(ssml_body: str) -> str:
    """Single-pass canonical → SSML <phoneme> rewrite. Case-insensitive,
    word-boundary. Idempotent — already-substituted tags are not re-matched
    because they no longer match the bare canonical string."""
    out = ssml_body
    for canonical, replacement in PRONUNCIATION.items():
        out = re.sub(rf"\b{re.escape(canonical)}\b", replacement, out, flags=re.I)
    return out


def ssml_validate(ssml: str) -> None:
    """Hard fail on malformed SSML — never let it degrade silently to default
    pronunciation. Wraps in <speak> if the caller hasn't, then parses."""
    wrapped = ssml if ssml.lstrip().startswith("<speak") else f"<speak>{ssml}</speak>"
    try:
        ET.fromstring(wrapped)
    except ET.ParseError as e:
        raise RuntimeError(f"SSML validation failed: {e}\nbody: {ssml[:200]!r}")
# ── End Track C SSML hooks ──────────────────────────────────────────────────

# All visual production tags used by the script agent
_TAG_PATTERN = re.compile(
    r'\[(?:CLIP\s*(?:SINGLE|COMPARE)?|STAT GRAPHIC|TACTICAL MAP|ARCHIVE PHOTO|B-ROLL|'
    r'STANDINGS TABLE|TOP SCORERS|TOP ASSISTS|PLAYER STATS|MATCH RESULT|'
    r'TRANSFER|TROPHY|CAREER TIMELINE|SEASON COMPARISON|TEAM LINEUP|'
    r'DISCIPLINARY RECORD|QUOTE CARD|PLAYER RADAR|PLAYER TRIO|ATTACKING RADAR|'
    r'TRANSITION|HERO[^\]]*)[^\]]*\]',
    re.IGNORECASE
)

# Markdown act headers e.g. "### ACT 1 — ORIGINS"
_HEADER_PATTERN = re.compile(r'^#{1,4}\s+.*$', re.MULTILINE)

# Section dividers inserted between acts
_DIVIDER_PATTERN = re.compile(r'^---+$', re.MULTILINE)

# (pause) → ElevenLabs SSML break
_PAUSE_PATTERN = re.compile(r'\(pause\)', re.IGNORECASE)

# Pronunciation normalisation — ensures ElevenLabs is consistent
_PRONUNCIATION_MAP = [
    (re.compile(r'\bSu[aá]rez\b', re.IGNORECASE), 'Suarez'),
    (re.compile(r'\bSu[aá]rez\'s\b', re.IGNORECASE), "Suarez's"),
]

ELEVENLABS_API_KEY = os.getenv("ELEVENLABS_API_KEY", "")
ELEVENLABS_VOICE_ID = os.getenv("ELEVENLABS_VOICE_ID", "UNtbwsQjlYBmZVAc3G7a")
ELEVENLABS_MODEL = "eleven_multilingual_v2"

# Strip bare "NARRATION:" prefixes the LLM sometimes emits (without square brackets)
_NARRATION_PREFIX = re.compile(r'^NARRATION:\s*', re.MULTILINE | re.IGNORECASE)


def _clean_for_elevenlabs(script: str) -> str:
    text = script
    # Strip all production/graphic tags
    text = _TAG_PATTERN.sub('', text)
    # Strip bare NARRATION: prefixes (no brackets — not caught by _TAG_PATTERN)
    text = _NARRATION_PREFIX.sub('', text)
    # Strip markdown headers and dividers
    text = _HEADER_PATTERN.sub('', text)
    text = _DIVIDER_PATTERN.sub('', text)
    # (pause) → long SSML break (used for major beats)
    text = _PAUSE_PATTERN.sub('<break time="1.2s" />', text)
    # Three-or-more blank lines = section/act boundary → longer breath (1.0s)
    text = re.sub(r'\n{4,}', '\n<break time="1.0s" />\n', text)
    # Standard paragraph break (two blank lines) → 0.7s breathing room.
    # 0.4s read too rushed and made narration feel AI; 0.7s gives a documentary cadence
    # without dragging.
    text = re.sub(r'\n{2,}', '\n<break time="0.7s" />\n', text)
    # Normalise pronunciations
    for pattern, replacement in _PRONUNCIATION_MAP:
        text = pattern.sub(replacement, text)
    # Collapse leftover blank lines
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


_MAX_CHARS = 9_500  # ElevenLabs hard limit is 10,000 — leave headroom


def _split_text(text: str) -> list[str]:
    """Split narration at paragraph/sentence boundaries to stay under _MAX_CHARS."""
    if len(text) <= _MAX_CHARS:
        return [text]
    chunks = []
    remaining = text
    while remaining:
        if len(remaining) <= _MAX_CHARS:
            chunks.append(remaining)
            break
        # Prefer splitting at a double newline, then ". ", then any space
        for sep in ('\n\n', '. ', ' '):
            pos = remaining.rfind(sep, 0, _MAX_CHARS)
            if pos != -1:
                cut = pos + len(sep)
                break
        else:
            cut = _MAX_CHARS
        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()
    return [c for c in chunks if c]


def _call_elevenlabs_chunk(text: str, headers: dict, voice_id: str | None = None) -> dict | None:
    """Call ElevenLabs for a single chunk. Returns parsed JSON or None."""
    vid = voice_id or ELEVENLABS_VOICE_ID
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{vid}/with-timestamps"
    payload = {
        "text": text,
        "model_id": ELEVENLABS_MODEL,
        "voice_settings": {
            "stability": 0.40,
            "similarity_boost": 0.75,
            "style": 0.0,
            "use_speaker_boost": True,
        },
    }
    response = requests.post(url, json=payload, headers=headers, timeout=180)
    if response.status_code != 200:
        print(f"    [!] ElevenLabs error {response.status_code}: {response.text[:300]}")
        return None
    return response.json()


def _call_elevenlabs(text: str, output_dir: str, voice_id: str | None = None) -> bool:
    """Call ElevenLabs API, chunking if needed. Saves narration.mp3 + timestamps.json."""
    if not ELEVENLABS_API_KEY or ELEVENLABS_API_KEY == "your_api_key_here":
        print("    [!] ELEVENLABS_API_KEY not set — skipping audio generation.")
        return False
    effective_voice_id = voice_id or ELEVENLABS_VOICE_ID
    if not effective_voice_id or effective_voice_id == "your_voice_id_here":
        print("    [!] ELEVENLABS_VOICE_ID not set — skipping audio generation.")
        return False

    print(f"    [*] ElevenLabs voice: {effective_voice_id}")
    headers = {"xi-api-key": ELEVENLABS_API_KEY, "Content-Type": "application/json"}
    chunks = _split_text(text)
    print(f"    [*] Calling ElevenLabs API ({len(text.split())} words, {len(chunks)} chunk(s))...")

    all_audio = b""
    merged_chars = []
    merged_start = []
    merged_end = []
    time_offset = 0.0

    for i, chunk in enumerate(chunks):
        print(f"    [*] Chunk {i+1}/{len(chunks)} — {len(chunk)} chars")
        data = _call_elevenlabs_chunk(chunk, headers, effective_voice_id)
        if not data:
            return False

        chunk_audio = base64.b64decode(data["audio_base64"])
        all_audio += chunk_audio

        alignment = data.get("alignment", {})
        chars  = alignment.get("characters", [])
        starts = alignment.get("character_start_times_seconds", [])
        ends   = alignment.get("character_end_times_seconds", [])

        merged_chars.extend(chars)
        merged_start.extend(t + time_offset for t in starts)
        merged_end.extend(t + time_offset for t in ends)

        # Advance time offset by the duration of this chunk's audio
        if ends:
            time_offset += ends[-1]

    # Save concatenated audio
    audio_path = f"{output_dir}/narration.mp3"
    with open(audio_path, "wb") as f:
        f.write(all_audio)
    print(f"    -> Saved: narration.mp3 ({len(all_audio) // 1024}kb)")

    # Save merged timestamps
    merged_alignment = {
        "characters": merged_chars,
        "character_start_times_seconds": merged_start,
        "character_end_times_seconds": merged_end,
    }
    timestamps_path = f"{output_dir}/timestamps.json"
    with open(timestamps_path, "w") as f:
        json.dump(merged_alignment, f, indent=2)
    print(f"    -> Saved: timestamps.json ({len(merged_chars)} characters mapped)")

    return True


def generate_narration(output_dir):
    print(f"[*] Narration Agent generating ElevenLabs script...")

    script = load_text(f"{output_dir}/script_draft.md")
    if not script:
        print("    [!] No script_draft.md found. Run script agent first.")
        return ""

    # Check if voiceover generation is disabled for this run
    voice_id_override = None
    context_path = os.path.join(output_dir, "context.md")
    if os.path.exists(context_path):
        import re as _re
        ctx_text = open(context_path).read()
        if "SKIP_VOICEOVER: true" in ctx_text:
            print("    [*] SKIP_VOICEOVER flag set — saving script only, skipping ElevenLabs API call.")
            narration = _clean_for_elevenlabs(script)
            save_text(f"{output_dir}/narration_elevenlabs.txt", narration)
            print(f"    -> Saved: narration_elevenlabs.txt ({len(narration.split())} words)")
            return narration

    narration = _clean_for_elevenlabs(script)
    # Track C: deterministic pronunciation lock + SSML validation gate.
    # pronunciation_replace rewrites canonical names to <phoneme> tags; ssml_validate
    # raises RuntimeError on malformed output so we never silently fall back to
    # ElevenLabs default pronunciation.
    narration = pronunciation_replace(narration)
    ssml_validate(narration)
    save_text(f"{output_dir}/narration_elevenlabs.txt", narration)
    print(f"    -> Saved: narration_elevenlabs.txt ({len(narration.split())} words)")

    _call_elevenlabs(narration, output_dir, voice_id=voice_id_override)

    return narration
