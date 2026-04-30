# The Documentary Engine

> Type a video title. Get a broadcast-quality football documentary.

[REPLACE: hero screenshot or animated GIF of the pipeline running end-to-end]

## What it does

The Documentary Engine takes a single line — `"Luis Suárez: The Complete Story"` — and produces a fully-structured football documentary package: a five-act narrative script, animated infographic graphics rendered through a Remotion (React) project, ElevenLabs voiceover, a sourcing sheet of every match clip the editor needs to find, and a timeline UI for assembling the final cut.

It is a personal R&D project that wires together a chain of specialised LLM agents — research, scripting, storyboarding, narration, graphics, production — behind a Flask web UI. Each stage is editable: you can adjust the director's brief, tick which moments to include, reorder scenes, re-render single graphics with prop tweaks, splice clips on a Premiere-style timeline, and export a final video.

The two halves are decoupled by design. Python decides **what** to make. Remotion (a separate project) decides **how it looks**.

## Demo

[REPLACE: YouTube/Loom link to a recorded walkthrough]

## Pipeline flow

```
[User: Title + Director's Brief]
          │
          ▼
Step 1 ── Title input (/)
          │   entity_agent extracts subject; research_agent fetches Wikipedia + Google News
          │   LLM generates Director's Brief context
          ▼
Step 2 ── Context + Checklist (/context)
          │   _extract_context_facts() reads brief → structured fact checklist
          │   User edits facts, checks moments to include, enables/disables voiceover
          │   Writes context.md + facts.md to output/<safe_name>/
          ▼
Step 3 ── Blueprint (/blueprint inline)
          │   LLM generates act-by-act scene blueprint (checked facts injected as MUST INCLUDE)
          │   User reviews act structure, adjusts emphasis
          ▼
Step 4 ── Storyboard Editor (/storyboard inline)
          │   script_agent generates full 70–90 scene storyboard (5-act structure)
          │   User can add/remove/reorder scenes in the drag-drop editor
          │   "Run pipeline" button chains all agents sequentially via orchestrator.py
          ▼
[Pipeline executes:]
          │   1. script_agent     → script_draft.md (full narrated script with tags)
          │   2. narration_agent  → narration.md (TTS-clean script, sent to ElevenLabs)
          │   3. graphics_agent   → renders each tag → output/<name>/renders/*.mp4
          │   4. production_agent → clips_needed.json + production_sheet.md
          ▼
Step 5 ── Live Pipeline Log (streaming output in browser)
          │
          ▼
[Review UI — two interfaces:]
          │
          ├── /studio/<safe_name>  — Grid view: review renders, approve/reject, edit props, re-render
          └── /edit/<safe_name>    — Timeline editor: reorder across 4 tracks, splice clips,
                                     drag graphics, add transitions, export
```

### Output directory per project

```
output/<safe_name>/
  context.md           — Director's brief + settings (SKIP_VOICEOVER flag)
  facts.md             — Structured fact checklist
  script_draft.md      — Full script with all tags
  narration.md         — TTS-clean script (sent to ElevenLabs)
  clips_needed.json    — Structured clip sourcing list
  production_sheet.md  — Human-readable clip sourcing guide
  renders/             — All auto-rendered .mp4 graphics
    manifest.json      — props + metadata for every rendered file
  studio_state.json    — Per-render approve/reject/note state
  timeline_state.json  — Saved timeline editor order
  export/              — Approved renders copied here on export
  clips/               — User-placed footage clips (sourced manually)
```

## Architecture

The engine is intentionally centralised — one large Flask server hosts the UI and orchestration, a folder of specialised agents generates content, and a separate Remotion project handles all video rendering.

### Agents (`agents/`)

| File | Role |
|------|------|
| `script_agent.py` | Gemini 2.5: generates full 5-act script with all visual tags |
| `storyboard_agent.py` | Generates the scene-by-scene plan; classifies each scene |
| `graphics_agent.py` | Parses tags from script; dispatches Remotion renders |
| `narration_agent.py` | Strips tags from script; sends clean text to ElevenLabs TTS |
| `production_agent.py` | Extracts CLIP + GRAPHIC tags; writes sourcing sheet |
| `research_agent.py` | Wikipedia + Guardian + Google News fetcher |
| `entity_agent.py` | Extracts the subject(s) from the title |
| `radar_agent.py` | Per-90 FBref stats via soccerdata; positional percentile ranks |
| `data_agent.py` / `analysis_agent.py` | Structured stat lookups + interpretation |
| `motion_agent.py` | Generative fallback: writes a bespoke Remotion TSX component when no template fits |
| `player_image_agent.py` | Fetches transparent-background player PNGs (Futwiz + Wikipedia Commons) |
| `anecdote_agent.py` / `anecdote_verification_agent.py` | Sources + fact-checks colour-piece moments |
| `fact_check_agent.py` | Verifies stat claims before they enter the script |
| `curiosity_agent.py` / `retention_agent.py` | Tunes hook strength and pacing |
| `marketing_agent.py` | Generates title, thumbnail copy, description |
| `shorts_agent.py` | Slices script for short-form variants |
| `style_agent.py` / `sync_agent.py` / `source_agent.py` / `trend_agent.py` | Supporting passes |

### Utilities (`utils/`)

| File | Role |
|------|------|
| `remotion_renderer.py` | All `render_*()` functions; shells out to Remotion CLI; team color/badge maps |
| `football_data_api.py` | FBref match data, lineups, standings via soccerdata |
| `formation_validator.py` | Validates LLM-generated formations against canonical layouts |
| `llm_utils.py` | `ask_llm()` (Groq Llama 70B) + `ask_gemini()` (Gemini 2.5 Flash Lite) |
| `bracket_data.py` | Tournament bracket data shapes |
| `file_utils.py` / `format_utils.py` / `search_utils.py` | Misc helpers |

### Templates (`templates/`)

| File | Role |
|------|------|
| `visual_grammar.md` | Source of truth for all `[TAG: content]` formats |
| `design_system.md` | Typography, color palette, spacing for all graphics |
| `style_rules.md` | LLM writing rules extracted from channel style analysis |
| `templateRegistry.py` | Whitelist + data-kind mapping per Remotion composition |
| `narration_profile.py` | Sentence rhythm + forbidden token rules for narration |
| `pronunciation.py` | Canonical → SSML phoneme registry |
| `motion_signature.json` | Per-component animation signature |
| `format_profiles.json` | Output format presets (long-form, shorts, portrait) |

## Tag system

Graphics are emitted by the script as `[TAG: content]` markers. `graphics_agent` parses them and dispatches to Remotion. The full grammar lives in `templates/visual_grammar.md` — selected examples:

```
[HERO INTRO: Video Title]                        → HeroIntro (always scene 1)
[CAREER TIMELINE: Player - Focus: ClubName]      → HeroCareerTimeline
[PLAYER TRIO: the debate, P1 vs P2 vs P3]        → HeroPlayerRevealTrio (mandatory ACT 3)
[PLAYER RADAR: Player, Club, Competition, Season]→ AttackingRadar
[HERO FORM RUN: Team, label]                     → HeroFormRun
[TEAM LINEUP: Team N-N-N vs Opp, DD Mon YYYY]    → TeamLineup
[TRANSITION: type]                               → letterbox/push/grain/paper/dataLine/flash
[CLIP SINGLE: description, Xs, label]            → footage placeholder (manual sourcing)
[HERO BIG STAT: stat, unit, label, context]     → HeroBigStat
[PLAYER STATS: Player Name YYYY/YY]              → PlayerStats (season card only)
[TOP SCORERS: Competition YYYY/YY]               → TopScorersTable
```

## Tech stack

**Python engine**
- Flask — web server, all UI HTML/CSS/JS inline in `server.py`
- Groq Llama 3.3 70B — fast/free LLM for research, data, analysis
- Google Gemini 2.5 Flash Lite — creative generation (script, storyboard)
- BeautifulSoup4 — Google News scraping
- soccerdata — FBref integration
- ElevenLabs — TTS narration (skippable via `SKIP_VOICEOVER: true`)
- Pandas / NumPy / SciPy — stats

**Remotion (separate React/TypeScript project, not in this repo)**
- Remotion v4 — video rendering via CLI subprocess
- @remotion/transitions — TransitionSeries + 6 transition types
- Nivo / D3 — data visualisations
- Framer Motion — animations
- Zod — schema validation on every composition

## Setup

### 1. Clone

```bash
git clone https://github.com/[REPLACE: your-username]/the-documentary-engine.git
cd the-documentary-engine
```

### 2. Python environment

```bash
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. API keys

```bash
cp .env.example .env
# Edit .env and fill in your keys
```

You need at minimum: `GROQ_API_KEY`, `GOOGLE_API_KEY`, `FOOTBALL_DATA_API_KEY`. Voiceover (`ELEVENLABS_*`) is optional — set `SKIP_VOICEOVER: true` in any project's `context.md` to bypass it.

### 4. Remotion rendering project

The graphics renderer is a **separate Remotion project** that is not bundled in this repo. The engine shells out to it via the `REMOTION_PROJECT_PATH` environment variable, which must point at a Remotion v4 project that exposes the compositions referenced in `templates/visual_grammar.md` (`HeroIntro`, `HeroCareerTimeline`, `HeroPlayerRevealTrio`, etc.).

Set the path in `.env`:

```
REMOTION_PROJECT_PATH=/absolute/path/to/your/remotion/project
```

[REPLACE: link to a companion Remotion project repo if/when one is published]

### 5. Run the server

```bash
python3 server.py
# Open http://localhost:5000
```

## Status

This is a personal R&D project — not production-ready, not a packaged tool, not actively maintained as a product. There are sharp edges:

- The pipeline assumes the football documentary domain (player careers, club histories). Other genres would need new templates and prompt scaffolding.
- The Remotion project that pairs with the engine is not included here. You would need to author your own compositions matching the tag system in `templates/visual_grammar.md`, or adapt the renderer dispatch in `utils/remotion_renderer.py`.
- LLM costs add up. A full pipeline run uses Gemini and Groq generously across ~15 agent calls.
- `server.py` is one ~11k-line file. UI HTML, CSS, and JS are all inline. This is a deliberate trade — a single source of truth for the entire product UI — but it is not how a production app would be structured.

Released as MIT so others can lift pieces, study the agent chain, or fork and rebuild. See [LICENSE](./LICENSE).

## Author

Nathan Coulthurst — [REPLACE: portfolio URL]
