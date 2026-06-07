# LimitLens Menubar Improvement Plan

## Goal
Make the macOS menubar experience feel polished, compact, and readable instead of looking like dense CLI output pasted into a dropdown.

## Problems in Current UI
- Top-bar title is noisy and not very informative at a glance.
- Dropdown rows are too long and visually busy.
- Too many emojis/bars/status fragments compete for attention.
- Important actions and important quota warnings are not clearly separated.
- Kilo / AgentRouter naming is not consistently user-friendly.

## Design Direction
### 1. Cleaner menubar title
- Show only 1-2 highest-value items.
- Prefer short tool names plus percentage.
- Example: `💡 Codex 82% · Kilo 75%`
- Avoid long recommendation names in the title.

### 2. Better information hierarchy in dropdown
Use clear sections:
1. **Best right now**
2. **Low quotas**
3. **All tools**
4. **Actions**

### 3. Simpler row layout
Replace dense rows with a two-line style when possible:
- Line 1: tool name + main percentage
- Line 2: short secondary detail (`750 / 1000 units left`, `resets in 2h`, etc.)

### 4. Reduce visual clutter
- Keep one tool icon max.
- Avoid combining icon + traffic light + bar + long prose in every row.
- Reserve warning emphasis for genuinely low quotas.

### 5. Improve naming
- Show **Kilo Code** as the primary user-facing name.
- Show **AgentRouter** only as provider detail when needed.
- Normalize labels so they feel product-oriented, not backend-oriented.

### 6. Improve action area
Add clearer actions at bottom:
- Refresh Now
- Open Config
- Quit

### 7. Optional polish
- Show `Last updated` timestamp.
- Add compact vs detailed mode later if needed.
- Consider click-through actions for opening relevant tool views.

## Implementation Phases
### Phase 1 — Visual cleanup
- Refactor title formatting.
- Shorten row formatting.
- Reorganize menu sections.
- Normalize Kilo naming.

### Phase 2 — Better menu structure
- Add dedicated low-quota grouping.
- Separate recommendation rows from raw quota rows.
- Improve empty/loading/error states.

### Phase 3 — Interaction polish
- Add `Open Config` action.
- Add last-updated display.
- Explore click actions per tool.

## Acceptance Criteria
- Menubar title is short and readable.
- Dropdown no longer feels like raw CLI output.
- Low quotas are easier to spot.
- Kilo naming is user-friendly.
- Existing refresh and notification behavior remains intact.
- Menubar tests are updated for the new presentation.
