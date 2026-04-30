"""
Track C — NarrationProfile.

Single source of truth for narration cadence, forbidden tokens, and length
targeting. Consumed by:
  - agents/script_agent.py: narration_post_processor()
  - the LLM prompt (10 NARRATION RULES block)
"""

NARRATION_PROFILE = {
    "voice_persona": (
        "BBC documentary narrator — measured, present-tense, leads the visual"
    ),
    "sentence_length": {
        "min_words": 1,                         # fragments allowed for rhythm ("Not slowly. All at once.")
        "max_words": 28,
        "target_avg": 14,
    },
    "rhythm": {
        "max_consecutive_long_sentences":     2,    # >20 words
        "min_short_sentences_per_act":        3,    # ≤8 words, used as stress beats
        "max_consecutive_fragments":          3,    # ≤3 words in a row — prevents fragment walls
        "comma_breath_max_per_sentence":      3,
    },
    # Regex patterns. Anything matching is hard-stripped from narration.
    "forbidden_tokens": [
        r"\b\d+(\.\d+)?\s*s\s*break\b",         # "0.4s break"
        r"\[BEAT\]",
        r"\[PAUSE\]",
        r"\[BREATH\]",
        r"\bpause for\b",
        r"\(beat\)",
        r"\bcut to\b",
        r"\bsmash cut\b",
    ],
    # Plain-text phrases. Case-insensitive substring strip.
    "forbidden_phrases": [
        "as you can see",
        "on screen",
        "this graphic shows",
        "as the chart shows",
        "look at this",
        "as shown",
    ],
    "tense": "present",
    "second_person": False,                     # never address viewer directly
    "broadcast_wpm": 156,                       # used by length targeting
    # ±tolerance applied to (duration_seconds * broadcast_wpm / 60) for length checks
    "length_tolerance": 0.20,
}
