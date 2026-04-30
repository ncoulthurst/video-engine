"""
Narration-Sync Agent
Reads timestamps.json (from ElevenLabs) + script_draft.md and produces sync_map.json:
  { "scenes": [ { scene_tag, content, narration_start_seconds, narration_sentence } ] }

This lets the timeline editor and export pipeline position graphics to land exactly when
the narrator mentions the corresponding subject, rather than playing back-to-back.

Usage:
    from agents.sync_agent import build_sync_map
    sync_map = build_sync_map(output_dir)
"""

import re
import json
from pathlib import Path

# ── Tag pattern (same as narration_agent._TAG_PATTERN) ────────────────────────
_TAG_RE = re.compile(
    r'\[(?:CLIP\s*(?:SINGLE|COMPARE)?|STAT GRAPHIC|TACTICAL MAP|ARCHIVE PHOTO|B-ROLL|'
    r'STANDINGS TABLE|TOP SCORERS|TOP ASSISTS|PLAYER STATS|MATCH RESULT|'
    r'TRANSFER|TROPHY|CAREER TIMELINE|SEASON COMPARISON|TEAM LINEUP|'
    r'DISCIPLINARY RECORD|QUOTE CARD|PLAYER RADAR|PLAYER TRIO|ATTACKING RADAR|'
    r'TRANSITION|HERO[^\]]*)[^\]]*\]',
    re.IGNORECASE
)

# Full generic tag — used to strip any remaining [TAG: content] from context sentences
_ANY_TAG_RE = re.compile(r'\[[A-Z][^\]]+\]', re.IGNORECASE)

# SSML break tags inserted by narration_agent
_SSML_RE = re.compile(r'<break\s[^>]+/>', re.IGNORECASE)

# Act headers  e.g. "### ACT 3 — PEAK"
_HEADER_RE = re.compile(r'^#{1,4}\s+', re.MULTILINE)

# How many characters to use as a search anchor when matching a sentence fragment
_ANCHOR_LEN = 28


def _strip_to_plain(text: str) -> str:
    """Strip tags, SSML breaks, and markdown headers to get plain narration text."""
    text = _ANY_TAG_RE.sub('', text)
    text = _SSML_RE.sub('', text)
    text = _HEADER_RE.sub('', text)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip()


def _build_char_time_index(timestamps: dict) -> list[float]:
    """
    Return a flat list where index i is the start time (seconds) of the i-th character
    in narration_elevenlabs.txt.  Missing entries default to the previous time.
    """
    chars  = timestamps.get("characters", [])
    starts = timestamps.get("character_start_times_seconds", [])
    times  = []
    last   = 0.0
    for i in range(len(chars)):
        t = starts[i] if i < len(starts) else last
        times.append(t)
        last = t
    return times


def _find_text_in_narration(query: str, narration: str, start_search: int = 0) -> int:
    """
    Return the char index in `narration` where `query` (or a strong prefix of it) starts.
    Returns -1 if not found.
    """
    q = query.strip()
    if not q:
        return -1

    # Try exact substring first
    idx = narration.find(q, start_search)
    if idx != -1:
        return idx

    # Try with a shorter anchor — first _ANCHOR_LEN non-whitespace chars
    words = q.split()
    # Build a series of progressively shorter anchors to find the best match
    for word_count in range(min(len(words), 8), 2, -1):
        anchor = ' '.join(words[:word_count])
        idx = narration.find(anchor, start_search)
        if idx != -1:
            return idx

    return -1


def _preceding_narration(lines: list[str], tag_line_idx: int) -> str:
    """
    Get the narration sentence(s) immediately before a tag line.
    Walk backwards from tag_line_idx, skipping blank lines and other tags,
    until we have 1 non-empty narration sentence.
    """
    collected = []
    i = tag_line_idx - 1
    while i >= 0 and len(collected) < 3:
        line = lines[i].strip()
        if not line:
            i -= 1
            continue
        # Skip if this line is itself a tag or header
        if _ANY_TAG_RE.fullmatch(line) or _HEADER_RE.match(line) or line.startswith('#'):
            i -= 1
            continue
        # Strip any inline tags from the narration line
        clean = _strip_to_plain(line)
        if clean:
            collected.insert(0, clean)
            break  # One clean sentence is enough for a reliable anchor
        i -= 1
    return ' '.join(collected)


def build_sync_map(output_dir: str) -> dict:
    """
    Main entry point. Reads script_draft.md + narration_elevenlabs.txt + timestamps.json
    from output_dir.  Writes sync_map.json and returns the dict.

    sync_map structure:
    {
      "scenes": [
        {
          "index": 0,
          "tag": "HERO BIG STAT",
          "content": "31, goals, in a single Premier League season, ...",
          "act": "ACT 3 — PEAK",
          "narration_start_seconds": 45.3,
          "narration_sentence": "Suárez scored 31 goals that season.",
          "char_position": 1847
        },
        ...
      ],
      "total_narration_duration_seconds": 1200.0,
      "sync_coverage_pct": 87.5
    }
    """
    out = Path(output_dir)

    script_path      = out / "script_draft.md"
    narration_path   = out / "narration_elevenlabs.txt"
    timestamps_path  = out / "timestamps.json"

    if not script_path.exists():
        print("    [!] Sync Agent: script_draft.md not found")
        return {}
    if not narration_path.exists():
        print("    [!] Sync Agent: narration_elevenlabs.txt not found — run narration agent first")
        return {}
    if not timestamps_path.exists():
        print("    [!] Sync Agent: timestamps.json not found — ElevenLabs API needed for sync")
        # Still produce a partial sync_map with estimated times
        return _build_estimated_sync_map(output_dir)

    script_text   = script_path.read_text(encoding="utf-8")
    narration_txt = narration_path.read_text(encoding="utf-8")
    timestamps    = json.loads(timestamps_path.read_text(encoding="utf-8"))

    char_times = _build_char_time_index(timestamps)
    total_duration = char_times[-1] if char_times else 0.0

    lines      = script_text.splitlines()
    scenes     = []
    act_current = "COLD OPEN"
    search_pos = 0   # advance search position to avoid matching same sentence twice
    synced     = 0

    act_map = {
        "cold open": "COLD OPEN",
        "act 1": "ACT 1 — ORIGINS",   "origins": "ACT 1 — ORIGINS",
        "act 2": "ACT 2 — RISE",       "rise":    "ACT 2 — RISE",
        "act 3": "ACT 3 — PEAK",       "peak":    "ACT 3 — PEAK",
        "act 4": "ACT 4 — THE DEFINING EVENT", "defining": "ACT 4 — THE DEFINING EVENT",
        "act 5": "ACT 5 — REDEMPTION AND LEGACY", "redemption": "ACT 5 — REDEMPTION AND LEGACY",
        "legacy": "ACT 5 — REDEMPTION AND LEGACY",
    }

    for line_idx, line in enumerate(lines):
        stripped = line.strip()

        # Track current act
        lower = stripped.lower().strip("# -—")
        for kw, act_name in act_map.items():
            if kw in lower and stripped.startswith("#"):
                act_current = act_name
                break

        # Find tags in this line
        for m in _TAG_RE.finditer(line):
            full_tag = m.group(0)
            # Extract tag name and content
            inner = full_tag[1:-1]  # strip [ ]
            colon_pos = inner.find(':')
            if colon_pos != -1:
                tag_name = inner[:colon_pos].strip().upper()
                tag_content = inner[colon_pos + 1:].strip()
            else:
                tag_name    = inner.strip().upper()
                tag_content = ""

            # Skip pure TRANSITION tags — they don't need narration sync
            if tag_name == "TRANSITION":
                continue

            # Get the narration sentence before this tag
            narr_sentence = _preceding_narration(lines, line_idx)

            # Find it in the narration text
            narr_start_sec = None
            char_pos       = -1

            if narr_sentence:
                # Try to find a 20-char anchor from the sentence
                anchor = narr_sentence[:_ANCHOR_LEN].strip()
                char_pos = _find_text_in_narration(anchor, narration_txt, search_pos)
                if char_pos == -1 and len(narr_sentence) > 10:
                    # Fallback: try from the beginning in case search_pos overshot
                    char_pos = _find_text_in_narration(anchor, narration_txt, 0)

                if char_pos != -1 and char_pos < len(char_times):
                    narr_start_sec = char_times[char_pos]
                    # Advance search position to avoid re-matching earlier text
                    search_pos = max(search_pos, char_pos)
                    synced += 1

            scenes.append({
                "index":                    len(scenes),
                "tag":                      tag_name,
                "content":                  tag_content,
                "act":                      act_current,
                "narration_start_seconds":  round(narr_start_sec, 3) if narr_start_sec is not None else None,
                "narration_sentence":       narr_sentence,
                "char_position":            char_pos if char_pos != -1 else None,
            })

    total = len(scenes)
    coverage = round(100 * synced / total, 1) if total > 0 else 0.0

    sync_map = {
        "scenes":                          scenes,
        "total_narration_duration_seconds": round(total_duration, 3),
        "sync_coverage_pct":               coverage,
    }

    out_path = out / "sync_map.json"
    out_path.write_text(json.dumps(sync_map, indent=2, ensure_ascii=False))
    print(f"    -> sync_map.json: {total} graphic scenes, {synced} synced ({coverage}% coverage), "
          f"{round(total_duration)}s total narration")
    return sync_map


def _build_estimated_sync_map(output_dir: str) -> dict:
    """
    Fallback when timestamps.json doesn't exist (SKIP_VOICEOVER=true).
    Estimates graphic timing based on narration word count per scene.
    Assumes average speaking pace of 145 words per minute.
    """
    out = Path(output_dir)
    script_path    = out / "script_draft.md"
    narration_path = out / "narration_elevenlabs.txt"

    if not script_path.exists():
        return {}

    script_text = script_path.read_text(encoding="utf-8")

    # Estimate total narration duration from word count
    narr_text = narration_path.read_text(encoding="utf-8") if narration_path.exists() else _strip_to_plain(script_text)
    word_count = len(narr_text.split())
    total_duration = word_count / 145 * 60  # 145 wpm → seconds

    lines = script_text.splitlines()
    scenes = []
    act_current = "COLD OPEN"
    narr_words_so_far = 0

    act_map_keys = ["cold open", "act 1", "origins", "act 2", "rise",
                    "act 3", "peak", "act 4", "defining", "act 5", "redemption", "legacy"]
    act_labels = {
        "cold open": "COLD OPEN", "act 1": "ACT 1 — ORIGINS", "origins": "ACT 1 — ORIGINS",
        "act 2": "ACT 2 — RISE", "rise": "ACT 2 — RISE",
        "act 3": "ACT 3 — PEAK", "peak": "ACT 3 — PEAK",
        "act 4": "ACT 4 — THE DEFINING EVENT", "defining": "ACT 4 — THE DEFINING EVENT",
        "act 5": "ACT 5 — REDEMPTION AND LEGACY",
        "redemption": "ACT 5 — REDEMPTION AND LEGACY", "legacy": "ACT 5 — REDEMPTION AND LEGACY",
    }

    for line_idx, line in enumerate(lines):
        stripped = line.strip()
        lower = stripped.lower().strip("# -—")
        for kw in act_map_keys:
            if kw in lower and stripped.startswith("#"):
                act_current = act_labels.get(kw, act_current)
                break

        # Count narration words BEFORE this tag
        for m in _TAG_RE.finditer(line):
            full_tag = m.group(0)
            inner = full_tag[1:-1]
            colon_pos = inner.find(':')
            tag_name    = inner[:colon_pos].strip().upper() if colon_pos != -1 else inner.strip().upper()
            tag_content = inner[colon_pos + 1:].strip()     if colon_pos != -1 else ""

            if tag_name == "TRANSITION":
                continue

            estimated_sec = (narr_words_so_far / word_count * total_duration) if word_count > 0 else 0.0
            scenes.append({
                "index": len(scenes),
                "tag": tag_name,
                "content": tag_content,
                "act": act_current,
                "narration_start_seconds": round(estimated_sec, 1),
                "narration_sentence": "",
                "char_position": None,
                "estimated": True,
            })

        # Accumulate narration words on this line (excluding tags)
        plain = _strip_to_plain(line)
        narr_words_so_far += len(plain.split())

    sync_map = {
        "scenes": scenes,
        "total_narration_duration_seconds": round(total_duration, 1),
        "sync_coverage_pct": 0.0,
        "estimated": True,
    }

    out_path = out / "sync_map.json"
    out_path.write_text(json.dumps(sync_map, indent=2, ensure_ascii=False))
    print(f"    -> sync_map.json (estimated, no timestamps.json): {len(scenes)} scenes, "
          f"~{round(total_duration)}s narration")
    return sync_map


if __name__ == "__main__":
    import sys
    if len(sys.argv) < 2:
        print("Usage: python3 sync_agent.py <output_dir>")
        sys.exit(1)
    result = build_sync_map(sys.argv[1])
    print(f"Done: {len(result.get('scenes', []))} scenes synced")
