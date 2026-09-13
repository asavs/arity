# Case Study 07: Plain Explanation First, Then Distill

> **Location**: `.arity/roles/committer/heuristics/07_plain_explanation_first.md`
> **Topic**: Recovering a commit message the author calls too long / symptom-heavy / gobbledygook
> **Instance**: `fix/slice-east-west-wall-quads`, 2026-09-11 ("Emit east/west wall quads per tile")
> **Evolution**: Jargon draft $\to$ Grade S

---

## 1. The Signal

The author rejects the draft with one of:

- "feels very very long"
- "describing symptoms not actual code changes"
- "that is gobbledygook, what does the code do"

Do not argue the draft. Do not shorten by trimming words. Restart from zero.

## 2. Step 1: Plain Explanation (never shipped)

Explain what the code does with zero doctrine terms — no `depth_y`, no
`depth keys`, no gate names. Concrete nouns only (tall strip, small squares,
head poking into the wall). One paragraph, spoken tone. Instance:

> The old code built each side wall as one tall strip with a single depth
> number, so the player sorted wrongly against one half. The new code builds
> the side walls from one small square per tile, each with its own depth, so
> each bit of wall sorts on its own. Collision now checks body width and head
> height instead of one foot point.

## 3. Step 2: Distill to Three Sentences (shipped)

Compress the plain paragraph into exactly three load-bearing sentences:

1. **Wrong** — the old behavior in one clause.
2. **Fix + why** — the code change and the reason, one sentence.
3. **Proof** — raw empirical line (Headless Ticks / `[Tiles]` / `[Depth]`).

Instance (shipped verbatim):

```git
Emit east/west wall quads per tile

Side walls were single tall strips with one depth value, so the player sorted wrong against one half of the wall. Build side walls from one quad per tile with per-tile depth (top/bottom stay merged, corners stay with them to avoid double-draw) and probe collision with body width and head height, because depth and collision both need per-tile resolution to match what the player sees. MSVC Release build: [Tiles] passed; [Depth] passed; 100,000 ticks in 1.93 ms on RTX 3070.
```

## 4. The Rule

Jargon draft $\to$ plain paragraph $\to$ three sentences. Never ship step 1;
never skip it. If the author cannot understand step 1, step 2 is wrong no
matter how dense it reads.
---

## 5. The Pair (verbatim)

### Original draft (rejected: too long, symptom-heavy, gobbledygook)

```git
Emit east and west wall quads per tile for depth

A merged east/west run spans the full column height under one
depth_y, so the north half covers the player wrongly and the south
half loses to it. Keep north/south runs merged along X (constant Y,
one depth_y per run); emit east/west as one TILE_WSIZE x TILE_HSIZE
quad per solid tile with depth_y at the tile center. North/south
runs own the four corners, east/west cover the inner column only,
so no coplanar duplicates Z-fight. Align all wall scales and centers
to tile dimensions, replacing the wall_band offsets, so visual faces
match the collision grid with zero dead zones. Probe collision with
a directional footprint (hw 0.04, head offset 0.06) so north motion
stops before the torso enters the wall.

MSVC Release build: [Tiles] wall/sarc/clamp parity passed; [Depth]
wall/player/floor order passed; 100,000 headless ticks in 1.93 ms
on RTX 3070.
```

### Final (shipped as d6c03e9)

```git
Emit east/west wall quads per tile

Side walls were single tall strips with one depth value, so the player sorted wrong against one half of the wall. Build side walls from one quad per tile with per-tile depth (top/bottom stay merged, corners stay with them to avoid double-draw) and probe collision with body width and head height, because depth and collision both need per-tile resolution to match what the player sees. MSVC Release build: [Tiles] passed; [Depth] passed; 100,000 ticks in 1.93 ms on RTX 3070.
```
