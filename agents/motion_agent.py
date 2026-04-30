"""
Generative Motion Graphics Agent
When a scene description doesn't fit any existing template, this agent generates a
bespoke Remotion TSX component via Gemini and registers it dynamically.

Usage:
    from agents.motion_agent import generate_motion_graphic
    result = generate_motion_graphic(scene_description, props_hint, output_dir)

Returns:
    {
        "composition_id": "GEN_abc123",
        "tsx_path": "<REMOTION_PROJECT_PATH>/src/gen/GEN_abc123.tsx",
        "props": { ... },
        "rendered_path": "output/.../renders/gen_abc123.mp4",
        "status": "rendered" | "generated" | "failed",
        "used_template": "HeroBigStat" | None  # if existing template was good enough
    }
"""

import hashlib
import json
import os
import re
import time
from pathlib import Path

from utils.llm_utils import ask_gemini
from utils.remotion_renderer import _render as remotion_render

REMOTION_PROJECT = Path(
    os.environ.get(
        "REMOTION_PROJECT_PATH",
        str(Path(__file__).parent.parent.parent / "remotiontest"),
    )
).resolve()
REMOTION_SRC  = REMOTION_PROJECT / "src"
GEN_DIR       = REMOTION_SRC / "gen"
GEN_INDEX     = GEN_DIR / "index.ts"

# ── Known existing templates — agent checks these first ──────────────────────

EXISTING_TEMPLATES = [
    ("HeroBigStat",       "One large stat number with unit and context label. Use for records, milestones, award counts."),
    ("HeroFormRun",       "Sequence of W/D/L match results as coloured squares. Use for form runs, title races."),
    ("HeroStatBars",      "Two side-by-side animated bar charts comparing stats. Use for head-to-head player comparison."),
    ("HeroTactical",      "Football pitch diagram with players and arrows. Use for tactical systems, pressing shapes."),
    ("HeroTransferRecord","Timeline of transfer fees as horizontal bars. Use for transfer market context."),
    ("HeroLeagueGraph",   "Line chart of league positions over a season. Use for title races, relegation battles."),
    ("HeroQuote",         "Full-screen pull quote with player image. Use for memorable spoken quotes."),
    ("HeroHeadlineStack", "Bold editorial headline lines revealing one by one. Use for punchy fact reveals."),
    ("HeroGoalRush",      "Season-by-season goal tally as rows counting up. Use for career goal record breakdown."),
    ("HeroPlayerRevealTrio", "Three full-height overlapping player portraits (Trio = canonical 3-player use, accepts 1–4). Use for multi-player reveals, NOT single-player — pick a different template for one player."),
    ("HeroContactSheet",  "Polaroid-style photo gallery. Use for historical/archival photo sequences."),
    ("CareerTimeline",       "Horizontal career club timeline with badges. Use for club transitions."),
    ("PlayerTrio",           "Three-player editorial comparison card. Use for 'the debate' peer comparison."),
    ("AttackingRadar",       "Radar chart showing per-90 stats percentiles. Use for single-season performance analysis."),
    ("TeamLineup",           "Tactical formation on a pitch. Use for every named match."),
    ("TimelineScroll",       "Cinematic timeline with camera pan and zoom. Use for year-by-year event sequences."),
    ("HeroTransferProfit","Buy-low/sell-high transfer analysis bars. Use for club transfer profit analysis."),
    ("SplitComparison",      "Side-by-side then/now comparison. Use for before/after contrasts."),
]

# System prompt fed to LLM for template selection
_TEMPLATE_SELECTION_PROMPT = """You are a motion graphics producer for a football documentary YouTube channel.
Given a scene description, decide if any of these existing Remotion templates covers it well enough.

EXISTING TEMPLATES:
{templates}

SCENE DESCRIPTION: {description}

Reply in this exact JSON format:
{{
  "use_existing": true/false,
  "template_id": "HeroBigStat",  // only if use_existing is true
  "reason": "short explanation"
}}

Be decisive. Only return use_existing=false if none of the templates would work even with minor prop adjustments."""


# ── Exemplar template source (used as few-shot context for generation) ────────

def _load_exemplar(name: str) -> str:
    """Load a template's source code as a few-shot example."""
    path = REMOTION_SRC / f"{name}.tsx"
    if path.exists():
        return path.read_text(encoding="utf-8")[:4000]  # cap at 4000 chars
    return ""


# ── Generation prompt ─────────────────────────────────────────────────────────

_GENERATION_PROMPT = """You are a senior Remotion/React developer and motion designer.
Write a complete, production-quality Remotion TSX component for a football documentary YouTube channel.

COMPONENT ID: {comp_id}
SCENE DESCRIPTION: {description}
PROPS HINT (suggested data structure): {props_hint}

DESIGN SYSTEM (follow these exactly):
{style_guide}

MOTION PRINCIPLES (follow these exactly):
{principles}

EXEMPLAR COMPONENT (pattern to follow — imports, structure, animation style):
```tsx
{exemplar}
```

VISUAL MOTIFS (use these shared components wherever they fit — they define the visual language):
- `RuleLine` — horizontal rule between sections; accepts color/opacity/label/progress props
- `ContextChip` — "/ LABEL" category stamp (e.g. <ContextChip label="Stats" />) — use near the top of every scene
- `FrameGlow` — animated border glow orbiting a rect (position absolute, inside a relative wrapper)
- `BadgeTreatment` — standardised club badge with optional glow halo

DESIGN RULES (these are non-negotiable):
- Every scene must have a `ContextChip` near the top-left identifying its type (e.g. "Career" / "Stats" / "Form" / "Lineup")
- Section dividers inside a scene use `RuleLine`, not plain <div> borders
- Club badges always use `BadgeTreatment` — never raw <img> or <SmartImg> directly for badges
- Image frames that persist on screen > 2 seconds should use `FrameGlow` to create visual life
- `skipIntro` prop: schema must include `z.boolean().optional().default(false)`; all entry springs must be written as `skipIntro ? 1 : spring(...)` so evolve/worldPan continuations appear instantly
- `worldState` prop: schema must include `WorldStateSchema.optional()` — import from "./shared"
- Never apply cameraX/cameraY transforms inside the component — VideoSequence handles this via WorldStateRoot

REQUIREMENTS:
1. Import ONLY from: "remotion", "react", "zod", "./shared" — no other imports
2. Export: `{comp_id}PropsSchema` (Zod schema), `{comp_id}` (React component)
3. Background: use `<PaperBackground />` or `<DarkBackground />` from shared.tsx
4. Grain layer: always include `<Grain />` on top of background
5. Fonts: use `fontFamily` (Inter) and `serifFontFamily` (Playfair Display) from shared.tsx
6. Animation: use spring() from remotion, follow motion-design-principles.md; all entry springs must be `skipIntro ? 1 : spring(...)`
7. Z-index sandwich: bg (z:0), image (z:1), grain (z:2), content (z:10)
8. All props must be optional with sensible defaults
9. Duration: 240 frames (8 seconds) — use useVideoConfig().fps for animation timing
10. Component must be self-contained and work with the default props in the schema
11. Schema must include `skipIntro: z.boolean().optional().default(false)` and `worldState: WorldStateSchema.optional()`
12. Include a `ContextChip` in the layout identifying the scene type

OUTPUT: Return ONLY the complete TypeScript/TSX code, no markdown fences, no commentary.
The first line must be: // GEN: {comp_id} — {description_short}
"""


def _slugify_desc(desc: str) -> str:
    """Convert description to a short CamelCase identifier."""
    words = re.sub(r'[^a-zA-Z0-9\s]', '', desc).split()[:4]
    return ''.join(w.capitalize() for w in words if w)


def _generate_comp_id(desc: str) -> str:
    """Generate a unique composition ID from description + timestamp hash."""
    hash_str = hashlib.md5(f"{desc}{time.time()}".encode()).hexdigest()[:6].upper()
    label    = _slugify_desc(desc) or "Custom"
    return f"GEN_{label}_{hash_str}"


def _update_gen_index(comp_id: str, tsx_filename: str):
    """Add the new component to gen/index.ts registry."""
    GEN_DIR.mkdir(exist_ok=True)

    current = GEN_INDEX.read_text(encoding="utf-8") if GEN_INDEX.exists() else ""

    # Already registered?
    if comp_id in current:
        return

    import_name = tsx_filename.replace(".tsx", "")

    # Find insertion point — just before the closing }
    import_line  = f'import {{ {comp_id} }} from "./{import_name}";'
    registry_line = f'  {comp_id},\n'

    # Add import after the last existing import
    if "import React" in current:
        current = current.replace(
            "// Generated components will be added here automatically",
            f"// Generated components will be added here automatically\n{import_line}"
        )
    else:
        # Prepend import
        current = f'{import_line}\n{current}'

    # Add to registry object
    current = current.replace(
        "  // Generated components will be added here automatically",
        f"  // Generated components will be added here automatically\n{registry_line}"
    )

    GEN_INDEX.write_text(current, encoding="utf-8")
    print(f"    [Motion] Registered {comp_id} in gen/index.ts")


def _check_existing_template(scene_description: str) -> str | None:
    """
    Ask LLM if any existing template covers this scene well enough.
    Returns template_id if yes, None if a custom graphic is needed.
    """
    templates_list = "\n".join(
        f"- {tid}: {desc}" for tid, desc in EXISTING_TEMPLATES
    )
    prompt = _TEMPLATE_SELECTION_PROMPT.format(
        templates=templates_list,
        description=scene_description,
    )
    try:
        raw = ask_gemini(prompt).strip()
        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())
        if data.get("use_existing"):
            return data.get("template_id")
    except Exception as e:
        print(f"    [Motion] Template selection error: {e}")
    return None


def _generate_tsx(comp_id: str, scene_description: str, props_hint: dict) -> str | None:
    """Call Gemini to generate a bespoke Remotion component."""
    # Load style guide + principles + design rules
    _engine_templates = Path(__file__).parent.parent / "templates"
    style_guide  = (REMOTION_SRC.parent / "STYLE_GUIDE.md").read_text() if (REMOTION_SRC.parent / "STYLE_GUIDE.md").exists() else ""
    principles   = (REMOTION_SRC.parent / "docs" / "motion-design-principles.md").read_text() if (REMOTION_SRC.parent / "docs" / "motion-design-principles.md").exists() else ""
    design_rules = (_engine_templates / "design_rules.md").read_text() if (_engine_templates / "design_rules.md").exists() else ""
    style_guide  = (style_guide + "\n\n" + design_rules).strip()

    # Use HeroHeadlineStack + HeroBigStat as exemplars
    exemplar = _load_exemplar("HeroHeadlineStack") or _load_exemplar("HeroBigStat")

    prompt = _GENERATION_PROMPT.format(
        comp_id=comp_id,
        description=scene_description,
        props_hint=json.dumps(props_hint, indent=2),
        style_guide=style_guide[:2000],
        principles=principles[:2000],
        exemplar=exemplar,
        description_short=scene_description[:60],
    )

    print(f"    [Motion] Generating bespoke component: {comp_id}...")
    try:
        tsx_code = ask_gemini(prompt).strip()
        # Strip any markdown fences the LLM added despite instructions
        if tsx_code.startswith("```"):
            lines = tsx_code.split("\n")
            tsx_code = "\n".join(
                line for line in lines
                if not line.strip().startswith("```")
            ).strip()
        return tsx_code
    except Exception as e:
        print(f"    [Motion] Generation failed: {e}")
        return None


def _validate_tsx(tsx_code: str, comp_id: str) -> bool:
    """Basic validation that the TSX looks correct."""
    required = [
        f"export const {comp_id}",      # component exported
        f"{comp_id}PropsSchema",         # schema exported
        "useCurrentFrame",               # uses Remotion
        "AbsoluteFill",                  # full-bleed
    ]
    for req in required:
        if req not in tsx_code:
            print(f"    [Motion] ⚠ Generated TSX missing: '{req}'")
            return False
    return True


def generate_motion_graphic(
    scene_description: str,
    props_hint: dict,
    output_dir: str,
    force_generate: bool = False,
) -> dict:
    """
    Main entry point. Given a scene description and suggested props,
    decide whether to use an existing template or generate a new one.

    Returns a result dict with composition_id, props, rendered_path, status.
    """
    out = Path(output_dir)
    renders_dir = out / "renders"
    renders_dir.mkdir(exist_ok=True)

    # 1. Check if any existing template fits
    if not force_generate:
        existing_id = _check_existing_template(scene_description)
        if existing_id:
            print(f"    [Motion] Using existing template: {existing_id} for '{scene_description[:50]}'")
            return {
                "composition_id": existing_id,
                "tsx_path":       None,
                "props":          props_hint,
                "rendered_path":  None,
                "status":         "use_existing",
                "used_template":  existing_id,
            }

    # 2. Generate a bespoke component
    comp_id      = _generate_comp_id(scene_description)
    tsx_filename = f"{comp_id}.tsx"
    tsx_path     = GEN_DIR / tsx_filename

    tsx_code = _generate_tsx(comp_id, scene_description, props_hint)
    if not tsx_code:
        return {"composition_id": None, "status": "failed", "error": "TSX generation failed"}

    # Validate basic structure
    if not _validate_tsx(tsx_code, comp_id):
        print(f"    [Motion] ⚠ Validation warnings for {comp_id} — saving anyway")

    # 3. Save to gen/
    GEN_DIR.mkdir(exist_ok=True)
    tsx_path.write_text(tsx_code, encoding="utf-8")
    print(f"    [Motion] ✓ Saved: src/gen/{tsx_filename}")

    # 4. Register in gen/index.ts
    _update_gen_index(comp_id, comp_id)

    # 5. Render via Remotion
    # Build default props from the Zod schema if possible
    # For now just use props_hint as the render props
    out_mp4 = str(renders_dir / f"{comp_id.lower()}.mp4")
    print(f"    [Motion] Rendering {comp_id}...")

    # Give Remotion 5 seconds to pick up the new file
    time.sleep(5)

    rendered = remotion_render(comp_id, props_hint, out_mp4, f"GEN {comp_id}")
    status = "rendered" if rendered else "generated"

    return {
        "composition_id": comp_id,
        "tsx_path":        str(tsx_path),
        "props":           props_hint,
        "rendered_path":   out_mp4 if rendered else None,
        "status":          status,
        "used_template":   None,
    }


# ── Integration with graphics_agent ──────────────────────────────────────────

def try_generate_for_tag(tag_name: str, tag_content: str, output_dir: str) -> dict | None:
    """
    Called by graphics_agent when a tag has no dedicated renderer.
    Builds a scene_description from the tag and attempts generation.
    """
    scene_description = f"{tag_name}: {tag_content}"
    props_hint = {"title": tag_content, "bgColor": "#f0ece4", "accentColor": "#C9A84C"}
    return generate_motion_graphic(scene_description, props_hint, output_dir)
