"""
PBIP Theme Compliance Analyzer - Analyzes visual consistency against theme.

This module provides comprehensive analysis of Power BI theme compliance including:
- Color palette validation
- Font consistency checking
- Visual formatting standards
- Theme coverage analysis
- Compliance scoring
"""

import json
import logging
import os
import re
from typing import Dict, List, Optional, Any, Set, Tuple
from datetime import datetime
from core.utilities.json_utils import load_json

logger = logging.getLogger(__name__)


# Standard Power BI theme color names
STANDARD_THEME_COLORS = [
    "foreground",
    "foregroundNeutralSecondary",
    "foregroundNeutralTertiary",
    "background",
    "backgroundLight",
    "backgroundNeutral",
    "tableAccent",
    "hyperlink",
    "visitedHyperlink",
    "negative",
    "neutral",
    "positive",
]

# Data colors (series colors)
DATA_COLOR_PATTERN = re.compile(r"dataColors?\[(\d+)\]|color(\d+)", re.IGNORECASE)


class PbipThemeComplianceAnalyzer:
    """Analyzes visual consistency against Power BI theme."""

    def __init__(self):
        """Initialize the theme compliance analyzer."""
        self.logger = logger

    def analyze_theme_compliance(
        self, report_folder: str, theme_path: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Analyze theme compliance across all visuals in a PBIP report.

        Args:
            report_folder: Path to the .Report folder
            theme_path: Optional path to a custom theme JSON file

        Returns:
            Dictionary with comprehensive theme compliance analysis
        """
        if not os.path.exists(report_folder):
            raise FileNotFoundError(f"Report folder not found: {report_folder}")

        definition_path = os.path.join(report_folder, "definition")
        if not os.path.isdir(definition_path):
            raise ValueError(f"No definition folder found in {report_folder}")

        self.logger.info(f"Analyzing theme compliance in: {report_folder}")

        result = {
            "report_folder": report_folder,
            "analysis_timestamp": datetime.now().isoformat(),
            "theme": None,
            "theme_source": None,
            "pages": [],
            "color_analysis": {},
            "font_analysis": {},
            "visual_analysis": {},
            "compliance_score": 0,
            "violations": [],
            "summary": {},
        }

        # Load theme
        theme_data = self._load_theme(report_folder, definition_path, theme_path)
        result["theme"] = theme_data.get("theme")
        result["theme_source"] = theme_data.get("source")

        # Parse all pages and visuals
        pages_path = os.path.join(definition_path, "pages")
        if os.path.isdir(pages_path):
            result["pages"] = self._analyze_all_pages(pages_path, theme_data.get("theme"))

        # Aggregate analysis
        result["color_analysis"] = self._aggregate_color_analysis(result["pages"])
        result["font_analysis"] = self._aggregate_font_analysis(result["pages"])
        result["visual_analysis"] = self._aggregate_visual_analysis(result["pages"])

        # Identify violations
        result["violations"] = self._identify_violations(result, theme_data.get("theme"))

        # Calculate compliance score
        result["compliance_score"] = self._calculate_compliance_score(result)

        # Generate summary
        result["summary"] = self._generate_summary(result)

        return result

    def _load_theme(
        self, report_folder: str, definition_path: str, custom_theme_path: Optional[str]
    ) -> Dict[str, Any]:
        """Load theme from report or custom path."""
        theme_info = {"theme": None, "source": None, "extracted_colors": [], "extracted_fonts": []}

        # Priority 1: Custom theme path
        if custom_theme_path and os.path.exists(custom_theme_path):
            try:
                theme_data = load_json(custom_theme_path)
                theme_info["theme"] = self._parse_theme(theme_data)
                theme_info["source"] = f"custom: {custom_theme_path}"
                self.logger.info(f"Loaded custom theme: {custom_theme_path}")
                return theme_info
            except (OSError, json.JSONDecodeError, KeyError) as e:
                self.logger.warning(f"Failed to load custom theme: {e}")

        # Priority 2: Report.json theme reference (PBIP format)
        report_json_path = os.path.join(definition_path, "report.json")
        if os.path.exists(report_json_path):
            try:
                report_data = load_json(report_json_path)

                # Check for themeCollection (modern PBIP format)
                theme_collection = report_data.get("themeCollection", {})
                custom_theme = theme_collection.get("customTheme", {})

                if custom_theme:
                    theme_name = custom_theme.get("name", "")
                    theme_type = custom_theme.get("type", "")

                    # Look in StaticResources based on type
                    if theme_type == "RegisteredResources":
                        theme_file = os.path.join(
                            report_folder, "StaticResources", "RegisteredResources", theme_name
                        )
                    else:
                        theme_file = os.path.join(
                            report_folder, "StaticResources", theme_type, theme_name
                        )

                    if os.path.exists(theme_file):
                        theme_data = load_json(theme_file)
                        theme_info["theme"] = self._parse_theme(theme_data)
                        # Use the theme's internal name if available
                        display_name = theme_data.get("name", theme_name)
                        theme_info["source"] = f"file: {display_name}"
                        self.logger.info(f"Loaded theme from StaticResources: {theme_file}")
                        return theme_info
                    else:
                        self.logger.warning(f"Theme file not found: {theme_file}")

                # Fallback: Check for legacy config.themeConfig
                theme_config = report_data.get("config", {}).get("themeConfig", {})

                # Check for embedded theme
                if "theme" in theme_config:
                    theme_info["theme"] = self._parse_theme(theme_config["theme"])
                    theme_info["source"] = "embedded in report.json"
                    return theme_info

                # Check for theme name reference
                theme_name = theme_config.get("name") or report_data.get("theme")
                if theme_name:
                    theme_info["source"] = f"referenced: {theme_name}"
                    # Try to find theme file
                    theme_file = self._find_theme_file(report_folder, theme_name)
                    if theme_file:
                        theme_data = load_json(theme_file)
                        theme_info["theme"] = self._parse_theme(theme_data)

            except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
                self.logger.warning(f"Error loading theme from report.json: {e}")

        # Priority 3: Look for theme.json in report folder
        theme_locations = [
            os.path.join(definition_path, "theme.json"),
            os.path.join(report_folder, "theme.json"),
            os.path.join(report_folder, "..", "theme.json"),
        ]

        for loc in theme_locations:
            if os.path.exists(loc):
                try:
                    theme_data = load_json(loc)
                    theme_info["theme"] = self._parse_theme(theme_data)
                    theme_info["source"] = f"file: {loc}"
                    return theme_info
                except (OSError, json.JSONDecodeError, KeyError) as e:
                    self.logger.warning(f"Failed to load theme from {loc}: {e}")

        # No theme found - use defaults
        if not theme_info["theme"]:
            theme_info["theme"] = self._get_default_theme()
            theme_info["source"] = "default (no theme file found)"

        return theme_info

    def _find_theme_file(self, report_folder: str, theme_name: str) -> Optional[str]:
        """Try to find a theme file by name."""
        # Common locations
        possible_paths = [
            os.path.join(report_folder, f"{theme_name}.json"),
            os.path.join(report_folder, "definition", f"{theme_name}.json"),
            os.path.join(report_folder, "themes", f"{theme_name}.json"),
            os.path.join(report_folder, "..", "themes", f"{theme_name}.json"),
        ]

        for path in possible_paths:
            if os.path.exists(path):
                return path

        return None

    def _parse_theme(self, theme_data: Dict) -> Dict[str, Any]:
        """Parse theme JSON into standardized format."""
        parsed = {
            "name": theme_data.get("name", "Unknown"),
            "colors": {
                "data_colors": [],
                "semantic_colors": {},
                "text_colors": {},
                "background_colors": {},
            },
            "fonts": {"title": None, "label": None, "header": None},
            "visual_styles": {},
        }

        # Extract data colors
        data_colors = theme_data.get("dataColors", [])
        if isinstance(data_colors, list):
            parsed["colors"]["data_colors"] = [self._normalize_color(c) for c in data_colors]

        # Extract semantic colors
        for color_name in STANDARD_THEME_COLORS:
            if color_name in theme_data:
                parsed["colors"]["semantic_colors"][color_name] = self._normalize_color(
                    theme_data[color_name]
                )

        # Extract text colors
        text_classes = theme_data.get("textClasses", {})
        for class_name, class_def in text_classes.items():
            if "color" in class_def:
                parsed["colors"]["text_colors"][class_name] = self._normalize_color(
                    class_def["color"]
                )
            if "fontFace" in class_def:
                parsed["fonts"][class_name] = class_def["fontFace"]

        # Extract visual styles
        visual_styles = theme_data.get("visualStyles", {})
        parsed["visual_styles"] = visual_styles

        return parsed

    def _get_default_theme(self) -> Dict[str, Any]:
        """Get default Power BI theme colors."""
        return {
            "name": "Default",
            "colors": {
                "data_colors": [
                    "#118DFF",
                    "#12239E",
                    "#E66C37",
                    "#6B007B",
                    "#E044A7",
                    "#744EC2",
                    "#D9B300",
                    "#D64550",
                ],
                "semantic_colors": {
                    "foreground": "#252423",
                    "background": "#FFFFFF",
                    "tableAccent": "#118DFF",
                    "hyperlink": "#0066CC",
                    "negative": "#E81123",
                    "positive": "#107C10",
                    "neutral": "#8A8886",
                },
                "text_colors": {},
                "background_colors": {},
            },
            "fonts": {"title": "Segoe UI", "label": "Segoe UI", "header": "Segoe UI Semibold"},
            "visual_styles": {},
        }

    def _normalize_color(self, color: Any) -> Optional[str]:
        """Normalize color to hex format."""
        if not color:
            return None

        if isinstance(color, str):
            color = color.strip()
            # Already hex
            if color.startswith("#"):
                return color.upper()
            # Named color or rgb
            return color.upper()

        if isinstance(color, dict):
            # ARGB format
            if "value" in color:
                return self._normalize_color(color["value"])

        return None

    def _analyze_all_pages(self, pages_path: str, theme: Optional[Dict]) -> List[Dict[str, Any]]:
        """Analyze all pages for theme compliance."""
        pages = []

        try:
            for page_id in os.listdir(pages_path):
                page_folder = os.path.join(pages_path, page_id)
                if not os.path.isdir(page_folder):
                    continue

                page_json_path = os.path.join(page_folder, "page.json")
                if not os.path.exists(page_json_path):
                    continue

                page_data = load_json(page_json_path)
                page_analysis = {
                    "id": page_data.get("name", page_id),
                    "display_name": page_data.get("displayName", ""),
                    "visuals": [],
                    "color_usage": {},
                    "font_usage": {},
                    "violations": [],
                }

                # Analyze visuals
                visuals_path = os.path.join(page_folder, "visuals")
                if os.path.isdir(visuals_path):
                    page_analysis["visuals"] = self._analyze_page_visuals(visuals_path, theme)

                # Aggregate page-level color/font usage
                for visual in page_analysis["visuals"]:
                    for color, count in visual.get("colors_used", {}).items():
                        page_analysis["color_usage"][color] = (
                            page_analysis["color_usage"].get(color, 0) + count
                        )
                    for font, count in visual.get("fonts_used", {}).items():
                        page_analysis["font_usage"][font] = (
                            page_analysis["font_usage"].get(font, 0) + count
                        )
                    page_analysis["violations"].extend(visual.get("violations", []))

                pages.append(page_analysis)

        except (OSError, json.JSONDecodeError, KeyError, TypeError) as e:
            self.logger.error(f"Error analyzing pages: {e}")

        return pages

    def _analyze_page_visuals(
        self, visuals_path: str, theme: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """Analyze all visuals on a page."""
        visuals = []

        try:
            for visual_id in os.listdir(visuals_path):
                visual_folder = os.path.join(visuals_path, visual_id)
                if not os.path.isdir(visual_folder):
                    continue

                visual_json_path = os.path.join(visual_folder, "visual.json")
                if not os.path.exists(visual_json_path):
                    continue

                visual_data = load_json(visual_json_path)
                visual_analysis = self._analyze_single_visual(visual_data, visual_id, theme)
                visuals.append(visual_analysis)

        except (OSError, json.JSONDecodeError, KeyError) as e:
            self.logger.error(f"Error analyzing visuals: {e}")

        return visuals

    def _analyze_single_visual(
        self, visual_data: Dict, visual_id: str, theme: Optional[Dict]
    ) -> Dict[str, Any]:
        """Analyze a single visual for theme compliance."""
        analysis = {
            "id": visual_id,
            "visual_type": "",
            "title": None,
            "colors_used": {},
            "fonts_used": {},
            "violations": [],
            "is_compliant": True,
        }

        try:
            visual = visual_data.get("visual", {})
            analysis["visual_type"] = visual.get("visualType", "unknown")

            # Extract title
            title_config = self._extract_title_config(visual_data)
            if title_config:
                analysis["title"] = title_config.get("text")

            # Extract all colors used
            colors = self._extract_colors_from_visual(visual_data)
            for color in colors:
                normalized = self._normalize_color(color)
                if normalized:
                    analysis["colors_used"][normalized] = (
                        analysis["colors_used"].get(normalized, 0) + 1
                    )

            # Extract all fonts used
            fonts = self._extract_fonts_from_visual(visual_data)
            for font in fonts:
                analysis["fonts_used"][font] = analysis["fonts_used"].get(font, 0) + 1

            # Check compliance
            if theme:
                violations = self._check_visual_compliance(analysis, theme, analysis["visual_type"])
                analysis["violations"] = violations
                analysis["is_compliant"] = len(violations) == 0

        except (KeyError, TypeError, AttributeError) as e:
            self.logger.warning(f"Error analyzing visual {visual_id}: {e}")

        return analysis

    def _extract_title_config(self, visual_data: Dict) -> Optional[Dict]:
        """Extract title configuration from visual."""
        try:
            visual = visual_data.get("visual", {})
            vc_objects = visual.get("visualContainerObjects", {})
            title_list = vc_objects.get("title", [])

            if isinstance(title_list, list) and len(title_list) > 0:
                title_props = title_list[0].get("properties", {})
                text_expr = title_props.get("text", {}).get("expr", {})

                if "Literal" in text_expr:
                    value = text_expr["Literal"].get("Value", "")
                    return {"text": value.strip("'\"")}

        except (KeyError, TypeError, AttributeError):
            pass

        return None

    def _extract_colors_from_visual(self, visual_data: Dict) -> List[str]:
        """Recursively extract all color values from visual JSON."""
        colors = []

        def search(obj, path=""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    key_lower = key.lower()
                    if any(c in key_lower for c in ["color", "fill", "stroke", "background"]):
                        if isinstance(value, str) and (
                            value.startswith("#")
                            or value.startswith("rgb")
                            or self._is_color_name(value)
                        ):
                            colors.append(value)
                        elif isinstance(value, dict):
                            # Check for solid.color pattern
                            solid = value.get("solid", {})
                            if "color" in solid:
                                colors.append(solid["color"])
                            # Check for expr.Literal.Value pattern
                            expr = value.get("expr", {})
                            if "Literal" in expr:
                                literal_val = expr["Literal"].get("Value", "")
                                if literal_val.startswith("'#") or literal_val.startswith("'rgb"):
                                    colors.append(literal_val.strip("'"))
                    search(value, f"{path}.{key}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    search(item, f"{path}[{i}]")

        search(visual_data)
        return colors

    def _extract_fonts_from_visual(self, visual_data: Dict) -> List[str]:
        """Recursively extract all font family names from visual JSON."""
        fonts = []

        def search(obj, path=""):
            if isinstance(obj, dict):
                for key, value in obj.items():
                    key_lower = key.lower()
                    # Only match fontFamily/fontFace, NOT fontSize or other font properties
                    if key_lower in ["fontfamily", "fontface"]:
                        if isinstance(value, str) and len(value) > 1:
                            # Filter out font size/weight values
                            if not self._is_font_size_or_weight(value):
                                fonts.append(value)
                        elif isinstance(value, dict):
                            expr = value.get("expr", {})
                            if "Literal" in expr:
                                font_val = expr["Literal"].get("Value", "").strip("'\"")
                                if font_val and not self._is_font_size_or_weight(font_val):
                                    fonts.append(font_val)
                    search(value, f"{path}.{key}")
            elif isinstance(obj, list):
                for i, item in enumerate(obj):
                    search(item, f"{path}[{i}]")

        search(visual_data)
        return [f for f in fonts if f]

    def _is_font_size_or_weight(self, value: str) -> bool:
        """Check if a value looks like a font size or weight rather than a font name."""
        if not value:
            return False
        value = value.strip().lower()
        # Font weight values to filter out
        font_weights = [
            "bold",
            "normal",
            "light",
            "medium",
            "semibold",
            "regular",
            "thin",
            "heavy",
            "black",
            "100",
            "200",
            "300",
            "400",
            "500",
            "600",
            "700",
            "800",
            "900",
        ]
        if value in font_weights:
            return True
        # Match patterns like: "12", "12pt", "12px", "12D", "10.5D", "24pt", etc.
        font_size_pattern = re.compile(r"^[\d.]+\s*(pt|px|em|rem|D)?$", re.IGNORECASE)
        return bool(font_size_pattern.match(value))

    def _is_color_name(self, value: str) -> bool:
        """Check if value is a CSS color name."""
        css_colors = [
            "black",
            "white",
            "red",
            "green",
            "blue",
            "yellow",
            "orange",
            "purple",
            "pink",
            "gray",
            "grey",
            "brown",
            "cyan",
            "magenta",
            "transparent",
            "inherit",
        ]
        return value.lower() in css_colors

    def _hex_to_rgb(self, hex_color: str) -> Optional[Tuple[int, int, int]]:
        """Convert hex color to RGB tuple."""
        if not hex_color or not isinstance(hex_color, str):
            return None
        hex_color = hex_color.strip().lstrip("#")
        if len(hex_color) == 3:
            hex_color = "".join(c * 2 for c in hex_color)
        if len(hex_color) != 6:
            return None
        try:
            return (int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16))
        except ValueError:
            return None

    def _rgb_to_hex(self, r: int, g: int, b: int) -> str:
        """Convert RGB to hex color."""
        r = max(0, min(255, r))
        g = max(0, min(255, g))
        b = max(0, min(255, b))
        return f"#{r:02X}{g:02X}{b:02X}"

    def _generate_color_variations(self, base_colors: List[str]) -> Set[str]:
        """
        Generate color variations (tints and shades) from base colors.
        Power BI creates lighter and darker versions of each theme color.

        Power BI's exact percentages (from Deneb documentation):
        - Tints: 60% lighter, 40% lighter, 20% lighter
        - Shades: 25% darker, 50% darker

        Additionally, we generate intermediate steps for better matching.
        """
        variations = set()

        for color in base_colors:
            if not color:
                continue
            rgb = self._hex_to_rgb(color)
            if not rgb:
                continue

            r, g, b = rgb
            variations.add(color.upper())

            # Power BI's exact tint percentages: 60%, 40%, 20% lighter
            # Plus intermediate values for better coverage
            tint_factors = [0.20, 0.30, 0.40, 0.50, 0.60, 0.70, 0.80]
            for factor in tint_factors:
                # Tints (lighter - mix with white)
                tint_r = int(r + (255 - r) * factor)
                tint_g = int(g + (255 - g) * factor)
                tint_b = int(b + (255 - b) * factor)
                variations.add(self._rgb_to_hex(tint_r, tint_g, tint_b))

            # Power BI's exact shade percentages: 25%, 50% darker
            # Plus intermediate values for better coverage
            shade_factors = [0.25, 0.35, 0.50, 0.65, 0.75]
            for factor in shade_factors:
                # Shades (darker - multiply by (1-factor))
                shade_r = int(r * (1 - factor))
                shade_g = int(g * (1 - factor))
                shade_b = int(b * (1 - factor))
                variations.add(self._rgb_to_hex(shade_r, shade_g, shade_b))

        return variations

    def _is_color_similar(self, color1: str, color2: str, threshold: int = 30) -> bool:
        """Check if two colors are similar within a threshold (Euclidean distance in RGB)."""
        rgb1 = self._hex_to_rgb(color1)
        rgb2 = self._hex_to_rgb(color2)
        if not rgb1 or not rgb2:
            return False

        # Euclidean distance in RGB space
        distance = (
            (rgb1[0] - rgb2[0]) ** 2 + (rgb1[1] - rgb2[1]) ** 2 + (rgb1[2] - rgb2[2]) ** 2
        ) ** 0.5
        return distance <= threshold

    def _get_common_neutral_colors(self) -> Set[str]:
        """Get common neutral colors used by Power BI (grays, near-whites, near-blacks)."""
        neutrals = set()

        # Common gray scale values Power BI uses
        gray_values = [
            0x00,
            0x11,
            0x22,
            0x33,
            0x44,
            0x55,
            0x66,
            0x77,
            0x88,
            0x99,
            0xAA,
            0xBB,
            0xCC,
            0xDD,
            0xEE,
            0xFF,
        ]

        # Pure grays (R=G=B)
        for v in gray_values:
            neutrals.add(f"#{v:02X}{v:02X}{v:02X}")

        # Additional common Power BI grays
        common_grays = [
            "#F3F3F3",
            "#F2F2F2",
            "#F0F0F0",
            "#EFEFEF",
            "#E6E6E6",
            "#E5E5E5",
            "#D9D9D9",
            "#D8D8D8",
            "#D0D0D0",
            "#CCCCCC",
            "#C8C8C8",
            "#C0C0C0",
            "#B3B3B3",
            "#A6A6A6",
            "#A0A0A0",
            "#999999",
            "#969696",
            "#909090",
            "#808080",
            "#7F7F7F",
            "#777777",
            "#737373",
            "#707070",
            "#696969",
            "#666666",
            "#606060",
            "#5A5A5A",
            "#595959",
            "#555555",
            "#505050",
            "#4D4D4D",
            "#404040",
            "#3D3D3D",
            "#333333",
            "#2D2D2D",
            "#262626",
            "#252423",
            "#1A1A1A",
            "#191919",
            "#0D0D0D",
        ]
        neutrals.update(common_grays)

        return neutrals

    def _check_visual_compliance(
        self, visual_analysis: Dict, theme: Dict, visual_type: str
    ) -> List[Dict[str, Any]]:
        """Check if visual complies with theme."""
        violations = []

        # Collect base theme colors
        base_theme_colors = []

        # Add data colors
        for c in theme.get("colors", {}).get("data_colors", []):
            if c:
                base_theme_colors.append(c)
        # Add semantic colors
        for c in theme.get("colors", {}).get("semantic_colors", {}).values():
            if c:
                base_theme_colors.append(c)
        # Add text colors
        for c in theme.get("colors", {}).get("text_colors", {}).values():
            if c:
                base_theme_colors.append(c)

        # Build complete set of valid theme colors
        theme_colors = set()

        # Add exact theme colors
        for c in base_theme_colors:
            theme_colors.add(c.upper())

        # Add generated variations (tints/shades) of theme colors
        theme_colors.update(self._generate_color_variations(base_theme_colors))

        # Add common neutral colors (grays)
        theme_colors.update(self._get_common_neutral_colors())

        # Add common defaults
        theme_colors.update(["#FFFFFF", "#000000", "TRANSPARENT", "INHERIT"])

        # Check colors
        for color, count in visual_analysis.get("colors_used", {}).items():
            color_upper = color.upper() if isinstance(color, str) else color
            if not color_upper:
                continue

            # First check exact match
            if color_upper in theme_colors:
                continue

            # Fallback: check if color is similar to any theme color (within threshold)
            is_similar = any(
                self._is_color_similar(color_upper, tc, threshold=25)
                for tc in theme_colors
                if tc.startswith("#")
            )

            if not is_similar:
                violations.append(
                    {
                        "type": "non_theme_color",
                        "severity": "warning",
                        "visual_id": visual_analysis["id"],
                        "visual_type": visual_type,
                        "detail": f"Color {color} not in theme palette",
                        "value": color,
                        "usage_count": count,
                    }
                )

        # Check fonts
        theme_fonts = set()
        for f in theme.get("fonts", {}).values():
            if f:
                theme_fonts.add(f.lower())

        # Add common defaults
        theme_fonts.update(["segoe ui", "arial", "helvetica", "din"])

        for font, count in visual_analysis.get("fonts_used", {}).items():
            font_lower = font.lower() if isinstance(font, str) else font
            # Check if any theme font is contained in the used font
            is_theme_font = any(tf in font_lower for tf in theme_fonts) if font_lower else False
            if font_lower and not is_theme_font:
                violations.append(
                    {
                        "type": "non_theme_font",
                        "severity": "info",
                        "visual_id": visual_analysis["id"],
                        "visual_type": visual_type,
                        "detail": f"Font '{font}' may not match theme",
                        "value": font,
                        "usage_count": count,
                    }
                )

        return violations

    def _aggregate_color_analysis(self, pages: List[Dict]) -> Dict[str, Any]:
        """Aggregate color usage across all pages."""
        all_colors = {}
        by_page = {}

        for page in pages:
            page_name = page.get("display_name") or page.get("id")
            by_page[page_name] = page.get("color_usage", {})

            for color, count in page.get("color_usage", {}).items():
                all_colors[color] = all_colors.get(color, 0) + count

        # Sort by usage
        sorted_colors = sorted(all_colors.items(), key=lambda x: x[1], reverse=True)

        return {
            "total_unique_colors": len(all_colors),
            "color_usage": dict(sorted_colors),
            "by_page": by_page,
            "top_colors": sorted_colors[:10],
        }

    def _aggregate_font_analysis(self, pages: List[Dict]) -> Dict[str, Any]:
        """Aggregate font usage across all pages."""
        all_fonts = {}

        for page in pages:
            for font, count in page.get("font_usage", {}).items():
                all_fonts[font] = all_fonts.get(font, 0) + count

        sorted_fonts = sorted(all_fonts.items(), key=lambda x: x[1], reverse=True)

        return {
            "total_unique_fonts": len(all_fonts),
            "font_usage": dict(sorted_fonts),
            "top_fonts": sorted_fonts[:5],
        }

    def _aggregate_visual_analysis(self, pages: List[Dict]) -> Dict[str, Any]:
        """Aggregate visual analysis across all pages."""
        visual_types = {}
        total_visuals = 0
        compliant_visuals = 0

        for page in pages:
            for visual in page.get("visuals", []):
                total_visuals += 1
                vtype = visual.get("visual_type", "unknown")
                visual_types[vtype] = visual_types.get(vtype, 0) + 1

                if visual.get("is_compliant", True):
                    compliant_visuals += 1

        return {
            "total_visuals": total_visuals,
            "compliant_visuals": compliant_visuals,
            "visual_type_distribution": visual_types,
            "compliance_rate": round(
                (compliant_visuals / total_visuals * 100) if total_visuals > 0 else 100, 1
            ),
        }

    def _identify_violations(
        self, result: Dict[str, Any], theme: Optional[Dict]
    ) -> List[Dict[str, Any]]:
        """Collect all violations from analysis."""
        violations = []

        for page in result.get("pages", []):
            page_name = page.get("display_name") or page.get("id")
            for v in page.get("violations", []):
                v["page"] = page_name
                violations.append(v)

        # Group by type
        by_type = {}
        for v in violations:
            vtype = v.get("type", "unknown")
            if vtype not in by_type:
                by_type[vtype] = []
            by_type[vtype].append(v)

        return violations

    def _calculate_compliance_score(self, result: Dict[str, Any]) -> int:
        """Calculate overall compliance score (0-100)."""
        score = 100

        violations = result.get("violations", [])

        # Deduct for violations
        warning_count = len([v for v in violations if v.get("severity") == "warning"])
        info_count = len([v for v in violations if v.get("severity") == "info"])

        # Warnings: -2 points each (max -40)
        score -= min(warning_count * 2, 40)

        # Info: -1 point each (max -20)
        score -= min(info_count * 1, 20)

        # Bonus for using a theme
        if result.get("theme_source") and "default" not in result.get("theme_source", "").lower():
            score = min(score + 5, 100)

        # Ensure score is in valid range
        return max(0, min(score, 100))

    def _generate_summary(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Generate summary statistics."""
        color_analysis = result.get("color_analysis", {})
        font_analysis = result.get("font_analysis", {})
        visual_analysis = result.get("visual_analysis", {})
        violations = result.get("violations", [])

        return {
            "compliance_score": result.get("compliance_score", 0),
            "theme_name": result.get("theme", {}).get("name", "Unknown"),
            "theme_source": result.get("theme_source", "Unknown"),
            "total_pages": len(result.get("pages", [])),
            "total_visuals": visual_analysis.get("total_visuals", 0),
            "compliant_visuals": visual_analysis.get("compliant_visuals", 0),
            "unique_colors": color_analysis.get("total_unique_colors", 0),
            "unique_fonts": font_analysis.get("total_unique_fonts", 0),
            "total_violations": len(violations),
            "warning_count": len([v for v in violations if v.get("severity") == "warning"]),
            "info_count": len([v for v in violations if v.get("severity") == "info"]),
        }
