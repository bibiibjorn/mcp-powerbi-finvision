# azureMap

## Overview

<!-- MANUAL: overview -->
`azureMap` is the Azure Maps-powered successor to the legacy `map` visual. It uses an **Azure Maps subscription** rather than Bing for geocoding and tile rendering, keeping data within your Azure tenant. It supports the full set of layers available in Azure Maps: **bubble layer** (equivalent to the legacy `map` points), **filled map** (choropleth — via the `filledMap` container inside objects), **heat map layer**, **3D bar chart layer**, **path layer**, **reference layer** (GeoJSON/KML overlays), and **tile layer** (custom tile services). The formatting pane reached **General Availability in May 2026**, aligning it with the modern formatting experience used by other Power BI visuals.
<!-- /MANUAL -->

<!-- MANUAL: when-to-use -->
Use `azureMap` when:

- You need **multiple map layers simultaneously** (e.g., bubble + heat map + reference polygon) on the same canvas — the legacy `map` supports only one visual mode.
- **Data residency** requires keeping geocoding within your Azure tenant (Azure Maps does not send data to Bing).
- **Advanced styling** is needed: 3D bar layer (`barChart` container), custom tile layers (satellite imagery, terrain), traffic overlays.
- You need to display a **reference layer** (GeoJSON/KML/WKT shapefile) as an overlay on top of the data layer.
- You want **pitch and heading** control to create tilted 3D-style city views.

Prefer the legacy `map` when you only need a simple bubble/heat map and want a simpler formatting surface with fewer containers. Prefer `filledMap` when choropleth-only is sufficient and the GA formatting pane coverage of `azureMap` is not yet confirmed by your Power BI Desktop version. Prefer `shapeMap` for fully offline custom-topology choropleths.

As of May 2026, `azureMap` formatting pane is GA. For reports targeting older Desktop versions (pre-May 2026), `azureMap` formatting options were only partially exposed and required `visual.objects` editing for many container properties.
<!-- /MANUAL -->

## Roles

| Role | Required | Multiple | Accepts | Notes |
|------|----------|----------|---------|-------|
| `Location` | yes | yes | Column | Geocoded via Azure Maps. Data category should be set (City, Country/Region, etc.). Multiple columns disambiguate (City + Country). |
| `Latitude` | no | no | Column | Numeric decimal degrees. Bypasses geocoding for precise point placement. Pair with `Longitude`. |
| `Longitude` | no | no | Column | Numeric decimal degrees. Pair with `Latitude`. |
| `Legend` | no | no | Column | Splits the bubble layer into colour-coded series. |
| `Size` | no | no | Measure | Scales bubble radius. Used by `bubbleLayer` and `barChart` (bar height). |
| `Color` | no | no | Measure | Colours the bubble or filled-polygon layer by measure value via gradient. |

## Containers

- **background** (3 properties) — see [./_shared/containers-background.md](./_shared/containers-background.md)
- **border** (4 properties) — see [./_shared/containers-border.md](./_shared/containers-border.md)
- **divider** (5 properties) — documented inline below
- **dropShadow** (9 properties) — documented inline below
- **general** (1 properties) — see [./_shared/containers-general.md](./_shared/containers-general.md)
- **lockAspect** (1 properties) — documented inline below
- **padding** (4 properties) — see [./_shared/containers-padding.md](./_shared/containers-padding.md)
- **spacing** (5 properties) — documented inline below
- **stylePreset** (1 properties) — documented inline below
- **subTitle** (11 properties) — documented inline below
- **title** (12 properties) — see [./_shared/containers-title.md](./_shared/containers-title.md)
- **visualHeader** (25 properties) — see [./_shared/containers-visualHeader.md](./_shared/containers-visualHeader.md)
- **visualHeaderTooltip** (13 properties) — documented inline below
- **visualLink** (13 properties) — documented inline below
- **visualTooltip** (17 properties) — documented inline below
- **barChart** (11 properties) — documented inline below
- **bubbleLayer** (42 properties) — documented inline below
- **categoryLabels** (10 properties) — documented inline below
- **commonDataOptions** (5 properties) — documented inline below
- **dataPoint** (2 properties) — documented inline below
- **filledMap** (10 properties) — documented inline below
- **heatMapLayer** (12 properties) — documented inline below
- **labels** (4 properties) — documented inline below
- **legend** (10 properties) — see [./_shared/containers-legend.md](./_shared/containers-legend.md)
- **mapControls** (19 properties) — documented inline below
- **pathLayer** (6 properties) — documented inline below
- **referenceLayer** (18 properties) — documented inline below
- **tileLayer** (13 properties) — documented inline below
- **traffic** (3 properties) — documented inline below

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
| `customizeSpacing` | `boolean` | — | — | no | Customize spacing |
| `spaceBelowSubTitle` | `number` | — | — | no | Space below subtitle |
| `spaceBelowTitle` | `number` | — | — | no | Space below title |
| `spaceBelowTitleArea` | `number` | — | — | no | Space below title area |
| `verticalSpacing` | `number` | — | — | no | Space between label and value |

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

### barChart

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `barHeight` | `integer` | — | — | no | A relative height value |
| `barShape` | `integer` | — | — | no | The shape of the 3D bar |
| `defaultColor` | `object` | — | — | no |  |
| `layerPosition` | `string` | — | — | no | Specifies the position of the layer relative to other map layers |
| `mapTransparency` | `number` | — | — | no | Transparency of each bar |
| `maxZoom` | `integer` | — | — | no | Maximum zoom level where layer is visible |
| `minZoom` | `integer` | — | — | no | Minimum zoom level where layer is visible |
| `scaleHeightOnZoom` | `boolean` | — | — | no | Specifies if the height of the bars should scale relative to the zoom level |
| `scaleThicknessOnZoom` | `boolean` | — | — | no | Specifies if the width of the bars should scale relative to the zoom level |
| `show` | `boolean` | — | — | no | Show |
| `thickness` | `integer` | — | — | no | The width of the bars |

### bubbleLayer

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoStrokeColor` | `boolean` | — | — | no | Makes the outline color contrast with the fill color for better accessibility |
| `blur` | `number` | — | — | no | Amount of blur applied to the outline |
| `bubbleRadius` | `integer` | — | — | no | Size of each bubble |
| `bubbleStrokeWidth` | `integer` | — | — | no | Width of the outline |
| `clusteredBubbleBorderShow` | `boolean` | — | — | no | Show |
| `clusteredBubbleFillColor` | `object` | — | — | no |  |
| `clusteredBubbleFontColor` | `object` | — | — | no |  |
| `clusteredBubbleFontSize` | `number` | — | — | no |  |
| `clusteredBubbleRadius` | `integer` | — | — | no | Cluster size |
| `clusteredBubbleStrokeColor` | `object` | — | — | no |  |
| `clusteredBubbleStrokeWidth` | `integer` | — | — | no | Border width  |
| `clusteringEnabled` | `boolean` | — | — | no | Cluster nearby data points into one bubble to display their aggregate. |
| `fillColor` | `object` | — | — | no |  |
| `iconId` | `string` | — | — | no | Icon |
| `imageData` | `object` | — | — | no |  |
| `imageSourceType` | `string` | — | — | no | Specifies the source of marker images |
| `imageUrl` | `string` | — | — | no | Accepted file formats for marker images sourced from URLs include PNG, JPEG, GIF, and SVG. |
| `layerPosition` | `string` | — | — | no | Specifies the position of the layer relative to other map layers |
| `logScale` | `integer` | — | — | no | Range of input data logarithmically mapped to the minimum and maximum size |
| `mapTransparency` | `number` | — | — | no | Transparency of each bubble |
| `markerRangeType` | `string` | — | — | no | Range scaling |
| `markerRotation` | `number` | — | — | no | Specifies the rotation angle of markers. |
| `markerType` | `string` | — | — | no | The source of the marker |
| `matchFillColor` | `boolean` | — | — | no | Makes the outline color align with fill color |
| `maxRadius` | `integer` | — | — | no | Maximum bubble size when scaling the data |
| `maxScale` | `integer` | — | — | no | Maximum scale of the public URL marker relative to its original size |
| `maxZoom` | `integer` | — | — | no | Maximum zoom level where layer is visible |
| `minBubbleRadius` | `integer` | — | — | no | Min size of each bubble |
| `minScale` | `integer` | — | — | no | Minimum scale public URL marker relative to its original size |
| `minZoom` | `integer` | — | — | no | Minimum zoom level where layer is visible |
| `pitchAlignment` | `string` | — | — | no | How the bubble looks when the map is pitched |
| `radiusScalingMethod` | `integer` | — | — | no | Scaling algorithm used to determine bubble size |
| `rotationAlignment` | `string` | — | — | no | How the bubble looks when the map is rotated |
| `scale` | `integer` | — | — | no | Scale of the public URL marker relative to its original size |
| `show` | `boolean` | — | — | no | Show |
| `strokeColor` | `object` | — | — | no |  |
| `strokeTransparency` | `number` | — | — | no | Transparency of the outline |
| `x1` | `number` | — | — | no | X1 parameter of a cubic bezier curve |
| `x2` | `number` | — | — | no | X2 parameter of a cubic bezier curve |
| `y1` | `number` | — | — | no | Y1 parameter of a cubic bezier curve |
| `y2` | `number` | — | — | no | Y2 parameter of a cubic bezier curve |
| `zoomScaleFactor` | `integer` | — | — | no | Amount the bubbles should scale relative to the zoom level. A zoom scale of one means no scaling. Large values will make bubbles smaller when zoomed out and larger when zoomed in |

### categoryLabels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `backgroundColor` | `object` | — | — | no |  |
| `backgroundTransparency` | `number` | — | — | no | Background color transparency |
| `bold` | `boolean` | — | — | no | Bold |
| `color` | `object` | — | — | no |  |
| `enableBackground` | `boolean` | — | — | no | Show background |
| `fontFamily` | `string` | — | — | no | Font family |
| `fontSize` | `number` | — | — | no |  |
| `italic` | `boolean` | — | — | no | Italic |
| `show` | `boolean` | — | — | no | Show |
| `underline` | `boolean` | — | — | no | Underline |

### commonDataOptions

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `maxDataValue` | `number` | — | — | no | The maximum value of the input data to scale against |
| `minDataValue` | `number` | — | — | no | The minimum value of the input data to scale against |
| `showNegatives` | `boolean` | — | — | no | Specifies if absolute value of negative size values should be plotted |
| `showZeros` | `boolean` | — | — | no | Specifies if points that have a size value of zero should be shown on the map using the minimum radius |
| `unselectedTransparency` | `number` | — | — | no | Transparency of unselected data points on the map |

### dataPoint

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `fill` | `object` | — | — | no | Fill color for data points. Supports conditional formatting via a measure returning theme color tokens ('good', 'bad', 'neutral') for per-bar/per-slice coloring. |
| `showAllDataPoints` | `boolean` | — | — | no | Show all |

### filledMap

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `borderShow` | `boolean` | — | — | no | Show |
| `defaultColor` | `object` | — | — | no |  |
| `layerPosition` | `string` | — | — | no | Specifies the position of the layer relative to other map layers |
| `mapTransparency` | `number` | — | — | no | Fill transparency |
| `maxZoom` | `integer` | — | — | no | Maximum zoom level where layer is visible |
| `minZoom` | `integer` | — | — | no | Minimum zoom level where layer is visible |
| `show` | `boolean` | — | — | no | Show |
| `strokeColor` | `object` | — | — | no |  |
| `strokeTransparency` | `number` | — | — | no | Outline transparency |
| `strokeWidth` | `integer` | — | — | no | Outline width |

### heatMapLayer

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `heatMapColorCenter` | `object` | — | — | no |  |
| `heatMapColorHigh` | `object` | — | — | no |  |
| `heatMapColorLow` | `object` | — | — | no |  |
| `heatMapIntensity` | `number` | — | — | no | Set the baseline intensity level for all data points. |
| `heatMapRadius` | `number` | — | — | no | The radius of each data point in the heat map |
| `heatMapRadiusUnit` | `string` | — | — | no | Use meters or pixels as units to visualize the data |
| `heatMapUseSize` | `boolean` | — | — | no | Use data from the 'Size' field well to determine heat intensity. |
| `layerPosition` | `string` | — | — | no | Specifies the position of the layer relative to other map layers |
| `mapTransparency` | `number` | — | — | no | Sets the transparency of the heat map layer |
| `maxZoom` | `integer` | — | — | no | Maximum zoom level where layer is visible |
| `minZoom` | `integer` | — | — | no | Minimum zoom level where layer is visible |
| `show` | `boolean` | — | — | no | Show |

### labels

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no | Font color for data labels. |
| `labelDisplayUnits` | `integer` | — | — | no | Display units for data label values (Auto, Thousands, Millions, Billions). |
| `labelPrecision` | `integer` | — | — | no | Select the number of decimal places to display for the values |
| `show` | `boolean` | — | — | no | Show |

### legend

See [./_shared/containers-legend.md](./_shared/containers-legend.md).

### mapControls

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `autoZoom` | `boolean` | — | — | no | Allow the map to automatically zoom over the data as the visual updates |
| `autoZoomIncludesReferenceLayer` | `boolean` | — | — | no | Include reference layer data when the map automatically zooms. |
| `centerLatitude` | `number` | — | — | no | Default latitude at the center of the map |
| `centerLongitude` | `number` | — | — | no | Default longitude at the center of the map |
| `defaultStyle` | `string` | — | — | no | Default style in which the map is displayed |
| `geocodingCulture` | `string` | — | — | no | Geocoding culture |
| `heading` | `number` | — | — | no | Default orientation of the map in degrees, where 0 is north, 90 is east, 180 is south, and 270 is west |
| `pitch` | `number` | — | — | no | Default tilt of the map in degrees between 0 and 60, where 0 is looking straight down at the map |
| `showAdminDistrict2Borders` | `boolean` | — | — | no | Display the borders for the second-level administrative divisions, such as county borders, on the map. |
| `showAdminDistrictBorders` | `boolean` | — | — | no | Display the borders for the first-level administrative divisions, such as state or province borders, on the map. |
| `showBuildingFootprints` | `boolean` | — | — | no | Display building footprints on the map. |
| `showCountryRegionBorders` | `boolean` | — | — | no | Display country/region borders on the map. |
| `showLabels` | `boolean` | — | — | no | Display labels for map elements such as streets and region names. |
| `showNavigationControls` | `boolean` | — | — | no | Allow report readers to zoom, rotate and change the pitch of the map |
| `showRoadDetails` | `boolean` | — | — | no | Adjust the level of detail for visible roads. Turning this off will reduce the number of roads shown on the map. |
| `showSelectionControl` | `boolean` | — | — | no | This control lets you draw polygon areas on the map and retrieve all the data points that are within that area. |
| `showStylePicker` | `boolean` | — | — | no | Allow report readers to change the map style |
| `worldWrap` | `boolean` | — | — | no | Allow the user to pan the map horizontally infinitively |
| `zoom` | `number` | — | — | no | Controls the default zoom level of the map. Lower values show a wider area; higher values zoom in closer. |

### pathLayer

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `color` | `object` | — | — | no |  |
| `maxZoom` | `integer` | — | — | no | Maximum zoom level where layer is visible |
| `minZoom` | `integer` | — | — | no | Minimum zoom level where layer is visible |
| `show` | `boolean` | — | — | no | Show |
| `strokeTransparency` | `number` | — | — | no | Transparency |
| `strokeWidth` | `integer` | — | — | no | Width |

### referenceLayer

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `bubbleFillColor` | `object` | — | — | no |  |
| `bubbleStrokeColor` | `object` | — | — | no |  |
| `bubbleStrokeTransparency` | `number` | — | — | no | Border transparency |
| `bubbleStrokeWidth` | `integer` | — | — | no | Border width  |
| `datasourceType` | `string` | — | — | no | Specifies the type of the reference layer data source |
| `lineStrokeColor` | `object` | — | — | no |  |
| `lineStrokeTransparency` | `number` | — | — | no | Transparency |
| `lineStrokeWidth` | `integer` | — | — | no | Width |
| `polygonFillColor` | `object` | — | — | no |  |
| `polygonStrokeColor` | `object` | — | — | no |  |
| `polygonStrokeTransparency` | `number` | — | — | no | Border transparency |
| `polygonStrokeWidth` | `integer` | — | — | no | Border width  |
| `referenceLayerUrl` | `string` | — | — | no | Accepted file formats include GeoJSON, KML, WKT, and shapefiles that are uploaded within a zip file. |
| `show` | `boolean` | — | — | no | Show |
| `unmappedObjectFillColor` | `object` | — | — | no |  |
| `unmappedObjectStrokeColor` | `object` | — | — | no |  |
| `unmappedObjectUseCustomColors` | `boolean` | — | — | no | Use custom colors |
| `unmappedObjectVisibility` | `boolean` | — | — | no | Show |

### tileLayer

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `eastBounds` | `number` | — | — | no | Eastern longitude of the bounding box where this tile is available |
| `isTMS` | `boolean` | — | — | no | Use Tile Map Service (TMS) protocol, which reverses Y-axis numbering. Turn this on if your tiles appear upside-down. |
| `layerPosition` | `string` | — | — | no | Specifies the position of the layer relative to other map layers |
| `mapTransparency` | `number` | — | — | no | Transparency of tile layer |
| `maxZoom` | `integer` | — | — | no | Maximum zoom level where layer is visible |
| `minZoom` | `integer` | — | — | no | Minimum zoom level where layer is visible |
| `northBounds` | `number` | — | — | no | Northern latitude of the bounding box where this tile is available |
| `show` | `boolean` | — | — | no | Show |
| `southBounds` | `number` | — | — | no | Southern latitude of the bounding box where this tile is available |
| `tileLayerUrl` | `string` | — | — | no | Formatted URL to the tile layer service or TileJSON resource |
| `tileSize` | `number` | — | — | no | An integer value that specifies both the width and height dimensions of the map tiles |
| `tileSizeDropdown` | `string` | — | — | no | An integer value that specifies both the width and height dimensions of the map tiles |
| `westBounds` | `number` | — | — | no | Western longitude of the bounding box where this tile is available |

### traffic

| Property | Type | Default | Range / Allowed values | Selector-aware? | Notes |
|----------|------|---------|------------------------|-----------------|-------|
| `show` | `boolean` | — | — | no | Show |
| `showIncidents` | `boolean` | — | — | no | Show traffic incidents on the map |
| `showTrafficControl` | `boolean` | — | — | no | Allow report readers to turn the traffic layer on or off |

## Conditional formatting

See [./_shared/conditional-formatting.md](./_shared/conditional-formatting.md).

## Templates

### Minimal

```json
{
  "visual": {
    "visualType": "azureMap",
    "query": {
      "queryState": {}
    }
  }
}
```

## Gotchas

<!-- MANUAL: gotchas -->

### Formatting pane was in preview until May 2026 — many properties required direct visual.objects editing

Before the May 2026 GA release, `azureMap`'s formatting pane did not expose all containers. Properties in `bubbleLayer`, `heatMapLayer`, `barChart`, `referenceLayer`, `tileLayer`, `pathLayer`, and `traffic` could only be set via `visual.objects` JSON. In reports targeting pre-May 2026 Desktop versions, always read existing `visual.objects` from a working same-type visual before hand-editing; do not assume the formatting pane shows the full property set.

### `azureMap` has a `filledMap` container inside `visual.objects` — distinct from the `filledMap` visual type

The `filledMap` container in `azureMap.visual.objects` controls the choropleth fill layer *within* the Azure Maps visual. It is entirely separate from the standalone `filledMap` visual type. Setting `filledMap.show = true` enables the choropleth overlay on the `azureMap` canvas. Both the bubble layer and the filled layer can be active simultaneously.

### Geocoding uses Azure Maps, not Bing — data stays within your Azure tenant

Unlike `map` and `filledMap` (which use Bing), `azureMap` routes geocoding through the Azure Maps service tied to your tenant. Location values are still transmitted to an external service (Azure), but remain within your Azure subscription boundary. Confirm your organisation's Azure Maps key is configured in the Power BI tenant settings before deploying `azureMap` reports.

### Clustering in `bubbleLayer` aggregates points at low zoom — measure values may not match

When `bubbleLayer.clusteringEnabled = true`, nearby points are merged into a single cluster bubble showing the aggregate count. The `Size` and `Color` measures reflect the cluster aggregate, not individual point values. This can mislead users comparing bubbles at different zoom levels. Disable clustering if exact measure-per-location fidelity matters.

### Reference layer URL must be reachable from the Power BI service

`referenceLayer.referenceLayerUrl` (GeoJSON/KML/WKT/shapefile zip) must be publicly accessible from the Power BI Service rendering engine. URLs behind VPNs or authenticated endpoints will fail silently — the layer renders blank without an error message.

### `barChart` (3D bar) layer requires a `Size` measure — omitting it hides all bars

The 3D bar layer (`barChart.show = true`) uses `Size` for bar height. Without a `Size` binding, all bars render at zero height and are invisible. The layer appears enabled in the formatting pane but produces an empty canvas.

<!-- /MANUAL -->

## Related

- MCP op: `11_PBIP_Authoring:pbir_add_visuals` — canonical authoring path
- pbir CLI: [../GUIDE.md#cli-azuremap](../GUIDE.md#cli-azuremap)
- JSON format: [../GUIDE.md#json-azuremap](../GUIDE.md#json-azuremap)
- skill: reports:pbir-cli, skill: pbir-page-builder

<!-- MANUAL: related-extras -->
TODO: fill in during enrichment.
<!-- /MANUAL -->
