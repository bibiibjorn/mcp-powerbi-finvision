# Information density

## Principle

"Max 12-15 visuals per page" is a heuristic calibrated to executive dashboards, not a universal truth. Density is correct when the audience expects density and the visuals are cognitively grouped; it is wrong when the audience is glancing or when visuals stand alone without grouping. The real principle is simpler: *the page must fit in working memory as a small number of bands, regardless of how many visuals it contains.*

## Why it works

Human working memory holds roughly 4-7 discrete chunks at once (Miller's 7±2 is the classic number; Cowan's 2001 refinement puts it closer to 4). This is the hard ceiling on how many independent things a reader can hold before the page becomes a search problem rather than a reading problem. Twelve disconnected visuals exceed working memory; twelve visuals grouped into three bands of four fit fine because the reader stores "three bands" plus the currently-focused band's contents.

Sweller's cognitive load theory (1988) separates load into intrinsic (the topic's inherent complexity), extraneous (noise and structure failures the designer added), and germane (productive effort spent integrating new information). Density is not inherently bad — it raises intrinsic load proportionally. What ruins dense pages is *extraneous* load: misaligned visuals, unclear grouping, inconsistent spacing, visuals whose purpose isn't obvious. A dense page with low extraneous load reads fine; a sparse page with high extraneous load (bad alignment, unclear labels) reads worse than the dense one.

Stephen Few's "at-a-glance" threshold (Few, *Information Dashboard Design*, 2013) defines a dashboard as a display that fits on one screen and can be absorbed in seconds. Under that definition, ~8 visuals is the upper bound — anything more and "glance" is no longer the right verb. But plenty of Power BI pages are *not* dashboards. Operational consoles are read all day; detail pages are drilled into with context; scratchpads are for exploration. Applying the dashboard density rule to non-dashboards is what produces either empty pages (agent obeys the rule out of context) or incoherent dashboards (agent breaks the rule without justification).

The audience-adapted rule: density scales with the user's expertise, time on page, and task. A relationship manager reviewing one client's entire holdings expects density; they're staring at the screen for 10 minutes and know the layout. A CFO opening the report Monday morning expects sparsity; they're glancing for 5 seconds. The same underlying data produces two wildly different page densities — and both are correct for their audience.

## How to apply

Density is a single decision derived from four inputs: audience, task, time-on-page, and grouping.

### Step 1 — Name the audience in one word

(This is the same audience classification as in `page-structure.md` — reuse the name.)

- **Executive / glance** — ≤ 8 visuals. Dashboards, overview pages, landing pages.
- **Manager / weekly** — ≤ 12 visuals. More detail, richer slicers; still scanned in under a minute.
- **Operational console** — 15-25 visuals. Staring-at-screen audience, grid-disciplined.
- **Analyst / scratchpad** — 1-5 dominant visuals + supporting filters. Density is distraction.
- **Detail / drill-through** — 10-20 visuals. Audience arrived by right-click; context is already set.
- **Mobile** — ≤ 5 visuals. No exceptions; viewport is the constraint.

Do not guess. Ask the user if unclear. A dashboard for "everyone" is a dashboard for no-one; the audience determines the density, and density determines half the other decisions on the page.

### Step 2 — Count task, not visuals

A single pivotTable with 12 columns is as dense as 12 separate KPI cards. The agent that obeys "≤ 8 visuals" by using one 20-column matrix has not reduced density; it has smuggled it in as one visual. Count the *questions the page answers*, not the visuals.

A useful rule: for each visual, can you state the question it answers in one sentence? If you cannot, the visual adds complexity without adding information. Cut it. Do this before asking whether the page is too dense — removing purposeless visuals often solves density problems without any layout work.

### Step 3 — Group into bands

Within whatever density budget the audience gives you, visuals must be grouped into ≤ 4 cognitive bands (horizontal or vertical regions where the visuals serve a single purpose). Bands are how you fit 20 visuals into 3 working-memory chunks.

- A KPI band (4-6 cards) is one chunk.
- A trend-charts band (2-3 charts sharing a time axis) is one chunk.
- A detail matrix is one chunk.
- A slicer row is one chunk.

Four bands — 20 visuals — one page — one glance. The reader's working memory holds "summary / trend / slicers / detail," and they can focus on any one band without losing the rest.

Pages with > 4 bands or bands with > 8 visuals each drift toward cognitive overload. When you feel yourself adding a fifth band, ask whether that content should be on a second page instead.

### Step 4 — Enforce visual similarity within bands

Gestalt similarity: the reader groups visuals that look alike. That is why a band of 6 KPI cards of the same size, same format, same font feels like one thing. A band with a KPI card, a small chart, a slicer, and a textbox reads as four disconnected things even if they're adjacent.

Within each band, enforce uniformity: same visual type, same size (at least same height), same formatting. Heterogeneity is reserved for the rare case where the contrast *is* the signal (e.g. a single alert callout in a sea of normal cards).

### Step 5 — Whitespace is structure, not empty

Empty space between bands is the membrane that tells the reader *this is one chunk, that is another*. Pages with zero whitespace read as one continuous blur; pages with consistent whitespace between bands read as structured.

A page with 20 visuals and generous between-band whitespace reads better than a page with 12 visuals crammed edge-to-edge. Density is how much you put on the page; whitespace is how you teach the reader to parse it.

### Step 6 — When to cut, when to split

If the page exceeds the audience's density budget even after cutting purposeless visuals and grouping into bands, the answer is **split into two pages**, not compress further. A second page with a clear one-sentence purpose costs the reader nothing (they click once); an overloaded first page costs them every time they look at it.

Symptoms that you need to split:

- You can't name the page's purpose in one sentence (it has two purposes → two pages).
- Different bands address different audiences (executive band + operational band on the same page → split by audience).
- The reader has to scroll (you are past the audience's density budget by definition).

## Worked examples

### Example 1 — R0101 `DIR PARTICIPATIONS - DETAIL` (19 visuals — correctly dense)

**User intent.** A relationship manager is reading everything about one participation: identity, ownership breakdown, key KPIs, transaction history, linked documents, navigation to adjacent views.

**Audience.** Relationship manager, detail drill-through — expert user, time-on-page is minutes not seconds, context already set by the drill.

**Visual count.** 19 visuals: 2 cardVisuals (identity header), 4 multiRowCards (key metrics), 2 donutCharts (ownership breakdown), 1 slicer, 2 shapes, 4 images, 1 actionButton (full-page overlay), 3 bookmarkNavigators.

**Why this density is correct.** The audience expects density, the task requires density, and the visuals are grouped into cognitive bands:
- Header band (identity cards + images + shapes)
- KPI band (multiRowCards)
- Ownership band (donuts)
- Navigation band (bookmarkNavigators + action button)

The reader's working memory holds 4 bands; within each band the visuals are similar enough to chunk together. 19 visuals fit in 4 chunks — within working memory.

**What the agent should reproduce.** Given *"Build a detail page for a single participation,"* the agent should resist the "≤ 12 visuals" rule because it's calibrated for a different audience. Check: is this a drill-through detail page? If yes, density ≤ 25 is acceptable as long as grouped into ≤ 4 bands.

### Example 2 — R0101 `ANALYSIS - TIME SERIES` (1 visual — correctly sparse)

**User intent.** An analyst is exploring rank-change dynamics over time.

**Audience.** Analyst scratchpad.

**Visual count.** 1 (`ribbonChart 344d69fec2b5d61e49cb`, 1541 × 798).

**Why this sparsity is correct.** The page has one question and one visual that answers it. Adding supporting visuals would steal pixels from the ribbonChart, which is cognitively demanding and benefits from every pixel. Adding KPI cards would violate the audience rule (scratchpad doesn't need summary cards). Adding a filter row would be reasonable — but not required.

The agent that sees "1 visual" and feels compelled to "fill the page" has misidentified the audience. Scratchpad pages can and should be visually minimal.

**What the agent should reproduce.** Given *"Show rank change over time for these entities,"* the agent should give the ribbon the whole canvas, add a title, and stop.

### Example 3 — correctly broken the wrong way

Suppose an agent produces a page for a CFO Monday-morning review with 14 visuals: 4 KPIs, a trend line, a regional bar chart, a matrix of returns, two separate waterfall charts, three small pie charts showing different breakdowns, and two textboxes with context.

**This is wrong.** The audience is executive/glance; the budget is ≤ 8 visuals. The agent has used rule-of-thumb "max 15" (manager rule) for an executive audience. The reader spends the 3-second budget hunting for the headline.

**Correct response.** Cut to ≤ 8. The 3 pies are the first to go (at-a-glance rule doesn't need decomposition); the two waterfalls collapse to one; the regional bar chart moves to a second page titled "Regional" where a CFO drills in *if* they want that detail. Result: 4 KPIs, 1 trend, 1 waterfall, 1 matrix = 7 visuals. Reader's 3-second glance finds the KPI band; 30-second scan finds the trend and variance; 300-second drill finds the matrix. The regional page sits one click away.

### Example 4 — operational console (correctly very dense)

**Hypothetical intent.** An operations team stares at a control-room monitor all day watching 24 reconciliation metrics across 6 business units.

**Audience.** Operational console.

**Visual count.** 24 small cardVisuals in a 6×4 grid, each showing one metric with semantic red/amber/green colour and the latest value. Uniformly sized. No charts, no slicers, no detail table — if something needs investigation, the operator clicks through.

**Why this density is correct.** Grid-disciplined, uniform visual type, semantic colour carries the 3-second scan (is anything red?), audience is expert and time-on-page is all day. Working memory holds "6 business units × 4 metrics," which is how the operator already thinks about their work. The page is dense by design, and adding a trend chart or KPI band would fight the operator's mental model.

**What the agent should reproduce.** Given *"Build a ops-monitor page for 24 reconciliation checks,"* the agent should skip the 3-30-300 layout, lay out a uniform grid, rely on semantic colour for alarms, and resist adding supporting charts. Density is the feature.

## Anti-patterns

### "Every metric gets a card"

**What the agent is tempted to produce.** 15 KPI cards at the top of a page because the user listed 15 metrics in the brief.

**Why it's wrong.** 15 equally-weighted numbers is zero numbers. The reader's eye has nowhere to land first; no hierarchy means no priority. Working memory overflows on a KPI band alone, before any other visuals are even read.

**What to do instead.** Pick the 4-6 *decision-grade* metrics (the ones where a 20% change should trigger action) and put them in the KPI band. The remaining 9 metrics go in a detail matrix further down the page, or on a second page.

### "Matrix with 12 columns"

**What the agent is tempted to produce.** One visual, "a matrix," with 12 measure columns because the user asked for "a summary of all the key numbers."

**Why it's wrong.** Visual count is 1, but cognitive load is 12 (the reader is scanning 12 separate measures). The rule says "count tasks, not visuals" — a 12-column matrix is 12 tasks. The density is smuggled in.

**What to do instead.** Cut the matrix to 4-6 measure columns (the decision-grade ones). Move the rest to a detail page or to row-level drill.

### "Fill the page"

**What the agent is tempted to produce.** Sparse content on a big page canvas; agent decides to add two more charts, a filter, and a context textbox to "use the space."

**Why it's wrong.** Whitespace is not wasted space. A page that naturally contains 3 visuals is not improved by adding 5 more to fill pixels — each added visual costs working-memory and dilutes the primary message.

**What to do instead.** Use the whitespace. Size the existing visuals generously. Add a single context line (title, date, data source) if one is missing. That is enough.

### "Everything on one page"

**What the agent is tempted to produce.** One page with 25 visuals covering summary, trend, regional breakdown, product breakdown, time breakdown, and customer detail. Because the user said "I want a dashboard."

**Why it's wrong.** The user said "dashboard" meaning "a thing where I can see what's going on"; they did not mean "dump everything on one canvas." 25 visuals across heterogeneous bands breaks grouping, breaks working memory, breaks the 3-second glance.

**What to do instead.** Split into 3-4 pages, each with one clear purpose: summary, by-region, by-product, customer-detail. Name each page with a complete thought. The user's navigation cost is one click per page; the payoff is each page is readable.

### "Scroll to see it all"

**What the agent is tempted to produce.** A page designed to be scrolled — KPI band at the top, trend charts in the middle, a 2000-pixel-tall matrix at the bottom.

**Why it's wrong.** Power BI pages are not web pages. They're presented as fixed canvases (default 1280×720 or 1920×1080). Content below the fold on the canvas is often truncated or silently clipped, depending on render context (Desktop vs Service vs embedded). Readers don't scroll Power BI pages the way they scroll Medium articles — they expect the page to fit.

**What to do instead.** Fit the page to the canvas. If more content is needed, paginate.

### "Empty space fear"

**What the agent is tempted to produce.** Tight spacing, visuals edge-to-edge, no bands, because "the user wants their money's worth of pixels."

**Why it's wrong.** Gestalt proximity makes zero-gap visuals into one chunk; the reader loses the ability to parse them separately. The visual density climbs fast but the information density — what the reader actually extracts — climbs slowly or falls.

**What to do instead.** 16-24 px gap between visuals within a band; 32-48 px between bands. Equal margins on all four edges. Whitespace is structure.

## Self-audit checklist

- [ ] Can the target audience be named in one word (executive / manager / operator / analyst / detail / mobile)?
- [ ] Is visual count within the band for that audience (exec ≤ 8, manager ≤ 12, operator ≤ 25, detail ≤ 20, analyst ≤ 5, mobile ≤ 5)?
- [ ] Can you state, for each visual on the page, the one-sentence question it answers? If any fails, is the visual purposeless and should be cut?
- [ ] Are visuals grouped into ≤ 4 cognitive bands?
- [ ] Within each band, are visuals visually similar (same type, same size, same formatting) — except where a contrast carries meaning?
- [ ] Is there consistent whitespace (16-24 px within-band; 32-48 px between bands)?
- [ ] Does any single "visual" (often a matrix) carry density that should be counted against the budget? If so, recount.
- [ ] If the page exceeds the budget, have you split into two pages — or compressed into one by cutting purposeless visuals — before trying to compress further?
- [ ] Does the page fit the canvas (no scroll, no truncation)?
- [ ] Would a reader from the named audience describe the page, after 3 seconds, as "organised" rather than "busy"?

## 2026 platform notes

- **Visual calculations reduce supplementary-visual sprawl.** Running totals, % of parent, and rank as `visualCalc` expressions live inside an existing matrix or chart — no separate "context" visual required. Use this to reduce visual count on analytical pages without losing information. Fewer visuals for the same information = correct direction for any density budget.
- **Scroll is still a canvas constraint.** Power BI Service pages remain fixed-canvas; content below the viewport is clipped or requires explicit scroll enabled (paginated reports). The "no scroll" rule for dashboard pages remains correct. Paginated reports are a separate format with different density rules.
- **DirectQuery and performance.** On pages served by Direct Lake or DirectQuery models, visual count has a performance dimension beyond cognitive load: each visual fires one or more queries. On operational console pages with 20+ visuals and auto-refresh ≤ 15 min, measure query cost alongside cognitive load. Pages that are correct by cognitive rules may still be wrong by query-concurrency rules on high-refresh Direct Lake models.

## See also

- [page-structure.md](page-structure.md) — audience classification and band layout.
- [chart-selection.md](chart-selection.md) — reducing visual count by picking the right chart rather than many charts.
- [cards-and-kpis.md](cards-and-kpis.md) — how to keep the KPI band tight.
- [interactivity.md](interactivity.md) — slicers and drill-through as a density-management tool.
- Rules-layer skill: `reports:pbi-report-design` — performance-based density thresholds (visuals per page).
