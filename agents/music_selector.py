"""
Music Selector Agent — picks a background music track for each act.

Reads the music/ folder (flat, semantic filenames) and asks Gemini to match
each act's mood to the most appropriate track.

Naming convention for music files:
  <mood>_<instrumentation>_<energy>.mp3
  e.g. tense_strings_slow.mp3, triumphant_brass_peak.mp3, legacy_piano_quiet.mp3

Called by production_agent.py; writes output/<name>/music_plan.json.
"""

import os
import json
from pathlib import Path
from utils.llm_utils import ask_gemini, ask_llm

MUSIC_DIR = Path(__file__).parent.parent / "music"

# Act mood profiles — used as fallback if LLM is unavailable or music dir is empty
_ACT_MOOD_KEYWORDS = {
    "COLD OPEN":          ["cinematic", "cold", "tense", "dark", "intro"],
    "ACT 1":              ["origins", "humble", "slow", "melancholic", "strings"],
    "ACT 2":              ["rise", "uplifting", "building", "orchestral", "momentum"],
    "ACT 3":              ["peak", "triumphant", "brass", "power", "glory"],
    "ACT 4":              ["dark", "chaos", "grain", "tense", "dramatic", "haunting"],
    "ACT 5":              ["legacy", "reflective", "quiet", "piano", "redemption"],
}


def _list_tracks(music_dir: Path) -> list[str]:
    """Return sorted list of .mp3 filenames in the music folder."""
    if not music_dir.exists():
        return []
    return sorted(
        f.name for f in music_dir.iterdir()
        if f.suffix.lower() == ".mp3" and not f.name.startswith(".")
    )


def _keyword_fallback(act_name: str, tracks: list[str]) -> str:
    """Pick best track by keyword matching when LLM is unavailable."""
    act_upper = act_name.upper()
    mood_key = next((k for k in _ACT_MOOD_KEYWORDS if k in act_upper), "ACT 1")
    keywords = _ACT_MOOD_KEYWORDS[mood_key]
    best = None
    best_score = -1
    for track in tracks:
        stem = track.replace(".mp3", "").replace("_", " ").lower()
        score = sum(1 for kw in keywords if kw in stem)
        if score > best_score:
            best_score = score
            best = track
    return best or tracks[0]


def select_track_for_act(act_name: str, act_summary: str, music_dir: Path = MUSIC_DIR) -> str | None:
    """
    Pick the best background music track for a given act.

    Args:
        act_name:    e.g. "ACT 3 — PEAK"
        act_summary: 1-2 sentence description of the act's emotional arc
        music_dir:   path to the flat music folder

    Returns:
        Filename (e.g. "triumphant_brass_peak.mp3") or None if no tracks available.
    """
    tracks = _list_tracks(music_dir)
    if not tracks:
        return None

    prompt = f"""You are scoring a football documentary. Pick the single best background music track
for this act from the list below. The filenames are semantic — read them as mood descriptors.

ACT: {act_name}
EMOTIONAL SUMMARY: {act_summary}

AVAILABLE TRACKS:
{chr(10).join(f"  - {t}" for t in tracks)}

Rules:
- Return ONLY the exact filename (e.g. "tense_strings_slow.mp3"), nothing else
- Match the emotional arc: cold opens need tension, peaks need triumph, act 5 needs reflection
- Avoid jarring mismatches (no triumphant brass for a dark/chaos act)
- If nothing is a great fit, pick the closest

Filename only:"""

    raw = (ask_gemini(prompt) or "").strip().strip('"').strip("'")

    # Validate response is an actual track name
    if raw in tracks:
        return raw

    # Fallback: keyword match
    return _keyword_fallback(act_name, tracks)


def build_music_plan(script_text: str, output_dir: str, music_dir: Path = MUSIC_DIR) -> list[dict] | None:
    """
    Select a music track for each act in the script and write music_plan.json.

    Returns the plan list, or None if no music folder / no tracks found.
    """
    tracks = _list_tracks(music_dir)
    if not tracks:
        print("    [music] No tracks found in music/ — skipping music plan")
        return None

    print(f"    [music] {len(tracks)} tracks available: {', '.join(tracks)}")

    # Extract act summaries from the script (simple: grab act header + first 200 chars)
    import re
    act_re = re.compile(r"^###\s+(.+)$", re.MULTILINE)
    headers = list(act_re.finditer(script_text))

    plan = []
    for i, m in enumerate(headers):
        act_name = m.group(1).strip()
        start = m.end()
        end = headers[i + 1].start() if i + 1 < len(headers) else len(script_text)
        act_body = script_text[start:end].strip()[:300]  # first 300 chars as summary

        track = select_track_for_act(act_name, act_body, music_dir)
        if track:
            plan.append({
                "act":    act_name,
                "track":  track,
                "path":   str(music_dir / track),
                "volume": 0.18,
            })
            print(f"    [music] {act_name} → {track}")

    if plan:
        plan_path = Path(output_dir) / "music_plan.json"
        with open(plan_path, "w") as f:
            json.dump(plan, f, indent=2)
        print(f"    [music] Saved music_plan.json ({len(plan)} acts)")

    return plan or None
