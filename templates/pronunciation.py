"""
Track C — PronunciationRegistry.

Canonical-spelling lock table. ElevenLabs v2 voices do NOT reliably support
SSML <phoneme> tags — they leak as literal noise in the audio. So this table
maps each canonical spelling to itself (canonical lock only) and does NOT
inject phoneme SSML. ElevenLabs handles these names natively in v2 voices.

If/when the engine moves to v3 (SSML-capable), re-introduce phoneme tags here.
"""

PRONUNCIATION = {
    "joga bonito": "joga bonito",
    "Vinícius":    "Vinícius",
    "Suárez":      "Suárez",
    "Atlético":    "Atlético",
    "Ronaldinho":  "Ronaldinho",
    "Guardiola":   "Guardiola",
    "Müller":      "Müller",
    "Özil":        "Özil",
    "Rodríguez":   "Rodríguez",
    "Cristiano":   "Cristiano",
}

CANONICAL_SPELLINGS = set(PRONUNCIATION.keys())
