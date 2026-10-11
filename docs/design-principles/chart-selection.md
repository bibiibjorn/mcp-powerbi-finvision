# Chart selection

## Principle

Pick a chart by **data shape** and **comparison type** first; aesthetic preferences last. Cleveland & McGill's perceptual accuracy rank (position on a common scale > position on non-aligned scales > length > angle / slope > area > volume > color saturation) is the tiebreaker whenever two chart types could carry the same data. A chart is well-chosen when the reader can decode the number without conscious effort — that is the goal; beauty is a side effect.

## Why it works

Cleveland & McGill (1984, *JASA*) ran controlled experiments measuring how accurately readers extract quantitative values from different visual encodings. The ranking is durable across replications and is the closest thing visualization has to a physical law:

1. Position on a common scale (bar charts, column charts, dot plots)
2. Position on non-aligned scales (multi-panel charts)
3. Length (unstacked bars, length of segments)
4. Angle and slope (line charts, pie chart angles)
5. Area (bubble charts, treemaps)
6. Volume and curvature (3D charts — never use)
7. Color saturation and hue (heatmaps — useful for patterns, imprecise for values)

The practical consequence: if a bar chart can carry the data, it is almost always a better choice than a pie, a donut, an area chart, or a bubble chart. The reader decodes bar lengths more accurately, faster, with less working-memory load. Every other choice trades some of that accuracy for something else (multi-dimension encoding, share-of-total metaphor, time continuity) — and that trade has to be justified.

Preattentive attributes (Ware, ch. 5) add a second constraint: certain visual features are detected in ~200 ms without conscious scanning. Position, length, orientation, motion, and hue are preattentive; text and precise color matching are not. A chart that encodes the signal preattentively ("the red bar is the tallest") communicates before the reader commits attention. A chart that requires reading axis labels to decode the signal ("this line is the revenue line if you check the legend") has lost the preattentive advantage — and the reader pays for it every time they look at the chart.

Finally, user tasks matter. Different comparison types map to different chart strengths:

| Reader task | Best encoding | Why |
|-------------|---------------|-----|
| Compare values across categories | Bar / column (length on common scale) | Cleveland-McGill #1 |
| See trend over continuous time | Line (slope on common scale) | Continuity implied by connected marks |
| See change across discrete periods | Column | Gaps between columns signal discreteness |
| Rank items | Horizontal bar, sorted | Length + reading order |
| Share of total | Stacked bar / 100% bar | Length better than angle |
| Variance (actual vs reference) | Waterfall, variance column | Purpose-built for delta semantics |
| Distribution shape | Histogram, box-and-whisker | Shape is the whole point |
| Correlation | Scatter | Position on two common scales |
| Detail lookup / drill | Matrix, table | Reading precise values beats any chart |
| Rank change over time | Ribbon / bump | Built for exactly this |
| Two measures, one category, shared time | Combo (line + column) | Avoids dual-axis where possible |

The rest of this file is how to apply these rankings in practice, including when to break them.

## How to apply

Chart selection is a decision tree, but prose reasoning beats flowcharts because user intent is always ambiguous. Work in this order.

### Step 1 — Name the comparison type

Before looking at any chart type, name the comparison the visual is making. If you can't name it in one word, the visual has no purpose and should not exist:

- **Trend** — value over continuous time
- **Period change** — value across discrete periods (Q1 vs Q2 vs Q3)
- **Ranking** — categories ordered by a measure
- **Share** — parts summing to a whole
- **Variance** — actual vs reference (budget, prior period, target)
- **Distribution** — shape of values within a population
- **Correlation** — relationship between two measures
- **Detail** — precise values for lookup
- **Rank change** — how rank shifts over time
- **Composition over time** — how the mix of parts evolves

If the user request doesn't fit any of these, ask them to restate it in one of these terms. "Show sales" is not a comparison — *sales compared to what, how?*

### Step 2 — Pick the strongest encoding for that type

For each comparison type, the default is:

- **Trend.** `lineChart` for a continuous series (daily, monthly sequences). `columnChart` for ≤ 12 discrete periods. Never a line chart on categorical x-axis — the line implies continuity the data doesn't have.
- **Period change.** `columnChart`, not line. Gaps between columns reinforce discreteness.
- **Ranking.** `clusteredBarChart` (horizontal) for long category labels; `columnChart` (vertical) for short labels and ≤ 8 bars. Sort descending by the measure; alphabetical sort on a ranking chart wastes the sort signal.
- **Share.** `barChart` or stacked 100% column for ≥ 4 slices. `donutChart` for 2-3 slices where quick *identity check* (which category is dominant?) matters more than precision. `pieChart` — effectively never; donut is the same encoding with a label-friendly center hole.
- **Variance.** `waterfallChart` for stepwise variance decomposition (start → delta → delta → end). Variance column chart (red/green columns above/below zero) for period-over-period deltas. IBCS notation adds semantic fills (solid = actual, hollow = budget, hatched = forecast).
- **Distribution.** Histogram (stacked `columnChart` with binned x). SVG box-and-whisker measure for a quick summary. Never a pie chart of "distribution."
- **Correlation.** `scatterChart` with positioned points. Size encoding for a third measure if genuinely needed; color for a fourth only if categorical.
- **Detail.** `pivotTable` (matrix) for hierarchical data (Region → Country → City). `tableEx` for flat rows. Show full precision; tables are where readers go for exact values.
- **Rank change.** `ribbonChart` — purpose-built. Nothing else encodes this well in native PBI.
- **Composition over time.** `lineStackedColumnComboChart` if one series is a line and the rest are stacked. Stacked `columnChart` if all are columns.

### Step 3 — Apply the Cleveland-McGill tiebreaker

When two chart types could carry the data, pick the higher-ranked encoding. Examples:

- Share of total with 5 slices → bar (length on common scale, rank #1) beats donut (angle, rank #4).
- Two measures across 10 categories → clustered columns (position on common scale) beat stacked columns (only the bottom segment is on common scale).
- Revenue by region → sorted bar (length + reading order) beats a map (position encoding wasted on geography; reader can't extract values).

### Step 4 — Justify the weaker choice if you make one

Breaking the rank is legitimate when the weaker encoding serves a task the stronger one can't:

- **Donut wins over bar** when the page already has five bar charts and the reader needs a visual variety signal that says "this one is share-of-total, glance and move on." But only with ≤ 3 slices, only when precision doesn't matter.
- **Map wins over bar** when spatial relationships are the point (neighboring regions are correlated; disease clusters). Not when "the data has a region column" — that's not enough.
- **Dual-axis line chart wins over combo chart** … essentially never. If two measures share a time axis and have different scales, use a combo (`lineClusteredColumnComboChart` or `lineStackedColumnComboChart`) or small multiples. Dual-axis invites misreading; readers assume the two lines share a scale when they don't.

### Step 5 — Verify field bindings match the chart's strengths

Every data visual has role slots (see the visual catalog per-type files). Picking the right chart is wasted if the fields are in the wrong roles:

- `lineChart` Category role wants a continuous sequence (date). If the Category is categorical text, you picked the wrong chart.
- `donutChart` Values role wants a single measure; Category role wants ≤ 3 categories. If Category has 15 values, you picked the wrong chart.
- `pivotTable` Rows role wants a hierarchy; `tableEx` Values role wants a flat column list. If you're stacking hierarchy fields on a `tableEx`, the matrix was the correct pick.
- `waterfallChart` Category role wants an ordered sequence (months, or steps). Unordered variance loses the walk.

When fields don't naturally fit, reconsider the chart before reshaping the data. The chart is a signal about what comparison you are actually making.

### Step 6 — Tune visual formatting to the encoding, not against it

Once the chart type is correct, formatting choices reinforce or fight the encoding:

- **Sort descending** on ranking charts. Keep the first bar against the axis; sort mechanics shouldn't require thought.
- **Minimise gridlines and axis clutter.** The data-ink ratio (Tufte, 1983) says every pixel not carrying data is competing with the data. Keep major gridlines; drop minor.
- **Muted neutral bars with one accent bar** when you want to highlight. Preattentive saturation does the work.
- **Labels on top of bars** when there are ≤ 8 bars and the user needs the exact value. For ≥ 10 bars, y-axis labels are cleaner.
- **Omit the legend** on single-series charts — the title + axis labels carry it.
- **No 3D, ever.** 3D defeats the encoding: column heights no longer map linearly to value because of perspective; front columns occlude back. If the user asks for 3D, push back.

## Worked examples

### Example 1 — R0101 `STRATEGIC ALLOCATION` waterfallChart (variance, correctly chosen)

**User intent.** Show how strategic-allocation variance decomposes: start target → deltas by asset class → end actual.

**Data shape.** Ordered sequence of contributions (start, +cash, -equity, +fixed-income, … , end), summing to a total delta.

**Decision.** `waterfallChart` (`f82168f249e80838e597` at 680×385 on STRATEGIC ALLOCATION). Built for this exact pattern: baseline → signed steps → total. Red for negative steps, green for positive, grey for the end total. Readers decode it without instruction because the visual mirrors the mental model of "what moved and how much."

**Why this beats the alternatives.** A column chart of signed deltas would show the same numbers but lose the *connection* — readers would have to mentally sum the columns to get the total walk. A variance table would be precise but not preattentive; the user has to read every cell. A pie chart of the contributions would be worst — ignores sign, ignores order.

**What the agent would produce.** Given *"Show me how allocation changed versus target, broken down by asset class,"* the agent should reach for waterfallChart immediately, bind the asset-class column to Category in the correct ordered sequence, bind the signed delta measure to Y. Colour the positive/negative bars semantically.

### Example 2 — R0101 `PORFTOLIO - INSTRUMENT BREAKDOWN` pivotTable vs tableEx (correctly distinguished)

**User intent.** Show detailed instrument-level data; allow the user to roll up by instrument type.

**Data shape.** Instrument Type → Sub-type → Instrument. Two hierarchical levels above the detail row.

**Decision.** `pivotTable` (`9c3e638a9a273a697eb0`, 1876×499). The page also has a `tableEx` visual (`a932b7e74d1812022c76`, 1879×500) for a different flat-list purpose (e.g. a filter-respecting detail table without hierarchy).

**Why the split.** `pivotTable` encodes hierarchy through indentation and expand/collapse icons — readers can drill without losing context. `tableEx` is flat; every row is independent. Forcing a hierarchical dataset into `tableEx` strips the structure the reader needs. Forcing a flat dataset into `pivotTable` wastes the matrix's rollup columns and produces redundant subtotals.

**What the agent would produce.** Given *"Show me instruments organised by type,"* the agent should pick `pivotTable`, bind type → sub-type → instrument to the Rows role, bind the measures to Values. Given *"Give me a flat list of all instruments with their key metrics,"* the agent should pick `tableEx`.

### Example 3 — R0101 donutChart on `DIR PARTICIPATIONS - DETAIL` (correctly breaking the Cleveland-McGill rank)

**User intent.** On a single-participation detail page, show the ownership split between a handful of parties (typically 2-4).

**Data shape.** Categorical (party) × single measure (% ownership). Always sums to 100%.

**Decision.** `donutChart` (two of them: `2069adf85e7ba6a735a1` and `b8e4917a6f03f2137dde`, each 460×370). A bar chart would be strictly more accurate for reading percentages — but the audience here doesn't need precision; they need to *identify* who owns the entity at a glance. Donut wins because the circle is a strong identity signal ("this is a split"), and the slice colours map to party colours established elsewhere on the page.

**Why this is correct.** The comparison type is *share*, precision is secondary, slice count is 2-4, and the page has five other visual types (cardVisual, multiRowCard, matrix, image, shape) — a bar chart would add to the visual monotony without serving a task the donut can't. The break from Cleveland-McGill is deliberate and small.

**What the agent should NOT do.** Use a donut for "top 10 holdings by value" (8+ slices, precision matters, should be a sorted bar chart). The donut exception is narrow: ≤ 3-4 slices AND identity > precision.

### Example 4 — correctly broken, the wrong way

Suppose an agent produces: a line chart with x-axis = asset class (cash, equity, fixed income, alternatives) and y = value.

**This is wrong.** Asset class is categorical, not continuous. The line connecting cash → equity → fixed income is meaningless — it implies a progression that doesn't exist. A reader's preattentive system reads "trend" where none is present.

**Correct.** A `columnChart` or sorted `barChart`. Columns have gaps (signal discreteness). Sorted by value, the chart also tells the reader "equity is biggest" at a glance.

## Anti-patterns

### "Pie of eight"

**What the agent is tempted to produce.** A pie or donut chart with 8+ slices because the user said "show the breakdown."

**Why it's wrong.** Angle encoding (Cleveland-McGill rank #4) collapses at high slice counts. Small slices become indistinguishable; the legend becomes a reading problem; labels overlap. Readers can't rank the slices and can't read the values.

**What to do instead.** Sorted bar chart with the top 5-7 categories, everything else collapsed into "Other." If the full set genuinely matters, use a 100% stacked bar (length on common scale) or a sorted matrix.

### "Line on categories"

**What the agent is tempted to produce.** A line chart where the x-axis is a non-temporal category (region, product, department).

**Why it's wrong.** Line charts preattentively encode *trend*. The eye reads the connected line as "value is changing over a continuous dimension." When the dimension is categorical, the reader's brain invents a progression that isn't in the data. Worse, reordering the categories visibly changes the chart's shape — a signal that the shape means nothing.

**What to do instead.** Column chart for categorical x-axis. Always.

### "Stacked for ranking"

**What the agent is tempted to produce.** Stacked bar chart when the user wants to rank items across multiple series.

**Why it's wrong.** Only the bottom segment of a stacked bar is on a common scale (rank #1). Every other segment is on a shifted baseline (rank #3 at best, usually worse). Readers can compare the bottoms of the bars; they cannot compare the tops or the middles. If ranking is the task, stacking fights it.

**What to do instead.** Clustered bar for direct comparison across series, OR separate sorted bar charts per series (small multiples). Stack only when the *sum* is the thing being ranked, not the parts.

### "3D anything"

**What the agent is tempted to produce.** A 3D column chart, 3D pie, 3D area. Usually because the user asked for "visual impact."

**Why it's wrong.** Perspective distorts the encoding non-linearly — a column twice as tall does not look twice as tall in 3D. Front elements occlude back. The chart is no longer a faithful representation of the data.

**What to do instead.** 2D. Always. If the user wants visual impact, give them a well-formatted 2D chart with one accent colour and clean typography. That reads as professional; 3D reads as amateurish.

### "Gauge for a KPI"

**What the agent is tempted to produce.** A gauge visual for "percentage of target achieved" because it looks dashboard-y.

**Why it's wrong.** Gauges use angle and area — ranks #4 and #5 in Cleveland-McGill. They waste huge amounts of screen space encoding a single value. Readers can't quickly distinguish 73% from 77% on a gauge; they can on a number with a target line.

**What to do instead.** A KPI card with the number, the target, the gap, and a small horizontal target-line or bullet chart. Same information, 1/5 the pixel budget, better accuracy.

### "Dual-axis without a reason"

**What the agent is tempted to produce.** A line chart with revenue (€) on left axis and units (count) on right axis, same time x-axis.

**Why it's wrong.** Readers assume two lines on the same chart share a scale. When they don't, the apparent correlation or crossing of the lines is visual noise, not signal. A "crossover point" in dual-axis charts is almost always a lie.

**What to do instead.** Small multiples (two stacked charts sharing the x-axis) or a `lineClusteredColumnComboChart` where the distinction between line and column is itself the signal that the measures are different. If dual-axis is truly unavoidable, annotate the right axis explicitly and use different colors aggressively; but expect some readers to misread it anyway.

### "Map because there's a region column"

**What the agent is tempted to produce.** A map visual because the dataset includes country or region.

**Why it's wrong.** Maps encode by geographic position, which is fixed regardless of value. Reading quantitative values off a map is Cleveland-McGill rank #5-7 (color saturation). Unless spatial relationships matter (neighbouring countries are correlated, disease cluster patterns), the map costs you precision for a metaphor the reader didn't need.

**What to do instead.** Sorted bar chart by country. 20× faster to read, ranks the countries explicitly, no colour-to-value guesswork.

## Self-audit checklist

- [ ] Can you name the comparison type of each data visual in one word (trend / period / ranking / share / variance / distribution / correlation / detail / rank change / composition over time)?
- [ ] Is the chart type the strongest Cleveland-McGill encoding for that comparison type? If not, is the weaker choice justified in one sentence?
- [ ] Does every visual's Category/Axis role carry the right kind of data (continuous for lines, categorical for columns, hierarchical for matrices)?
- [ ] Is every field binding to a real model field (no dangling references)?
- [ ] Are donut / pie charts used only with ≤ 3-4 slices AND identity > precision?
- [ ] Are ranking charts sorted descending by measure?
- [ ] Is 3D off everywhere?
- [ ] Does every chart omit gridlines that don't add information (minor gridlines off, major trimmed to what's needed)?
- [ ] On single-series charts, is the legend omitted (title carries it)?
- [ ] If a dual-axis chart is used, is there a reason that couldn't be solved with a combo or small multiples?
- [ ] Can you point at any chart and answer "what does this tell the reader in 1 second?" If no, reconsider the chart type, not just the formatting.
- [ ] On variance/waterfall charts: is the baseline at zero? Are positive/negative steps coloured semantically AND paired with the bar direction (position redundancy) so CVD readers can decode direction without colour?
- [ ] If the organisation uses IBCS notation: do actual bars use solid fill, plan/budget bars use hollow/outlined treatment, and forecast bars use hatched or distinct fill?

## 2026 platform notes

- **Visual calculations (GA May 2026).** Running totals, rank, % of parent, and moving averages can now be computed in the visual layer (a new `visualCalc` expression, not a model measure). This changes the decision tree for certain comparison types: a "rank" or "running total" visual calc is often simpler and more maintainable than a dedicated RANKX / SUMX measure. When the calculation applies to one specific visual and has no cross-visual dependency, prefer visual calculations over model measures.
- **R and Python visuals deprecated for Embed for your customers (May 2026).** If the report will be embedded in an application via the "Embed for your customers" scenario, R and Python visuals are now deprecated for that context. Use Deneb/Vega-Lite (`reports:deneb-visuals`) or SVG DAX measures (`reports:svg-visuals`) as replacements. They still work in standard Service and Desktop reports.
- **Deprecation: gauge/speedometer.** Still supported natively, but the anti-pattern guidance here (replace with cardVisual + sparkline) aligns with Microsoft's own guidance as of 2026. The `cardVisual` (new card, 2024+) is the platform-recommended replacement.
- **IBCS variance charts.** The International Business Communication Standards (IBCS v1.2) complement Cleveland & McGill by adding *semantic fill* conventions for variance charts: solid fill = actual (AC), hollow/outlined = plan/budget (PL), hatched = forecast (FC), grey = prior year (PY). Power BI implements these via ZebraBI/certified custom visuals, or approximately via theme `good`/`bad` sentiment colours + hollow-style reference bars. When authoring a waterfall or variance-column chart on a report where the organisation follows IBCS, ensure: (1) the baseline and total bars are neutral grey; (2) positive steps use the `good` sentiment colour; (3) negative steps use the `bad` colour; (4) target/plan bars use an outlined (hollow) visual treatment distinct from actual fill. The self-audit checklist includes an IBCS compliance section.
- **Accessible colour on variance charts.** A red/green variance bar chart fails for ~8 % of male readers who have red-green colour-vision deficiency. Always pair variance colour with the sign of the bar (above/below zero baseline) as a redundant encoding. See `color.md` for CVD-safe palette alternatives (blue/orange as a theme-level substitute).

## See also

- [page-structure.md](page-structure.md) — which band the chart belongs to.
- [color.md](color.md) — semantic encoding, emphasis, variance colouring.
- [cards-and-kpis.md](cards-and-kpis.md) — when a card is the right choice instead of a chart.
- [typography-spacing.md](typography-spacing.md) — label typography, axis font, spacing around charts.
- Visual catalog: [`../visual-catalog/INDEX.md`](../visual-catalog/INDEX.md) — every supported chart type's roles, properties, and templates.
- Rules-layer skill: `reports:pbi-report-design`, section "Charts" — prescriptive rules this reasoning grounds.
- Custom visuals (out-of-native-chart cases): `reports:deneb-visuals`, `reports:svg-visuals`, `reports:python-visuals`, `reports:r-visuals`.
