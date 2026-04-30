# RETENTION PATTERNS (V3)

## Pacing Rhythm

The baseline rhythm for a 15-20 minute documentary is a 3-beat cell:
**narration → clip → graphic** — repeat, vary, escalate.

Rules:
- Never place two graphics back-to-back without narration between them
- Never open a cold open with a graphic — open with narration or clip first
- Every 3 acts (roughly 5 minutes), escalate: the narration gets shorter, the cuts get faster, the graphics get bolder
- Narration scenes should shrink from ~14s (ACT 1) to ~8s (ACT 5) — urgency builds through compression
- ACT 3 (PEAK) is the exception: slow down. Let the biggest graphic breathe for 12–14s.
- Transition scenes (letterbox, push, grain) are 2s max — they are punctuation, not scenes

### Scene duration targets by type
| Type        | ACT 1–2 | ACT 3 | ACT 4–5 |
|-------------|---------|-------|---------|
| narration   | 12–16s  | 10–14s| 8–12s   |
| clip        | 7–10s   | 8–12s | 6–8s    |
| graphic     | 8–12s   | 10–14s| 7–10s   |
| transition  | 2s      | 2s    | 2s      |

---

## Scene Roles — what each role means narratively

| Role              | Purpose | Placement |
|-------------------|---------|-----------|
| `anchor`          | Embodies the thesis. The viewer returns here as a home base. Always a clip or graphic of the anchor character at their defining moment. | ACT 3 strongest scene |
| `evidence`        | Proves a claim made in narration. A stat, a result, a clip of what the narrator just said. | Immediately after the narration it supports |
| `emotional_beat`  | Makes the viewer feel something. Not data — a human moment: the celebration, the low, the revelation. | End of each act |
| `transition_support` | Bridge between two ideas. Short (≤6s), does not stand alone. A reaction shot, a crowd shot, a close-up cutaway. | Between evidence and anchor, or between acts |
| `context`         | Sets the scene. First scene of any act — establishes the world the viewer is about to enter. | First scene of each act |

---

## Callback Mechanics

The retention loop is built from callbacks — returning to an image, phrase, or character the viewer already knows.

Rules:
- The **contrast frame** (`loop_sentence` from retention_brief) must recur in different words at every act break — the narrator doesn't repeat it verbatim, but echoes it
- The **anchor character** must appear in at least 3 acts. Each appearance shows a different phase: introduction → peak → aftermath
- ACT 5 must **call back to the cold open**. If the cold open showed a controversy, ACT 5 asks whether it mattered. If it showed a triumph, ACT 5 asks whether it was worth it.
- The **closing provocation** is a callback to the documentary's opening question — but unanswered. It should feel like the viewer has been asking this question since minute 1.

### The 3-appearance anchor arc
```
ACT 1/2 — Introduction: "This is who they are"
ACT 3   — Peak: "This is what they became" (anchor scene, strongest moment)
ACT 5   — Aftermath: "This is what they left behind" (closing_line from retention_brief)
```

---

## Continuity Devices (3 types)

These are the tools that make a documentary feel like one continuous thought, not a list.

### 1. Callback
Return to an image, phrase, or character the viewer already knows.
- Cold open moment → referenced again in ACT 3 or ACT 5
- "Earlier we saw X" → now the viewer sees it in a new context
- The contrast frame (`loop_sentence`) should echo across act breaks

### 2. Echo
A structural parallel — a moment in ACT 4 or 5 that mirrors the structure of a moment in ACT 1 or 2, but with reversed meaning.
- ACT 1: the player arrives at a new club, unknown, carrying hope
- ACT 5: the player arrives at a new club, fading, carrying doubt
- Same structure. Different emotional register. The viewer feels the distance.
- Use when: the arc has completed and you want the viewer to feel the weight of it

### 3. Reversal
A moment where something the viewer believed turns out to be wrong.
- Setup: "Everyone thought this was the moment everything changed."
- Reversal: "It wasn't. It was the moment the unravelling began."
- Use once per documentary, in ACT 3 or ACT 4 — the break point
- Never manufacture a reversal — it must be earned by the setup in earlier acts

---

## Breather Scenes

Not every scene should push forward. Tension requires release.

A **breather scene** is a short (6–8s clip) moment that slows the pace and lets the viewer absorb what just happened.

Rules:
- Place one breather per act after the highest-intensity moment in that act
- Role: always `emotional_beat` — a human moment, not data
- Examples: a quiet celebration, a manager walking off alone, a crowd shot after a goal, an interview clip of reflection
- Duration: 6–8s max — any longer and it becomes a pause in the story, not a release
- Never place a breather as the first or last scene of an act — it belongs in the middle

---

## Act Escalation Rules

Each act must feel more urgent than the last. Escalation is structural, not just tonal.

| Act | Narrative mode | Clip density | Graphic complexity | Narration pace |
|-----|---------------|--------------|-------------------|----------------|
| ACT 1 | Establishing | 1–2 clips | Simple timelines | Slow, grounded |
| ACT 2 | Rising | 2–3 clips | Stats + timeline | Medium |
| ACT 3 | Peak (exception: slower) | 2–3 clips | Hero visual + radar | Deliberate |
| ACT 4 | Break / consequence | 3–4 clips | Bare stats, no flourish | Fast, urgent |
| ACT 5 | Question / aftermath | 2–3 clips | Minimal — feel > data | Shortest narration |

Enforcement:
- Narration word count must decrease from ACT 1 to ACT 5 (except ACT 3 which can be equal to ACT 2)
- ACT 4 and 5 must not introduce new characters — only characters already established
- ACT 5 must not introduce new graphics types not seen earlier — repetition with new meaning

---

## The Warning Sign Hook (every ~3 minutes)

At every major act transition, do not simply advance the story — install a forward hook.

Not: "And so began his time at Barcelona."
But: "Barcelona was supposed to be the answer. It turned out to be the question."

Patterns:
- **The shadow**: "Nobody noticed the cracks forming."
- **The pivot**: "But something was about to break."
- **The inversion**: "The thing that made him great was the same thing that would undo him."
- **The clock**: "He had three years left to prove them wrong."

---

## Forbidden Patterns

These patterns kill retention. The engine must never produce them.

| Forbidden | Why | Replace with |
|-----------|-----|--------------|
| "And so began..." | Passive, zero tension | A forward hook with consequence |
| "He was one of the best in the world" | Generic, unverifiable | A specific stat, a specific match, a specific rival's quote |
| "It was an iconic moment" | Lazy — tells the viewer to feel instead of showing them | Name the moment, the date, the crowd reaction |
| Two graphics back-to-back | Pacing collapses — viewer disengages | Insert narration or clip between them |
| Conclusion in ACT 5 | Satisfies the viewer — they stop watching or don't share | End on the closing provocation — an unanswered question |
| Stats-first cold open | Numbers before emotion loses viewers in 10s | Open on a human moment, then justify it with data |
| "Over the course of his career..." | Summary mode — no tension | Pick one specific, named moment and zoom in |
| PLAYER TRIO with non-peer comparisons | Confuses rather than provokes | Three players of the same era, same tier, competing for the same narrative claim |

---

## Structural Milestones (retention checkpoints)

| Timestamp | What must have happened by here |
|-----------|--------------------------------|
| 0:30      | The viewer has a reason to keep watching — a claim has been made that isn't yet proven |
| 2:00      | The anchor character has been introduced — the viewer has someone to follow |
| 5:00      | At least one specific, named fact has surprised the viewer |
| 8:00      | ACT 3 (PEAK) has begun — the pace has changed, something is at stake |
| 12:00     | The anchor character's lowest point or defining moment has been shown |
| 15:00     | The closing provocation has been seeded (the viewer is already asking the question) |
| End       | The video ends on an open question — never a summary |
