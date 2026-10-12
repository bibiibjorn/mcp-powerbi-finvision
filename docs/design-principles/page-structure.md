# Page structure & information hierarchy

## Principle

A Power BI page has one job: answer the user's first question in the first look. Structure — position, size, grouping, whitespace — is how that answer becomes visible before anything else. The 3-30-300 rule (3 seconds to the headline, 30 to the scan, 300 to the drill) is a *pattern* that fits most audiences most of the time; it is not a law. A page is well-structured when the user could describe what it tells them after a 3-second glance, and you can name the single question each visual answers.

## Why it works

Human attention is hierarchical and pre-attentive. Before the eye reads words, it processes position, size, color, and motion in ~200 ms (Ware, *Information Visualization*, ch. 5 — "preattentive processing"). Reading-order conventions in left-to-right top-to-bottom cultures put the first fixation in the upper-left quadrant of a full-page display; eye-tracking on dashboards shows a Z-shaped or F-shaped scan path that reliably starts there (Nielsen/Pernice eye-tracking studies on web layouts; Few, *Information Dashboard Design*, ch. 4).

Gestalt proximity means visuals close together are read as a group; gestalt alignment means the eye notices 4-pixel misalignment as a signal that something is wrong. These are hard-wired, not cultural — they apply even to users who don't know the word "gestalt." That means structure communicates *before* the user reads anything. An uneven margin or a stray visual is not a cosmetic problem; it is a content-truthfulness problem, because the reader spends attention-budget decoding "is something broken?" instead of reading your data.

Stephen Few's dashboard attention model adds the temporal dimension that names the 3-30-300 pattern:

- **3 seconds** — the user has glanced at the top of the page and expects the headline number, the status of the business.
- **30 seconds** — they have scanned the primary row: KPIs, trend, the single chart that carries the narrative.
- **300 seconds** — they are drilling, cross-filtering, reading a detail table. They have committed to the page.

Pages that fail 3-30-300 don't just look bad — they invert the cost of attention. The reader spends the first 3 seconds hunting rather than consuming. Pages that *correctly break* 3-30-300 do so because the audience isn't an executive arriving cold — they're an analyst who came for one chart and wants it full-bleed.

Finally, cognitive load (Sweller, 1988): working memory holds ~7±2 chunks. Every visual, every slicer, every border is a chunk. Structure is the mechanism by which you compress 20 visuals into 3 bands, so the page fits in working memory as "summary / trend / detail" rather than as 20 disconnected things.

## How to apply

Structure decisions cascade: **audience → page count → per-page purpose → band layout → visual slot**. Make them top-down; don't start by placing a chart and work outward.

### Step 1 — Identify the audience

Before picking a layout, name the user in one word. The audience determines the structure — there is no generic "good dashboard layout" independent of who reads it.

- **Executive / cold arrival.** Glances for ≤ 3 seconds, leaves if the headline isn't there. Needs 3-30-300, hard. Headline KPI top-left; supporting KPIs in the same band; one dominant trend chart below; detail tucked at the bottom. ≤ 8 visuals total.
- **Manager / weekly review.** Same structure, higher density allowed — up to ~12 visuals. Expects slicers for time/business-unit cuts. Will scroll on a monitor but not on a phone.
- **Operational console / monitor wall.** Dense by design — 16-25 visuals is normal. Audience is staring at the screen all day; they know the layout. Rule: visuals must be on a strict grid, cognitively grouped into ≤ 4 bands. The 3-second rule still applies (the operator must spot an alarm immediately), but the headline isn't a KPI — it's whatever is flashing red.
- **Analytical scratchpad.** Audience = one analyst, exploring. Structure is *narrative*: left-to-right, question to answer. 1-3 dominant visuals with supporting filters. Density is noise here.
- **Mobile.** Always ≤ 5 visuals, vertical stack, single band. No grid tricks — the viewport is too narrow. 3-second rule is still 3 seconds, but the "page" is the top 40% of the phone screen.

If the user didn't tell you the audience, ask. Do not guess from the data alone — the same sales data produces a completely different page for a CFO vs. an account manager.

### Step 2 — Decide page count

A report is not one page. Before placing any visual, pick:

- **One page per question the audience will ask.** A CFO asks "how's the quarter?" — that's one page. They also ask "which regions are hurting?" — that's a second page. If a single page tries to answer both, the structure fights itself.
- **Max ≈ 5-8 pages for a report.** Beyond that, the user can't remember which page answers what; the report becomes a search problem.
- **Detail pages** serve drill-through, not navigation. A detail page is arrived at by right-clicking a row, not by tab-clicking. Its structure is flat and dense — it's answering a specific question about a specific entity, so the user already has context.

### Step 3 — Define the page's purpose

For each page, write one sentence in the form "Shows *audience* *what* so they can *decide / verify / investigate what*." If you can't write that sentence, the page has no structure because it has no purpose.

Examples:
- *"Shows the CFO Q4 portfolio performance so they can verify the quarter closed on target."* → exec layout, 3-30-300.
- *"Shows the relationship manager everything about Participation X so they can prepare for a client meeting."* → high-density detail page, single subject.
- *"Shows the analyst NAV trajectory under two ownership scenarios so they can judge which scenario is robust."* → scratchpad layout, narrative left-to-right.

### Step 4 — Band layout

Pages are composed of **horizontal bands** (or occasionally vertical columns on monitors wider than 16:9). A band is a visually coherent row of visuals the eye reads as one chunk. The 3-30-300 pattern maps cleanly:

- **Band A (headline band).** Top of page. Answers the 3-second question. Contains the headline KPI(s) and the primary slicer row if any. Height: 120-180 px on a 720-tall page, 150-220 on 1080. If the page has no KPIs (scratchpad), this band may be the page title + a short context line instead.
- **Band B (trend / primary analysis).** Middle. Answers the 30-second question. Usually one dominant chart (line, column, ribbon, combo) spanning most of the page width, or two smaller charts side by side. Height: 300-450 px.
- **Band C (detail / drill).** Bottom. Answers the 300-second question. Usually a matrix, table, or decomposition tree. Height: whatever is left; typically 300-400 px.

Pages with 4 bands (A-KPI → B-slicers → C-trend → D-table) work fine when the slicer row is visually distinct and the KPI row is not. Pages with 5+ bands generally fail — readers lose the structure.

### Step 5 — Place each visual in its band

For each visual, answer: *which band does this belong to?* If "both," pick the higher one (it's closer to the 3-30-300 front). If "neither," cut the visual.

Within a band, position by importance: headline KPI is leftmost on executive pages (upper-left quadrant wins 3-second attention). On mobile, importance is *top*, not *left*.

### Step 6 — Grid math, always

Never eyeball. Compute positions:

```
margin = 24 (or 32 for wide pages)
gap    = 16
content_width = page_width - 2*margin
total_gaps    = gap * (n_visuals_in_row - 1)
visual_width  = (content_width - total_gaps) / n_visuals
```

For mixed-width rows (KPI + chart sharing a row), enforce that the gap between them equals the gap elsewhere on the page. 4 px misalignment is visible. Confirm by checking `visual_B.x - (visual_A.x + visual_A.width)` equals the gap between every other adjacent pair.

Vertical column boundaries across bands should align. If band B splits 648 | 16 | 584, band C should reuse the same 648 | 16 | 584 split unless the content demands otherwise. Aligned column gutters carry the eye down the page; offset gutters break the grid.

### When to break 3-30-300

Break it when the audience is not an arriving executive. Specifically:

- **Single-subject detail pages.** The whole page is about one thing (one participation, one customer, one flight). The 3-second question is "which one?" — and it's already answered because the user drilled in from elsewhere. The rest of the page is 300-level detail, all of it. No KPI band needed; the header becomes "entity name + a handful of key attributes" and the rest of the page is the report.
- **Scratchpad / exploration pages.** The audience is committed before they arrive. Dominate the page with the one visual that is the point. Skip the headline band.
- **Alarm consoles.** The headline is whatever is red, wherever it is. Structure here is *uniform grid*; attention is drawn by semantic color, not by position.

In all three cases, breaking 3-30-300 is correct because the page isn't answering a 3-second question. If you catch yourself breaking the rule on an executive-audience page, you're almost certainly wrong.

## Worked examples

### Example 1 — R0101 `DIR PARTICIPATIONS - DETAIL` (correctly breaking 3-30-300)

**User intent.** A relationship manager has clicked through to one specific direct participation. They already know *which* entity they're looking at; now they want everything.

**Data shape.** One entity (the selected participation) × many attributes: name, legal structure, ownership %, NAV, trend of NAV, currency exposure, key dates, linked documents, contact info.

**Structure decision.** Break 3-30-300. No headline KPI band in the traditional sense; instead, a compact header strip (two cardVisuals spanning the full width, `f208eccac1e0f3ecd659` at 850×110 and `c432543ba2fab21d4d51` at 1030×110) functions as the entity identity card. Below it, the page packs 19 visuals — donuts for ownership breakdown, multiRowCards for key metrics, matrix for transaction history, a bookmark navigator for switching sub-views, images and shapes as visual anchors.

**Why this is correct.** The 3-second question was already answered on arrival (which participation?). The page isn't serving an executive glancing; it's serving an analyst reading. Density is the feature, not the bug. What makes it work — and what distinguishes it from "Power BI slop" — is that the visuals are grouped into bands cognitively (identity header → ownership donuts + key KPIs → transaction matrix → linked bookmarks) even though the density is high. A reader can point at any visual and say what question it answers. That is structure doing its job even in a high-density layout.

**What the agent would produce.** Given the prompt *"Build a detail page for a single participation showing all relevant metrics,"* the agent should (a) skip the executive KPI band, (b) create an identity header strip, (c) compose 4 cognitive bands of related visuals, (d) pack density inside each band on a strict grid, (e) confirm each visual has a stateable purpose before placing it.

### Example 2 — R0101 `ANALYSIS - TIME SERIES` (single dominant visual, 3-30-300 suspended)

**User intent.** An analyst is exploring rank change over time across ~20 entities. They came to the page to look at *one chart* — a ribbonChart — and want it as large and readable as possible.

**Data shape.** Time × entity × rank. Ribbon is the only native visual that encodes rank change over time legibly.

**Structure decision.** Suspend 3-30-300. The page has one visual (ribbonChart `344d69fec2b5d61e49cb` at 1541×798) that dominates. No KPI band; no detail table at the bottom; no supporting charts.

**Why this is correct.** The page is a *scratchpad for one question*. Adding cards would steal pixels from the ribbon, which needs every pixel it can get because rank-change is cognitively demanding (Cleveland-McGill ranks angle/position encodings below position, and ribbon is angle-heavy). The visual *is* the page. A reader arriving cold might be confused for 3 seconds — but they're not arriving cold; they navigated to this tab specifically.

**What the agent would produce.** Given *"Show rank change over time for these entities,"* the agent should give the ribbon the whole canvas, add a title bar identifying the measure and the time period, and stop. Don't add supporting visuals to "fill the page."

### Example 3 — correctly breaking the rule, wrong

Given the prompt *"Build an exec dashboard for Q4 portfolio performance,"* suppose an agent produced: one giant 1600×800 line chart of NAV over time, and nothing else. The agent's reasoning: "1 dominant chart answers the question."

**This is wrong.** The audience is executive, arriving cold. At 3 seconds they want a number, not a trend. They want to know *did we hit the target?* — which is a KPI + variance question, not a trend question. The trend matters at 30 seconds. The agent has conflated "the question is about performance" with "the visual is a performance chart."

**Correct structure.** Band A: 4 KPI cards (NAV, YTD return, variance vs benchmark, top-contribution holding) spanning the top. Band B: the trend chart. Band C: a matrix of by-asset-class returns. The line chart is still there — it's just in the correct band.

## Anti-patterns

### "Dashboard amoeba"

**What the agent is tempted to produce.** Visuals of random sizes scattered across the page. A 400×250 chart next to a 600×180 card next to a 300×400 donut. Positions set by dragging rather than by grid math.

**Why it's wrong.** Gestalt alignment is broken — the reader's eye spends attention on decoding "why are these sizes different?" instead of reading. Unaligned edges signal "this report was assembled, not designed," and the reader trusts the data less.

**What to do instead.** Grid math. Decide margin and gap once per page; compute every visual's width and height from the grid. Equal-width cards in a row; equal-height charts in a band. If a visual "needs" to be a weird size, that's a signal it belongs on a different page.

### "Tetris stacking"

**What the agent is tempted to produce.** Visuals packed edge-to-edge with no gap. Zero breathing room. Often accompanied by a "but I fit 15 visuals!" justification.

**Why it's wrong.** Gestalt proximity turns zero-gap visuals into a single chunk — the reader loses the ability to parse them as separate things. Whitespace isn't decoration; it's the membrane between ideas.

**What to do instead.** 16 px minimum between visuals. 24 px preferred. If you can't fit what you need to fit with the gap, cut visuals — don't compress the gap.

### "KPI wall"

**What the agent is tempted to produce.** 10-12 KPI cards in one row because the user said "add these metrics." Each card at 150 px wide, all the same.

**Why it's wrong.** No hierarchy. 12 equally-weighted numbers means zero numbers — the reader can't decide what's important. The 3-second scan has nothing to land on.

**What to do instead.** Max 4-6 cards per row, with explicit hierarchy: the primary KPI (headline) gets more space / larger number; supporting KPIs are smaller. If there are truly 12 metrics that matter equally, that's a matrix, not a KPI band.

### "Scroll to find the point"

**What the agent is tempted to produce.** The headline number is placed below the fold, or on the right side of the page, because that's where the chart happens to sit after the agent placed it first.

**Why it's wrong.** Inverted 3-30-300. The reader's first fixation lands on supporting detail; they spend attention hunting for the answer instead of consuming it. Often paired with a big trend chart at the top — but the trend isn't the answer; the latest value + variance is.

**What to do instead.** Place KPIs top-left *first*, then the trend underneath. The agent should never place a chart first and back-fill KPIs.

### "Mystery tabs"

**What the agent is tempted to produce.** 12 report pages with names like "Page 1", "Analysis", "Details", "Details 2", "More Details" — produced by cloning a template repeatedly.

**Why it's wrong.** The user can't navigate. Each page has no purpose statement; they're just containers. When the CFO returns on Monday, they can't find their quarterly view.

**What to do instead.** ≤ 8 pages per report. Each page name is a complete thought ("Q4 Summary", "Regional Performance", "Participation Detail"). Each page passes the "one-sentence purpose" test before it gets built.

### "Every visual is a chart"

**What the agent is tempted to produce.** The page has 10 charts. No text. No title. No context strip. Pure visual density.

**Why it's wrong.** Charts without context are ambiguous. The reader doesn't know what period the data covers, what "NAV" means in this context, or why they should care about the third chart. Preattentive processing handles visuals, but *meaning* requires a one-line anchor.

**What to do instead.** Every page has a title. Pages with domain-specific terms include a short context line ("All values in EUR, as of last close"). A textbox visual is cheap; add one.

## Self-audit checklist

Run each item at end-of-build. The answer must be demonstrably yes — "probably" means no.

- [ ] Does the page have a title (textbox or background image with visible page name)?
- [ ] Can you name the page's purpose in one sentence of the form "Shows *audience* *what* so they can *decide what*"?
- [ ] Is the audience named (executive / manager / operator / analyst / mobile), and does the layout match the audience rule?
- [ ] For executive / manager pages: is the headline KPI in the top-left quadrant of the page?
- [ ] Is there a clear visual hierarchy (size, position, or weight distinguishing the primary answer from supporting detail)?
- [ ] Are all adjacent-visual gaps on the page identical (within each band; cross-band splits aligned)?
- [ ] Are all four edge margins identical?
- [ ] Are visual positions derivable from grid math (margin, gap, page width)? If anything looks "close but not quite," fix it.
- [ ] Can you state, for each visual on the page, what question it answers? If any visual fails this test, delete it.
- [ ] Does the report have ≤ 8 pages? If more, are detail pages navigated *to* via drill-through, not via tab click?
- [ ] If you broke 3-30-300, is the reason that the audience isn't arriving cold (scratchpad, detail page, alarm console)? If not, you're wrong; restructure.
- [ ] Is tab order configured in the Selection pane to follow reading order (top-to-bottom, left-to-right within each band)? Are decorative shapes removed from tab order?
- [ ] Does every non-decorative visual have alt text that states the insight (not just the chart type)?

## 2026 platform notes

- **Canvas size in theme JSON (March 2026).** The default canvas size can now be defined in the report theme JSON, applying automatically to all new pages. Set it once at project start rather than adjusting per-page. Target the deployment resolution from day 1 (1280×720, 1600×900, or 1920×1080 as appropriate).
- **PBIR is now the default format.** Reports created or migrated after the March 2026 Desktop release use PBIR (per-page JSON) by default. Page layout and visual coordinates behave identically, but page IDs are now stable hex strings in `definition/pages/<id>/page.json`. Structure decisions are unchanged; only the authoring tool path differs.
- **Visual calculations (GA May 2026).** The new `visualCalc` layer lets you add running totals, rank, or % of parent directly on a visual without a model measure. These don't change band layout decisions, but they do reduce the need for supplementary "context" visuals on analytical pages — a single matrix with visual calculations can replace a matrix + a card band in some analytical page designs.
- **Tab order is a structural decision, not a post-hoc fix.** WCAG 2.1 criterion 2.4.3 (Focus Order) requires that keyboard navigation follows a logical sequence. The correct tab order on a Power BI page mirrors the reading order of the bands: page title → period slicer → other slicers (left-to-right, top-to-bottom) → primary KPI cards → secondary charts → tables → navigation buttons. Decorative shapes, background images, and dividers must be removed from tab order in the Selection pane. Configure tab order during the band-layout phase (Step 4), not after the page is built — retrofitting is error-prone because Power BI assigns tab positions based on visual creation order.
- **Alt text is content, not metadata.** WCAG 2.1 criterion 1.1.1 (Non-text Content) requires alt text on all non-decorative visuals. The alt text should state the insight, not the chart type: "Revenue by region, Q1 2026 — EMEA leads at 42%" not "Bar chart showing regional revenue." For dynamic data, use DAX conditional alt text: `"Revenue = " & FORMAT([Revenue], "#,##0") & ", vs target: " & FORMAT([Variance%], "0%")`. Max 250 characters. Set via Format pane → General → Alt Text. The page title + visual type are already read by screen readers; the alt text adds what they don't provide.

## See also

- [chart-selection.md](chart-selection.md) — once the band is decided, picking the right visual for each slot.
- [density.md](density.md) — how many visuals belong on the page given the audience.
- [cards-and-kpis.md](cards-and-kpis.md) — how to build the headline band.
- [typography-spacing.md](typography-spacing.md) — grid math, alignment, font hierarchy within bands.
- Visual catalog: [`../visual-catalog/INDEX.md`](../visual-catalog/INDEX.md) — mechanical properties of each visual type.
- Rules-layer skill: `reports:pbi-report-design` — prescriptive thresholds this reasoning grounds.
