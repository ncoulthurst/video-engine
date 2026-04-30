"""
orchestrator — Track D pipeline reorder.

Replaces the old sequential chain (script → narration → graphics) with the
classification-aware ordered chain:

  storyboard → should_render → resolve_template → script → narration → render

Narration sees the kept-scene set as ground truth; renderer is invoked
per-scene only after data_gate validates the payload. Track D depends on
Track A (gates + RenderRequest), Track B (build_payload + render), and
Track C (narration_post_processor + ssml_validate, applied implicitly via
script_agent / narration_agent).
"""

from __future__ import annotations

import sys
import os
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

# ── Existing upstream agents (preserved — these prepare the brief) ──────────
from agents.entity_agent             import extract_entities
from agents.research_agent           import conduct_research
from agents.anecdote_agent           import research_anecdotes
from agents.anecdote_verification_agent import verify_anecdotes
from agents.data_agent               import fetch_data
from agents.analysis_agent           import analyze_data
from agents.script_agent             import generate_script
from agents.narration_agent          import generate_narration
from agents.storyboard_agent         import generate_scenes


# ── Track A imports (gates + RenderRequest) — stub if not yet landed ────────
try:
    from server import (  # type: ignore
        should_render as _should_render,
        resolve_template as _resolve_template,
        data_gate as _data_gate,
        RenderRequest as _RenderRequest,
    )
except Exception:
    from dataclasses import dataclass as _dc

    @_dc(frozen=True)
    class _RenderRequest:  # type: ignore
        template_id: str
        payload: dict
        scene_id: str

    def _should_render(scenes):  # type: ignore
        # Stub: keep all graphic scenes that aren't explicitly SHOULD_NOT_VISUALISE
        return {
            s.get("id") for s in scenes
            if s.get("type") in (None, "graphic")
            and s.get("classification") != "SHOULD_NOT_VISUALISE"
        }

    def _resolve_template(scene):  # type: ignore
        return scene.get("template")

    def _data_gate(template_id, payload):  # type: ignore
        return bool(payload)


# ── Track B imports (renderer) — stub if not yet landed ─────────────────────
try:
    from agents.graphics_agent import (  # type: ignore
        build_payload as _build_payload,
        render as _render,
        render_preview as _render_preview,
        set_renders_dir as _set_renders_dir,
    )
except Exception:
    def _build_payload(template_id, scene):  # type: ignore
        return scene.get("_payload") or {}

    def _render(request):  # type: ignore
        return None

    def _render_preview(request):  # type: ignore
        return None

    def _set_renders_dir(path):  # type: ignore
        pass


# ── PipelineResult ──────────────────────────────────────────────────────────

@dataclass
class PipelineResult:
    output_dir: str
    scenes:     list = field(default_factory=list)
    kept_ids:   set  = field(default_factory=set)
    rendered:   list = field(default_factory=list)  # list of (scene_id, path)
    skipped:    list = field(default_factory=list)  # list of (scene_id, reason)
    narration_chars: int = 0


# ── Storyboard ground-truth context builder ────────────────────────────────

def _build_storyboard_context(scenes: list[dict]) -> str:
    """Serialise the kept scenes into a compact prompt block for script_agent.

    Output one line per scene:
      "Scene N — ACT X — classification — data_kind — Template — Entity — Beat"

    The script_agent injects this verbatim into each act prompt so the LLM
    never references a graphic that ShouldRenderGate has already demoted.
    """
    lines: list[str] = []
    for idx, s in enumerate(scenes, start=1):
        act_idx = s.get("actIndex", "?")
        act_label = s.get("act", f"ACT {act_idx}")
        cls       = s.get("classification", "—")
        kind      = s.get("data_kind", "none")
        template  = s.get("template") or "(none)"
        entity    = s.get("entity") or "(no entity)"
        beat = (s.get("content") or s.get("narration") or "").strip().replace("\n", " ")
        if len(beat) > 140:
            beat = beat[:137] + "…"
        lines.append(
            f"Scene {idx:>3} — {act_label} — {cls} — {kind} — {template} — {entity} — {beat}"
        )
    return "\n".join(lines)


# ── Brief helpers ───────────────────────────────────────────────────────────

def _normalise_brief(brief) -> dict:
    """Accept either a plain topic string or a brief dict; normalise to dict."""
    if isinstance(brief, str):
        return {"topic": brief}
    if isinstance(brief, dict):
        return dict(brief)
    raise TypeError(f"run_pipeline: brief must be str or dict, got {type(brief).__name__}")


def _safe_name(topic: str) -> str:
    return (
        topic.lower()
             .replace(" ", "_").replace(":", "").replace("?", "")
             .replace("—", "").replace("-", "_").replace("&", "and")
             .strip("_")
    )


def _detect_pipeline_type(topic: str) -> str:
    comparison_kw = ["vs", "greatest", "comparison", "compare", "between", "ranking"]
    thematic_kw   = [
        "history of", "story of", "decline of", "rise of", "death of", "end of",
        "why did", "how football", "evolution of", "playmakers", "pressing",
        "system", "golden generation", "lost generation", "what happened to",
        "where did", "the fall of", "the rise of", "the problem with",
    ]
    t = topic.lower()
    if any(kw in t for kw in comparison_kw):
        return "COMPARISON"
    if any(kw in t for kw in thematic_kw):
        return "THEMATIC"
    return "DOCUMENTARY"


# ── Main entry point ────────────────────────────────────────────────────────

def run_pipeline(brief) -> PipelineResult:
    """Track D ordered pipeline.

    Steps:
      1. storyboard       = generate_scenes(brief)
      2. keep_ids         = should_render(storyboard)               # Track A
      3. mark templates   = resolve_template(s) per kept graphic    # Track A
      4. context          = _build_storyboard_context(kept)
      5. script           = script_agent.generate(storyboard=…, storyboard_context=…)
      6. narration        = narration_agent.build(script)           # Track C SSML inside
      7. render loop      = build_payload + data_gate + render      # Track B
      8. return PipelineResult
    """
    brief = _normalise_brief(brief)
    topic = brief.get("topic", "")
    if not topic:
        raise ValueError("run_pipeline: brief must include 'topic'")

    sep = "=" * 50
    print(f"\n{sep}\nSTARTING PIPELINE (Track D): {topic}\n{sep}")

    p_type        = _detect_pipeline_type(topic)
    is_comparison = (p_type == "COMPARISON")
    print(f"[*] Detected Pipeline Type: {p_type}")

    # ── Output directory ────────────────────────────────────────────────────
    safe_name = brief.get("safe_name") or _safe_name(topic)
    out_dir   = Path(brief.get("output_dir", f"output/{safe_name}"))
    out_dir.mkdir(parents=True, exist_ok=True)
    out_dir_str = str(out_dir)

    # ── Upstream prep (still useful — these populate research/analysis on disk
    #    that storyboard_agent consults via the brief's wiki/context fields) ──
    entities = brief.get("entity") or extract_entities(topic)
    if "research" not in brief:
        conduct_research(entities, out_dir_str, is_comparison=is_comparison)
        research_anecdotes(entities, out_dir_str)
        verify_anecdotes(out_dir_str)
        fetch_data(entities, out_dir_str)
        analyze_data(entities, None, out_dir_str)

    # ── 1. Storyboard ───────────────────────────────────────────────────────
    blueprint        = brief.get("blueprint", {"acts": []})
    checked_facts    = brief.get("checked_facts", [])
    wiki             = brief.get("wiki", "")
    user_context     = brief.get("context", "")
    retention_brief  = brief.get("retention_brief")
    director_overrd  = brief.get("director_override", "")

    # If a user-edited storyboard exists on disk (saved via Step 4 "💾 Save"),
    # load it instead of regenerating. This lets the user iterate on script/
    # narration without losing storyboard edits to a fresh LLM regen.
    saved_storyboard_path = out_dir / "storyboard.json"
    use_saved = saved_storyboard_path.exists() and not brief.get("force_regenerate_storyboard")
    scenes = []
    if use_saved:
        try:
            saved = json.loads(saved_storyboard_path.read_text())
            # Saved file is a bare list (legacy /save-storyboard) or a {"scenes": [...]} dict
            if isinstance(saved, list):
                scenes = saved
            elif isinstance(saved, dict):
                scenes = saved.get("scenes", [])
            print(f"[*] Loaded saved storyboard from {saved_storyboard_path} ({len(scenes)} scenes) — skipping regeneration")
            print( "    Set brief['force_regenerate_storyboard']=True to override this behaviour.")
        except Exception as e:
            print(f"[!] Could not read saved storyboard ({e}) — regenerating instead")
            scenes = []

    if not scenes:
        print("[*] Storyboard agent generating scenes…")
        scenes = generate_scenes(
            topic, entities, blueprint, checked_facts,
            wiki=wiki, context=user_context,
            retention_brief=retention_brief,
            director_override=director_overrd,
        ) or []
        print(f"    -> {len(scenes)} scene(s) generated")

    # ── 2. ShouldRenderGate (Track A) ───────────────────────────────────────
    keep_ids = _should_render(scenes)
    print(f"[*] ShouldRenderGate kept {len(keep_ids)} of {sum(1 for s in scenes if s.get('type') == 'graphic')} graphic scenes")

    # ── 3. Resolve templates per kept graphic scene ─────────────────────────
    prev_template = None
    for s in scenes:
        if s.get("type") != "graphic":
            continue
        if s.get("id") not in keep_ids:
            s["template"] = None
            continue
        s["_prev_template"] = prev_template
        resolved = _resolve_template(s)
        s["template"] = resolved
        if resolved:
            prev_template = resolved

    # ── 4. Storyboard ground-truth context ──────────────────────────────────
    kept_for_context = [
        s for s in scenes
        if s.get("template") or s.get("type") != "graphic"
    ]
    storyboard_context = _build_storyboard_context(kept_for_context)
    (out_dir / "storyboard.json").write_text(json.dumps(scenes, indent=2, default=str))
    (out_dir / "storyboard_context.txt").write_text(storyboard_context)

    # ── 5. Script (narration written for the kept set) ──────────────────────
    print("[*] Script agent writing narration aligned to kept storyboard…")
    script = generate_script(
        entities, out_dir_str,
        storyboard=scenes,
        storyboard_context=storyboard_context,
    )

    # ── 6. Narration → SSML (Track C handles pronunciation_replace + ssml_validate) ──
    print("[*] Narration agent building TTS-ready text…")
    narration = generate_narration(out_dir_str) or ""

    # ── 7. Preview loop (Track B + Track A data_gate) ───────────────────────
    # OPTION A render-on-confirm:
    #   - Pipeline produces PNG single-frame previews + manifest.json
    #   - mp4 render is deferred to /render-batch endpoint after user approves
    #   - Studio + timeline editor read manifest.json and show PNG previews
    renders_dir = Path(out_dir) / "renders"
    renders_dir.mkdir(parents=True, exist_ok=True)
    previews_dir = renders_dir / "previews"
    previews_dir.mkdir(parents=True, exist_ok=True)
    _set_renders_dir(str(renders_dir))

    import re as _re
    def _safe_scene_id(sid):
        return _re.sub(r"\W+", "_", str(sid)).strip("_") or "scene"

    rendered: list = []
    skipped:  list = []
    manifest: list = []
    for s in scenes:
        # Only graphic scenes get auto-rendered. Clips need user footage; transitions
        # and narration are handled by VideoSequence at export time.
        if s.get("type") != "graphic":
            continue
        if not s.get("template"):
            continue
        template_id = s["template"]
        scene_id    = s.get("id")
        try:
            payload = _build_payload(template_id, s)
        except Exception as e:
            skipped.append((scene_id, f"build_payload raised: {e}"))
            continue
        if not payload:
            skipped.append((scene_id, "build_payload returned None"))
            continue
        if not _data_gate(template_id, payload):
            skipped.append((scene_id, f"data_gate rejected (source={payload.get('_source')})"))
            continue

        request = _RenderRequest(template_id=template_id, payload=payload, scene_id=scene_id)
        safe_id  = _safe_scene_id(scene_id)
        mp4_name = f"{template_id}_{safe_id}.mp4"
        png_name = f"{template_id}_{safe_id}.png"

        # Render PNG preview only (mp4 deferred to /render-batch)
        try:
            preview_path = _render_preview(request)
        except Exception as e:
            skipped.append((scene_id, f"render_preview raised: {e}"))
            preview_path = None

        # Derive tag fields from storyboard scene shape so timeline editor's
        # _find_render_for_tag (script-tag → manifest) can match.
        tag_key  = (s.get("template") or "").upper().strip()
        tag_text = (s.get("content")  or "").strip()
        tag_full = f"[{tag_key}: {tag_text}]" if tag_key else ""

        manifest.append({
            "scene_id":         scene_id,
            "scene_index":      s.get("scene_index"),
            "act":              s.get("act"),
            "act_index":        s.get("actIndex"),
            "composition":      template_id,
            "tag":              tag_full,
            "tag_text":         tag_text,
            "tag_key":          tag_key,
            "type":             s.get("type", "graphic"),
            "filename":         mp4_name,
            "preview_filename": png_name,
            "preview_rendered": bool(preview_path),
            "rendered":         False,  # mp4 not yet rendered
            "props":            payload,
        })

        if preview_path:
            rendered.append((scene_id, str(preview_path)))
        else:
            skipped.append((scene_id, "render_preview returned None"))

    # Write manifest.json so /studio + /edit can show previews + queue mp4 renders
    manifest_path = renders_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str))

    print(f"\n{sep}\nPIPELINE COMPLETE (preview-only — mp4 deferred)\n{sep}")
    print(f"  previews: {len(rendered)} | skipped: {len(skipped)}")
    print(f"  manifest: {manifest_path}")
    print(f"  output:   {out_dir_str}")

    return PipelineResult(
        output_dir=out_dir_str,
        scenes=scenes,
        kept_ids=set(keep_ids),
        rendered=rendered,
        skipped=skipped,
        narration_chars=len(narration),
    )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("usage: python3 orchestrator.py <topic>", file=sys.stderr)
        sys.exit(1)
    topic_idea = " ".join(sys.argv[1:])
    run_pipeline({"topic": topic_idea})
