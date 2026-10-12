# advancedSlicerVisual

## Overview

<!-- MANUAL: overview -->
`advancedSlicerVisual` is the modern replacement for the legacy `slicer`, introduced to support **hierarchical selection, search, and large value sets** natively. It accepts multiple columns into the `Values` role (stepping down a hierarchy) and renders a richer UI: search box, expand/collapse tree, multi-select with checkboxes. Since 2024 it is the recommended default for any slicer with >20 distinct values or with a natural parent/child structure.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `advancedSlicerVisual` when:

- Users need to **search** for a value (entity by name, security by ticker).
- The filter column has a **hierarchy** (region → country → city, sector → industry → ticker).
- The value set is **large** (>20 items) and the legacy slicer becomes a long scroll.
- **Multi-select with counts** or tri-state parent selection is desired.

Prefer the legacy `slicer` when: the value set is small and fixed (Actual / Simul 1 / Simul 2), or you need a date-range picker or numeric-range picker (those modes are only on the legacy type).
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts |
|------|----------|----------|---------|
| `Values` | yes | yes | Column |

## Containers

- **background** (5 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (2 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **lockAspect** (1 properties) — documented inline below
- **padding** (6 properties) — see [./_shared/containers-padding.md](./_shared/containers-padding.md)
- **spacing** (1 properties) — documented inline below
- **stylePreset** (1 properties) — documented inline below
- **subTitle** (11 properties) — documented inline below
- **title** (12 properties) — see [./_shared/containers-title.md](./_shared/containers-title.md)
- **visualHeader** (25 properties) — see [./_shared/containers-visualHeader.md](./_shared/containers-visualHeader.md)
- **visualHeaderTooltip** (13 properties) — documented inline below
- **visualLink** (13 properties) — documented inline below
- **visualTooltip** (17 properties) — documented inline below
- **accentBar** (6 properties) — documented inline below
- **actionState** (5 properties) — documented inline below
- **behavior** (1 properties) — documented inline below
- **data** (1 properties) — documented inline below
- **fillCustom** (17 properties) — documented inline below
- **glowCustom** (9 properties) — documented inline below
- **icon** (10 properties) — documented inline below
- **image** (23 properties) — documented inline below
- **label** (17 properties) — documented inline below
- **layout** (54 properties) — documented inline below
- **outline** (5 properties) — documented inline below
- **overFlow** (2 properties) — documented inline below
- **rotation** (3 properties) — documented inline below
- **selection** (4 properties) — documented inline below
- **selectionIcon** (11 properties) — documented inline below
- **shadowCustom** (9 properties) — documented inline below
- **shapeCustomRectangle** (14 properties) — documented inline below
- **value** (17 properties) — documented inline below

## Properties

### background

See [./_shared/containers-background.md](./_shared/containers-background.md).

### border

See [./_shared/containers-border.md](./_shared/containers-border.md).

### divider

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no | Color of the divider line between header and content areas. |
| `ignorePadding` | `boolean` | — | — | no | Ignore padding |
| `show` | `boolean` | — | — | no | Show |
| `style` | `string` | — | — | no | Line style |
| `width` | `number` | — | — | no | Width |

### dropShadow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no | Color of the drop shadow. Note: drop shadows create accessibility issues for users with vestibular disabilities -- prefer flat layout with background contrast. |
| `position` | `string` | — | — | no | Shadow position |
| `preset` | `string` | — | — | no | Preset |
| `shadowBlur` | `number` | — | — | no | Blur |
| `shadowDistance` | `number` | — | — | no | Distance |
| `shadowSpread` | `number` | — | — | no | Size |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### general

See [./_shared/containers-general.md](./_shared/containers-general.md).

### lockAspect

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | — | — | no | Show |

### padding

See [./_shared/containers-padding.md](./_shared/containers-padding.md).

### spacing

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `verticalSpacing` | `integer` | — | — | no | Space between label and value |

### stylePreset

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `name` | `string` | — | — | no | Style |

### subTitle

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no | Horizontal alignment of the subtitle text. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontColor` | `object` | — | — | no | Font color for the subtitle. |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no | Font size for the subtitle text (6-45pt). |
| `heading` | `string` | — | — | no | Set the heading tag for your visual titles to help with screen reader accessibility. This won't impact the visual styling of your titles. |
| `italic` | `boolean` | — | — | no | Italic |
| `show` | `boolean` | — | — | no | Show |
| `text` | `string` | — | — | no | The name of the visual |
| `titleWrap` | `boolean` | — | — | no | Word wrap |
| `underline` | `boolean` | — | — | no | Underline |

### title

See [./_shared/containers-title.md](./_shared/containers-title.md).

### visualHeader

See [./_shared/containers-visualHeader.md](./_shared/containers-visualHeader.md).

### visualHeaderTooltip

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `background` | `object` | — | — | no | Background color for the visual header tooltip popup. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontFamily` | `string` | — | — | no | Font family for the tooltip |
| `fontSize` | `number` | — | — | no | Font size for visual header tooltip text. |
| `italic` | `boolean` | — | — | no | Italic |
| `section` | `string` | — | — | no | Choose the page you want to use for your tooltip |
| `text` | `string` | — | — | no | Write the custom tooltip that will show up when a report page tooltip hasn't been chosen or isn't supported |
| `themedBackground` | `object` | — | — | no | Theme-aware background color for header tooltips. |
| `themedTitleFontColor` | `object` | — | — | no | Theme-aware font color for header tooltip title. |
| `titleFontColor` | `object` | — | — | no | Font color for header tooltip title text. |
| `transparency` | `number` | — | — | no | Set transparency for the tooltip background |
| `type` | `string` | — | — | no | Type |
| `underline` | `boolean` | — | — | no | Underline |

### visualLink

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bookmark` | `string` | — | — | no | Choose which bookmark will open when this element is selected. |
| `dataFunction` | `object` | — | — | no |  |
| `disabledTooltip` | `string` | — | — | no | Disabled tooltip |
| `drillthroughSection` | `string` | — | — | no | Select a single destination or use conditional formatting for custom destination logic, such as multi-destination support. |
| `enabledTooltip` | `string` | — | — | no | Enabled tooltip |
| `navigationSection` | `string` | — | — | no | Select a single destination or use conditional formatting for custom destination logic, such as multi-destination support. |
| `show` | `boolean` | — | — | no | Show |
| `showDefaultTooltip` | `boolean` | — | — | no | Show tooltips |
| `suppressDefaultTooltip` | `boolean` | — | — | no | No tooltips |
| `tooltip` | `string` | — | — | no | Write the custom tooltip that will show when users hover over this element. |
| `tooltipPlaceholderText` | `string` | — | — | no | Placeholder for tooltip |
| `type` | `string` | — | — | no | Choose what action will occur when this element is selected. |
| `webUrl` | `string` | — | — | no | Web URL |

### visualTooltip

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `actionFontColor` | `object` | — | — | no | Font color for tooltip action text (e.g., drill-through links). |
| `background` | `object` | — | — | no | Background color for the tooltip popup. |
| `bold` | `boolean` | — | — | no | Bold |
| `fontFamily` | `string` | — | — | no | Font family for the tooltip |
| `fontSize` | `number` | — | — | no | Font size for tooltip text. |
| `italic` | `boolean` | — | — | no | Italic |
| `section` | `string` | — | — | no | Choose the page you want to use for your tooltip |
| `show` | `boolean` | — | — | no | Show |
| `showActionsInTooltips` | `boolean` | — | — | no | Add drill options within tooltips |
| `themedBackground` | `object` | — | — | no | Theme-aware background color for tooltips. |
| `themedTitleFontColor` | `object` | — | — | no | Theme-aware font color for tooltip title. |
| `themedValueFontColor` | `object` | — | — | no | Theme-aware font color for tooltip values. |
| `titleFontColor` | `object` | — | — | no | Font color for tooltip title text. |
| `transparency` | `number` | — | — | no | Set transparency for the tooltip background |
| `type` | `string` | — | — | no | Allows report pages to be used as tooltips for this visual |
| `underline` | `boolean` | — | — | no | Underline |
| `valueFontColor` | `object` | — | — | no | Font color for tooltip value text. |

### accentBar

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `color` | `object` | — | — | no |  |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |
| `width` | `number` | — | — | no | Width |

### actionState

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `IsButtonsAdvanced` | `boolean` | — | — | no | Advanced |
| `isCalloutValuesAdvanced` | `boolean` | — | — | no | Advanced |
| `isExpansionIconAdvanced` | `boolean` | — | — | no | Advanced |
| `isImageAdvanced` | `boolean` | — | — | no | Advanced |
| `isSelectionIconAdvanced` | `boolean` | — | — | no | Advanced |

### behavior

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `disabledItemsOrder` | `string` | — | — | no | Placement |

### data

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `isInvertedSelectionMode` | `boolean` | — | — | no | Invert selection -- exclude selected items instead of including them. |

### fillCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `blur` | `integer` | — | — | no | Blur |
| `contrast` | `integer` | — | — | no | Contrast |
| `effects` | `boolean` | — | — | no | Image effects |
| `exposure` | `integer` | — | — | no | Exposure |
| `field` | `string` | — | — | no | Field |
| `fillColor` | `object` | — | — | no |  |
| `fit` | `string` | — | — | no | Image fit |
| `height` | `integer` | — | — | no | Image height |
| `image` | `object` | — | — | no |  |
| `imageShow` | `boolean` | — | — | no | Add image |
| `imageSplitTransparency` | `boolean` | — | — | no |  |
| `imageTransparency` | `number` | — | — | no | Background color transparency |
| `saturation` | `integer` | — | — | no | Saturation |
| `show` | `boolean` | — | — | no | Show |
| `sourceType` | `string` | — | — | no | Image source |
| `transparency` | `number` | — | — | no | Background color transparency |
| `url` | `string` | — | — | no | Image URL |

### glowCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no |  |
| `glowDistance` | `number` | — | — | no | Distance |
| `glowPositionPreset` | `string` | — | — | no | Preset |
| `glowSpread` | `number` | — | — | no | Size |
| `position` | `string` | — | — | no | Offset |
| `shadowBlur` | `number` | — | — | no | Blur |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### icon

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `altText` | `string` | — | — | no | Alt text |
| `icon` | `object` | — | — | no |  |
| `iconType` | `string` | — | — | no | Icon type |
| `iconUrl` | `string` | — | — | no | Image URL |
| `padding` | `integer` | — | — | no | Padding |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `size` | `integer` | — | — | no | Size |
| `transparency` | `number` | — | — | no | Background color transparency |

### image

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `altText` | `string` | — | — | no | Alt text |
| `blur` | `integer` | — | — | no | Blur |
| `contrast` | `integer` | — | — | no | Contrast |
| `effects` | `boolean` | — | — | no | Image effects |
| `enhancedBlur` | `integer` | — | — | no | Blur |
| `enhancedFit` | `string` | — | — | no | Image fit |
| `enhancedSaturation` | `integer` | — | — | no | Saturation |
| `exposure` | `integer` | — | — | no | Exposure |
| `height` | `integer` | — | — | no | Image height |
| `ignorePadding` | `boolean` | — | — | no | Image ignores buttons padding and reaches the edges. |
| `image` | `string` | — | — | no | Field |
| `imageAreaSize` | `integer` | — | — | no | Image area size |
| `imageFile` | `object` | — | — | no |  |
| `imageFit` | `string` | — | — | no | Image fit |
| `padding` | `integer` | — | — | no | Space between image and callout |
| `position` | `string` | — | — | no | Position |
| `saturation` | `integer` | — | — | no | Saturation |
| `setAsBackGround` | `boolean` | — | — | no | Set as background |
| `show` | `boolean` | — | — | no | Show |
| `sourceType` | `string` | — | — | no | Image source |
| `transparency` | `number` | — | — | no | Background color transparency |
| `url` | `string` | — | — | no | Image URL |

### label

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `alignBaselines` | `boolean` | — | — | no | Align baselines |
| `bold` | `boolean` | — | — | no | Bold |
| `field` | `string | integer` | — | — | no | Field |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `heading` | `string` | — | — | no | Heading |
| `horizontalAlignment` | `string` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `labelSource` | `string` | — | — | no | Label source |
| `matchValueAlignment` | `boolean` | — | — | no | Match value alignment |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `transparency` | `number` | — | — | no | Transparency |
| `underline` | `boolean` | — | — | no | Underline |

### layout

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `alignment` | `string` | — | — | no |  |
| `autoGrid` | `boolean` | — | — | no | Autogrid |
| `backgroundFillColor` | `object` | — | — | no |  |
| `backgroundImage` | `object` | — | — | no |  |
| `backgroundImageBlur` | `integer` | — | — | no | Blur |
| `backgroundImageContrast` | `integer` | — | — | no | Contrast |
| `backgroundImageData` | `string` | — | — | no | Data |
| `backgroundImageEffects` | `boolean` | — | — | no | Image effects |
| `backgroundImageExposure` | `integer` | — | — | no | Exposure |
| `backgroundImageFit` | `string` | — | — | no | Image fit |
| `backgroundImageHeight` | `integer` | — | — | no | Image height |
| `backgroundImageSaturation` | `integer` | — | — | no | Saturation |
| `backgroundImageShow` | `boolean` | — | — | no | Add image |
| `backgroundImageTransparency` | `number` | — | — | no | Image transparency |
| `backgroundImageType` | `string` | — | — | no | Image source |
| `backgroundImageUrl` | `string` | — | — | no | Image URL |
| `backgroundShow` | `boolean` | — | — | no | Show |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `borderColor` | `object` | — | — | no |  |
| `borderStyle` | `string` | — | — | no | Line style |
| `borderTransparency` | `number` | — | — | no | Transparency |
| `borderWidth` | `number` | — | — | no | Width |
| `bottomOuterMargin` | `integer` | — | — | no | Bottom padding |
| `calloutSize` | `number` | — | — | no | Callout size |
| `cellPadding` | `integer` | — | — | no | Space between cards |
| `columnCount` | `integer` | — | — | no | Columns |
| `columnPadding` | `integer` | — | — | no | Space between columns |
| `customizeLines` | `boolean` | — | — | no | Customize lines |
| `customizePadding` | `boolean` | — | — | no | Customize spacing |
| `fixedHeight` | `integer` | — | — | no | Height |
| `fixedWidth` | `integer` | — | — | no | Width |
| `gridlineColor` | `object` | — | — | no |  |
| `gridlineStyle` | `string` | — | — | no | Line style |
| `gridlineTransparency` | `number` | — | — | no | Transparency |
| `gridlineWidth` | `number` | — | — | no | Width |
| `leftOuterMargin` | `integer` | — | — | no | Left padding |
| `lineColor` | `object` | — | — | no |  |
| `lineStyle` | `string` | — | — | no | Line style |
| `lineTransparency` | `number` | — | — | no | Transparency |
| `lineWidth` | `number` | — | — | no | Width |
| `maxTiles` | `integer` | — | — | no | Max cards shown |
| `orientation` | `integer` | — | — | no | Arrangement |
| `rectangleRoundedCurve` | `integer` | — | — | no | Rounded corners |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Individual corners |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `rightOuterMargin` | `integer` | — | — | no | Right padding |
| `rowCount` | `integer` | — | — | no | Rows |
| `rowPadding` | `integer` | — | — | no | Space between rows |
| `showFixedSize` | `boolean` | — | — | no | Fixed size |
| `style` | `string` | — | — | no | Style |
| `topOuterMargin` | `integer` | — | — | no | Top padding |

### outline

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `lineColor` | `object` | — | — | no |  |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Set transparency for line color |
| `weight` | `number` | — | — | no | Thickness of the outline in pixels |

### overFlow

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `overFlowDirection` | `integer` | — | — | no | Overflow direction |
| `overFlowStyle` | `integer` | — | — | no | Overflow style |

### rotation

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Rotation |
| `shapeAngle` | `integer` | — | — | no | Shape rotation |
| `textAngle` | `integer` | — | — | no | Text rotation |

### selection

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `restrictToLeafNodes` | `boolean` | — | — | no | Enable this option to allow selection only at the last level (leaf nodes) of the hierarchy tree. This ensures that users can only select the most specific items, preventing selection of intermediate categories |
| `selectAllCheckboxEnabled` | `boolean` | — | — | no | Show "Select all" as an option in the slicer. This selects all the values in the slicer so you can unselect them one by one to create an "is not" type filter. |
| `singleSelect` | `boolean` | — | — | no | Single select |
| `strictSingleSelect` | `boolean` | — | — | no | Force Selection ensures only one item can be chosen at a time. If no item is selected, the first available will be automatically chosen. |

### selectionIcon

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `color` | `object` | — | — | no |  |
| `overlayPosition` | `string` | — | — | no | Position |
| `overlayToggle` | `boolean` | — | — | no | Overlay |
| `position` | `string` | — | — | no | Position |
| `show` | `boolean` | — | — | no | Show |
| `showIconByState` | `boolean` | — | — | no | Show |
| `size` | `integer` | — | — | no | Size |
| `spacing` | `integer` | — | — | no | Horizontal gap |
| `transparency` | `number` | — | — | no | Transparency |
| `verticalAlignment` | `string` | — | — | no |  |

### shadowCustom

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `angle` | `number` | — | — | no | Angle |
| `color` | `object` | — | — | no | Color of custom shadow effect. |
| `position` | `string` | — | — | no | Offset |
| `shadowBlur` | `number` | — | — | no | Blur |
| `shadowDistance` | `number` | — | — | no | Distance |
| `shadowPositionPreset` | `string` | — | — | no | Preset |
| `shadowSpread` | `number` | — | — | no | Size |
| `show` | `boolean` | — | — | no | Show |
| `transparency` | `number` | — | — | no | Transparency |

### shapeCustomRectangle

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `rectangleRoundedCurve` | `integer` | — | — | no | Rounded corners |
| `rectangleRoundedCurveCustomStyle` | `boolean` | — | — | no | Customize style |
| `rectangleRoundedCurveLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `rectangleRoundedCurveLeftTop` | `integer` | — | — | no | Top left corner |
| `rectangleRoundedCurveRightBottom` | `integer` | — | — | no | Bottom right corner |
| `rectangleRoundedCurveRightTop` | `integer` | — | — | no | Top right corner |
| `tabCutCornerSnipSizeBottom` | `integer` | — | — | no | Size of bottom snips |
| `tabCutCornerSnipSizeCustomStyle` | `boolean` | — | — | no | Customize style |
| `tabCutCornerSnipSizeLeftBottom` | `integer` | — | — | no | Bottom left corner |
| `tabCutCornerSnipSizeLeftTop` | `integer` | — | — | no | Top left corner |
| `tabCutCornerSnipSizeRightBottom` | `integer` | — | — | no | Bottom right corner |
| `tabCutCornerSnipSizeRightTop` | `integer` | — | — | no | Top right corner |
| `tabCutCornerSnipSizeTop` | `integer` | — | — | no | Size of top snips |
| `tileShape` | `string` | — | — | no | Shape |

### value

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `$id` | `string` | — | — | no | Internal identifier (not user-facing). |
| `bold` | `boolean` | — | — | no | Bold |
| `customFormatString` | `string` | — | — | no | Enter a custom number format for your callout. |
| `fontColor` | `object` | — | — | no |  |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `horizontalAlignment` | `string` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `labelDisplayUnits` | `integer` | — | — | no | Select the units (millions, billions, etc.) |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |
| `showBlankAs` | `string` | — | — | no | Show blank as |
| `text` | `string` | — | — | no | Text |
| `textWrap` | `boolean` | — | — | no | Text wrap |
| `transparency` | `number` | — | — | no | Transparency |
| `underline` | `boolean` | — | — | no | Underline |
| `verticalAlignment` | `string` | — | — | no |  |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "advancedSlicerVisual",
    "query": {
      "queryState": {}
    }
  }
}
```

### R0101 — CASH FLOW & AVAILABILITY (visual 2ca9278aec2b86ea53d8)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = `[mf Cash Flow]` (measure-field — an aggregated field from a `mf` table, not a raw column). The slicer picks a named cash-flow profile.
- **Position:** `(≈226, ≈12) 1656×72` — very wide horizontal strip at the top of CASH FLOW & AVAILABILITY — effectively a tile-row spanning most of the page width.
- **No vCO title** — relies on the tile labels themselves to communicate what's being selected.
- **Objects:** `layout`, `outline`, `fillCustom`, `general`, `value`, `padding`, `selection`, `shapeCustomRectangle` — distinctive from the legacy slicer: `shapeCustomRectangle` + `fillCustom` drive the pill/tile visual style. `value.properties` includes `textWrap` — enables multi-line wrapping inside each tile for long cash-flow names.
- **vCO minimal:** `title`, `padding` only.
<!-- /MANUAL -->

### R0101 — CASH FLOW CURVE (visual a1a741b5e2466099ecd7)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = `[GraphView]` (from `s Graph View` switcher). Slicer that toggles which curve to render on the companion line chart.
- **Position:** `(130, ≈112) 140×31` — tiny compact slicer, looks more like a button group than a panel.
- **Objects:** `general`, `selection`, `layout`, `overFlow`, `value`, `fillCustom`, `outline`, `shapeCustomRectangle`, `padding` — same tile family as 2ca9278…, plus `overFlow` for narrow-width truncation behaviour.
- **Full vCO chrome** (`title`, `border`, `background`, `visualHeader`) — compact but fully styled.
- **Pattern:** for view-mode toggles (e.g. "cumulative vs periodic"), this small tile-row advanced slicer is the right control. The tiny footprint signals "this is a view switch, not a filter" to users.
<!-- /MANUAL -->

### R0101 — PORFTOLIO - INSTRUMENT BREAKDOWN (visual a2c1f5dfb64c9805868e)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = `[Periodview]` (from `s Period View 1` switcher). Picks YTD / 1Y / 3Y / 5Y for the page's time-window.
- **Position:** `(≈1, ≈56) 467×55` — medium-width tile row.
- **vCO title:** `"Select Period View"` — explicit instruction label.
- **Objects:** same advanced-slicer tile family. `value.properties` includes `horizontalAlignment`, `bold`, `fontColor` — the selected tile uses bold + custom colour to highlight the active pick.
- **Full vCO chrome** including `visualTooltip`.
- **Pattern:** period-view switchers belong at the page's top-left or top-centre — users reach for them first before the filter panel. Keep footprint small (~50 px high) so they don't crowd the primary visuals.
<!-- /MANUAL -->

### R0101 — ANALYSIS - OWNERSHIP SIMULATION (visual 3fcd5660108cc50d0990)

Screenshot: ⚠️ screenshot unavailable

**Configuration highlights:**
<!-- MANUAL: per-template-notes -->
- **Role binding:** `Values` = `[Value]` (from `s Ownership Variance`) — the **variance scenario switcher**. This slicer selects between `"Actuals"`, `"Simul 1"`, `"Simul 2"` and drives every `Δ`/`Δ%` measure on the page.
- **Position:** `(≈6, 0) 190×49` — small compact top-left scenario picker.
- **vCO title:** `"Select Simulation Scenario"` — explicit, so users know this is the scenario driver, not a generic filter.
- **Objects:** adds `data` and `image` on top of the standard advanced-slicer set — `data` configures the selectable value set; `image` attaches icons per scenario (an image tile per scenario value).
- **Full vCO chrome** including `background`, `border`, `visualHeader`.
- **⚠️ Critical — lives inside a content group.** This slicer is the one that gets silently deleted when a content group is rebuilt (see CLAUDE.md §"Audit slicers before deleting content groups"). Always restore it by ID after rebuilding; its GUID must be identical or the page's Δ measures return BLANK.
- **⚠️ Δ measures depend on this slicer's state.** See CLAUDE.md §"pbir CLI — Safety Rules" → "Delta/Simulation measures return BLANK without `s Ownership Variance` slicer context".
<!-- /MANUAL -->

## Gotchas

<!-- MANUAL: gotchas -->

### Tile rendering depends on the original visual's width and height

`advancedSlicerVisual` with `layout[0].properties.style = "'Cards'"` renders tiles sized to the **visual's own dimensions**. Shrinking the original slicer (e.g. 115 × 117 → 280 × 40 for a horizontal band) makes Power BI stop rendering tiles — you get a blank slicer where only the title shows.

When copying `visual.objects` from an existing pill slicer, also copy its original width and height, then only adjust position. R0101 reference dimensions:

- `Select Scenario` (3-option vertical pill stack) — **115 × 117**
- `Select Simulation Scenario` (2-option horizontal pill band) — **190 × 50**

### Explicit `rowCount` / `columnCount` — do not rely on `autoGrid`

For pill layouts, set `layout[0].properties.rowCount` and `columnCount` to match the number of options, and set `autoGrid = "false"`. `autoGrid = "true"` lets Power BI reshape the layout on resize, which disrupts the fixed design.

### Pill width formula

For a compact horizontal pill band:

```text
width = (option_count × 70) + 20
```

Too narrow and pills collapse; too wide and they stretch. The `+20` is the outer padding.

### Selector pattern for selected vs. default state

`visual.objects.value` needs **two** entries, one per state:

- `selector: {"id": "default"}` — unselected style
- `selector: {"id": "selection:selected"}` — selected style

Both must be set. Without the selected-state entry, clicked pills do not change appearance.
<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-advancedslicervisual](../GUIDE.md#cli-advancedslicervisual)
- JSON format: [../GUIDE.md#json-advancedslicervisual](../GUIDE.md#json-advancedslicervisual)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
- docs/design-principles/interactivity.md — filter UX patterns, sync-slicer conventions.
- docs/visual-catalog/slicer.md — legacy slicer (range/date modes still live there).
- docs/GUIDE.md#json-advancedslicervisual — hierarchy binding and mode configuration.
<!-- /MANUAL -->
