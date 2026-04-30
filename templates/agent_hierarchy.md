# Agent Authority Hierarchy

This document defines what each agent is allowed to decide, and what it must defer to the next layer.
Crossing these boundaries introduces inconsistency. When in doubt, push enforcement down the hierarchy.

---

## Hierarchy (top = highest authority)

```
retention_agent  →  storyboard_agent  →  server.py  →  graphics_agent  →  motion_agent
```

---

## retention_agent

**Owns:**
- `contrast_frame` — the before/after loop sentence that recurs at every act break
- `anchor_character` — the one person whose arc IS the documentary's arc
- `closing_question` — the final unanswered provocation (never a conclusion)
- `act_reframes` — what question each act must answer, and the payoff

**Does NOT decide:**
- Which specific scenes to generate (that is storyboard_agent's job)
- Which templates to use (that is graphics_agent's job)
- Scene order, duration, or clip type

**Output flows into:** storyboard_agent prompt as mandatory constraints

---

## storyboard_agent

**Owns (Layer 1 — narrative intent):**
- Narrative beats — what happens in each scene, in what order
- Scene intent — the emotional or informational purpose of each scene
- Rough visual type — clip / graphic / narration (suggestion only, not final)
- `visual_rationale` — why this visual choice serves the story at this moment
- `hero_visual` flag — marks scenes that merit a bespoke motion_agent render

**Does NOT decide:**
- Final clip type (CLIP SINGLE vs CLIP COMPARE) — server.py decides
- Role assignment (`anchor`, `evidence`, `emotional_beat`, etc.) — server.py decides
- `world_id` grouping — server.py decides
- `flow_hint` / `flow_direction` — server.py decides
- Template caps or act-opener variety enforcement — server.py decides

**Output flows into:** server.py Layer 2 post-processing

---

## server.py (Layer 2 enforcement)

**Owns:**
- Final clip type resolution (by role)
- Role assignment (first→context, strongest ACT3→anchor, last→emotional_beat, etc.)
- `world_id` grouping via era detection
- `flow_hint` (same world→evolve, act boundary→cut, default→push)
- `flow_direction` via ACT_DIRECTIONS map; inverted for emotional_beat
- `_reconcile_format` — trim-only budget enforcement against format profile
- `_enforce_template_caps` — global + consecutive cap; logs diversity_score
- `_validate_act_openers` — no two consecutive acts open with same template
- `_inject_hero_visuals` — marks 1–3 hero_visual scenes (ACT3 mandatory)
- `_inject_missing_context_moments` — uses `_cached_infer` to find gaps vs Director's Brief
- `transitionDirection` — wires flow_direction into Remotion VideoSequence scenes

**Does NOT decide:**
- What the narrative content of scenes should be (storyboard_agent owns that)
- How to render a template (graphics_agent owns that)
- Template visual design (design_system.md owns that)

**Layer 2 post-processing order (must not change):**
1. `_assign_scene_metadata` — roles, world_id, clip types, flow_hint, flow_direction
2. `_enforce_template_caps` — global + consecutive cap, diversity_score log
3. `_validate_act_openers` — act-opener variety
4. `_inject_hero_visuals` — hero_visual flags
5. `_reconcile_format` — trim to format budget

---

## graphics_agent

**Owns:**
- Rendering decisions — which render function handles which template tag
- Template caps enforcement at render time (secondary, server.py is primary)
- `_validate_visual_identity` — accent colour drift check, motion_signature compliance
- `_render_hero_visuals` — calls motion_agent for hero_visual scenes, fallback to closest template
- Manifest construction (`manifest.json`)

**Does NOT decide:**
- Which scenes exist or their order (server.py / storyboard_agent own that)
- Template content (the storyboard scene's `content` field drives this)
- Narrative structure (retention_agent owns that)

---

## motion_agent

**Owns:**
- Bespoke TSX component generation for hero_visual scenes
- Custom animation logic for generated components
- Registering generated components in `src/gen/index.ts`

**Does NOT decide:**
- When to generate (graphics_agent decides via hero_visual flag)
- What the scene content is (passed in from storyboard scene)
- Visual identity rules (motion_signature.json and design_system.md define those)

**Two invocation modes:**
- **Proactive** — called for scenes where `hero_visual: true` (flagged by server.py)
- **Reactive** — called when template cap is exceeded and no alternative template exists

**Guarantee:** At least 1 motion_agent render per video.

---

## _cached_infer (utility, not an agent)

Replaces all hardcoded domain logic (team rivalries, era peers, player comparisons).

- Strict prompt: "Based ONLY on widely recognised context"
- Returns `None` / fallback if uncertain — never hallucinates
- Caches results in-memory per session
- Used by: `_inject_missing_context_moments`, rival/peer inference in storyboard post-processing

---

## What belongs where — quick reference

| Decision | Owner |
|----------|-------|
| Narrative arc and loop | retention_agent |
| Scene content and intent | storyboard_agent |
| Final clip type | server.py |
| Scene roles | server.py |
| world_id | server.py |
| flow_hint / flow_direction | server.py |
| Format budget enforcement | server.py |
| Template diversity | server.py |
| Rendering each template | graphics_agent |
| Identity validation | graphics_agent |
| Bespoke hero visuals | motion_agent |
| Domain fact inference | _cached_infer |
