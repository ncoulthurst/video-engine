# Music Library

Drop `.mp3` files here. The engine reads filenames as semantic descriptors to auto-select tracks per act.

## Naming convention

```
<mood>_<instrumentation>_<energy>.mp3
```

All lowercase, underscores. Examples:

| Filename | Used for |
|---|---|
| `cold_open_cinematic.mp3` | Cold open, title cards |
| `tense_strings_slow.mp3` | Origins / early struggle |
| `tense_strings_fast.mp3` | Defining event / chaos act |
| `dark_piano_haunting.mp3` | Dark turn, scandal, low point |
| `uplifting_orchestral_rise.mp3` | Rise act, breakthrough |
| `triumphant_brass_peak.mp3` | Peak act, glory, trophies |
| `melancholic_guitar.mp3` | Decline, reflection |
| `legacy_piano_quiet.mp3` | Act 5 legacy/redemption |
| `grain_dark_chaos.mp3` | Grain transition scenes |
| `paper_reflective_soft.mp3` | Paper transition / epilogue |

## Volume

All tracks are mixed at **18% volume** under narration (no ducking).
Override per-act in `output/<name>/music_plan.json` → `"volume"` field.
