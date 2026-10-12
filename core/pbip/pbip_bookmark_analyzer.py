"""
PBIP Bookmark Analyzer - Analyzes bookmark configurations in PBIP reports.

This module provides comprehensive analysis of Power BI bookmarks including:
- Bookmark categorization (navigation, filter state, visual state)
- Orphaned bookmark detection
- Naming convention analysis
- Complexity analysis
- State configuration breakdown
"""

import logging
import os
from typing import Dict, List, Optional, Any, Set
from datetime import datetime
from core.utilities.json_utils import load_json
from core.pbip.pbir_model import bookmark_membership, decode_literal, get_bookmark_references

logger = logging.getLogger(__name__)


class PbipBookmarkAnalyzer:
    """Analyzes bookmark configurations in PBIP reports."""

    def __init__(self):
        """Initialize the bookmark analyzer."""
        self.logger = logger

    def analyze_bookmarks(self, report_folder: str) -> Dict[str, Any]:
        """
        Analyze all bookmarks in a PBIP report.

        Args:
            report_folder: Path to the .Report folder

        Returns:
            Dictionary with comprehensive bookmark analysis
        """
        if not os.path.exists(report_folder):
            raise FileNotFoundError(f"Report folder not found: {report_folder}")

        definition_path = os.path.join(report_folder, "definition")
        if not os.path.isdir(definition_path):
            raise ValueError(f"No definition folder found in {report_folder}")

        self.logger.info(f"Analyzing bookmarks in: {report_folder}")

        result = {
            "report_folder": report_folder,
            "analysis_timestamp": datetime.now().isoformat(),
            "bookmarks": [],
            "summary": {},
            "categories": {},
            "issues": [],
            "navigation_structure": {},
            "pages": [],
        }

        # Parse pages first (for navigation button detection)
        pages_path = os.path.join(definition_path, "pages")
        if os.path.isdir(pages_path):
            result["pages"] = self._parse_pages_for_navigation(pages_path)

        # Parse bookmarks
        bookmarks_path = os.path.join(definition_path, "bookmarks")
        result["bookmark_groups"] = {}
        if os.path.isdir(bookmarks_path):
            result["bookmarks"] = self._parse_all_bookmarks(bookmarks_path)
            meta_file = os.path.join(bookmarks_path, "bookmarks.json")
            if os.path.isfile(meta_file):
                try:
                    result["bookmark_groups"] = bookmark_membership(load_json(meta_file) or {})[
                        "groups"
                    ]
                except Exception as e:  # noqa: BLE001
                    self.logger.warning(f"Could not read bookmarks.json: {e}")
        else:
            self.logger.info("No bookmarks folder found - report has no bookmarks")

        # Analyze bookmark usage and categorization
        result = self._analyze_bookmark_usage(result)

        # Generate summary statistics
        result["summary"] = self._generate_summary(result)

        return result

    def _parse_all_bookmarks(self, bookmarks_path: str) -> List[Dict[str, Any]]:
        """Parse all bookmark JSON files."""
        bookmarks = []

        try:
            for filename in os.listdir(bookmarks_path):
                if filename.endswith(".bookmark.json"):
                    bookmark_file = os.path.join(bookmarks_path, filename)
                    bookmark = self._parse_bookmark_json(bookmark_file)
                    if bookmark:
                        bookmarks.append(bookmark)

        except Exception as e:
            self.logger.warning(f"Error parsing bookmarks: {e}")

        return bookmarks

    def _parse_bookmark_json(self, bookmark_file: str) -> Optional[Dict[str, Any]]:
        """Parse a single bookmark JSON file with detailed analysis."""
        try:
            data = load_json(bookmark_file)

            bookmark = {
                "id": data.get("name", ""),
                "display_name": data.get("displayName", ""),
                "file_path": bookmark_file,
                "raw_state": data.get("state", {}),
                "analysis": {},
            }

            # Analyze the state
            state = data.get("state", {})
            analysis = self._analyze_bookmark_state(state, data)
            bookmark["analysis"] = analysis

            # Determine category
            bookmark["category"] = self._categorize_bookmark(analysis)

            # Calculate complexity score
            bookmark["complexity_score"] = self._calculate_complexity(analysis)

            # Check for naming issues
            bookmark["naming_issues"] = self._check_naming_conventions(
                bookmark["display_name"], bookmark["id"]
            )

            return bookmark

        except Exception as e:
            self.logger.warning(f"Error parsing bookmark {bookmark_file}: {e}")
            return None

    def _analyze_bookmark_state(self, state: Dict, full_data: Dict) -> Dict[str, Any]:
        """Analyze the bookmark state to understand what it captures."""
        analysis = {
            "has_page_context": False,
            "page_name": None,
            "has_visual_states": False,
            "visual_count": 0,
            "hidden_visuals": [],
            "visible_visuals": [],
            "has_filters": False,
            "filter_count": 0,
            "filters": [],
            "has_spotlight": False,
            "spotlighted_visual": None,
            "has_drill_state": False,
            "has_slicer_states": False,
            "slicer_states": [],
            "has_selection": False,
            "selected_visual": None,
            "capture_options": [],
            "exploration_state": None,
        }

        try:
            # Check for page context
            if "currentPage" in state or "page" in state:
                analysis["has_page_context"] = True
                analysis["page_name"] = state.get("currentPage") or state.get("page")

            # Check for visual states (visibility)
            visual_states = state.get("visualContainers", [])
            if visual_states:
                analysis["has_visual_states"] = True
                analysis["visual_count"] = len(visual_states)
                for vs in visual_states:
                    visual_id = vs.get("id", vs.get("name", "unknown"))
                    is_hidden = vs.get("isHidden", False)
                    if is_hidden:
                        analysis["hidden_visuals"].append(visual_id)
                    else:
                        analysis["visible_visuals"].append(visual_id)

            # Check for filters
            filters = state.get("filters", [])
            if filters:
                analysis["has_filters"] = True
                analysis["filter_count"] = len(filters)
                for filt in filters:
                    filter_info = {
                        "name": filt.get("name", ""),
                        "type": filt.get("type", ""),
                        "has_values": bool(filt.get("filter")),
                    }
                    analysis["filters"].append(filter_info)

            # Check for spotlight
            if state.get("spotlightEnabled") or "spotlight" in state:
                analysis["has_spotlight"] = True
                analysis["spotlighted_visual"] = state.get("spotlight", {}).get("visualId")

            # Check for drill state
            if "drill" in state or state.get("drillState"):
                analysis["has_drill_state"] = True

            # Check for slicer states
            slicer_states = state.get("slicerStates", [])
            if slicer_states:
                analysis["has_slicer_states"] = True
                for ss in slicer_states:
                    slicer_info = {
                        "visual_id": ss.get("visualId", ""),
                        "has_state": bool(ss.get("state")),
                    }
                    analysis["slicer_states"].append(slicer_info)

            # Check for selection
            if state.get("selection") or state.get("selectedVisual"):
                analysis["has_selection"] = True
                analysis["selected_visual"] = state.get("selectedVisual")

            # Extract capture options from full_data
            options = full_data.get("options", {})
            if options.get("captureData"):
                analysis["capture_options"].append("data")
            if options.get("captureDisplay"):
                analysis["capture_options"].append("display")
            if options.get("captureCurrentPage"):
                analysis["capture_options"].append("current_page")
            if options.get("captureAllPages"):
                analysis["capture_options"].append("all_pages")

            # Check for exploration state
            if "explorationState" in state:
                analysis["exploration_state"] = state.get("explorationState")

        except Exception as e:
            self.logger.warning(f"Error analyzing bookmark state: {e}")

        return analysis

    def _categorize_bookmark(self, analysis: Dict[str, Any]) -> str:
        """Categorize a bookmark based on its state analysis."""
        # Check for navigation bookmark (primarily changes page)
        if (
            analysis["has_page_context"]
            and not analysis["has_filters"]
            and not analysis["has_visual_states"]
        ):
            return "navigation"

        # Check for filter state bookmark
        if analysis["has_filters"] or analysis["has_slicer_states"]:
            if analysis["has_visual_states"]:
                return "combined_filter_visual"
            return "filter_state"

        # Check for visual state bookmark
        if analysis["has_visual_states"]:
            if analysis["has_spotlight"]:
                return "spotlight"
            return "visual_state"

        # Check for selection bookmark
        if analysis["has_selection"]:
            return "selection"

        # Check for drill state bookmark
        if analysis["has_drill_state"]:
            return "drill_state"

        # Default
        return "unknown"

    def _calculate_complexity(self, analysis: Dict[str, Any]) -> int:
        """Calculate a complexity score for the bookmark (1-10)."""
        score = 1  # Base score

        # Add for visual states
        if analysis["has_visual_states"]:
            score += min(analysis["visual_count"] // 5, 3)  # Up to 3 points

        # Add for filters
        if analysis["has_filters"]:
            score += min(analysis["filter_count"], 3)  # Up to 3 points

        # Add for slicer states
        if analysis["has_slicer_states"]:
            score += 1

        # Add for spotlight
        if analysis["has_spotlight"]:
            score += 1

        # Add for drill state
        if analysis["has_drill_state"]:
            score += 1

        return min(score, 10)

    def _check_naming_conventions(self, display_name: str, id_name: str) -> List[str]:
        """Check for naming convention issues."""
        issues = []

        # Check for generic names
        generic_patterns = ["bookmark", "new bookmark", "bookmark 1", "untitled"]
        if display_name.lower() in generic_patterns or any(
            p in display_name.lower() for p in generic_patterns
        ):
            issues.append("Generic bookmark name")

        # Check for very short names
        if len(display_name) < 3:
            issues.append("Name too short")

        # Check for very long names
        if len(display_name) > 50:
            issues.append("Name too long")

        # Check for special characters that might cause issues
        special_chars = ["<", ">", ":", '"', "/", "\\", "|", "?", "*"]
        if any(c in display_name for c in special_chars):
            issues.append("Contains special characters")

        # Check if name starts with number
        if display_name and display_name[0].isdigit():
            issues.append("Starts with number")

        return issues

    def _parse_pages_for_navigation(self, pages_path: str) -> List[Dict[str, Any]]:
        """Parse pages to find navigation buttons that reference bookmarks."""
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
                page_info = {
                    "id": page_data.get("name", page_id),
                    "display_name": page_data.get("displayName", ""),
                    "bookmark_references": [],
                    "navigation_buttons": [],
                }

                # Scan visuals for bookmark references
                visuals_path = os.path.join(page_folder, "visuals")
                if os.path.isdir(visuals_path):
                    for visual_id in os.listdir(visuals_path):
                        visual_folder = os.path.join(visuals_path, visual_id)
                        if not os.path.isdir(visual_folder):
                            continue

                        visual_json_path = os.path.join(visual_folder, "visual.json")
                        if os.path.exists(visual_json_path):
                            visual_data = load_json(visual_json_path)
                            bookmark_refs = self._find_bookmark_references(visual_data)
                            page_info["bookmark_references"].extend(bookmark_refs)
                            nav = get_bookmark_references(visual_data)

                            # Check for navigation buttons
                            visual_type = visual_data.get("visual", {}).get("visualType", "")
                            if visual_type in [
                                "actionButton",
                                "navigatorButton",
                                "bookmarkNavigator",
                            ]:
                                page_info["navigation_buttons"].append(
                                    {
                                        "visual_id": visual_id,
                                        "type": visual_type,
                                        "bookmark_refs": bookmark_refs,
                                        "bookmark_groups": nav["bookmark_groups"],
                                        # a navigator with no group shows EVERY bookmark
                                        "shows_all_bookmarks": visual_type == "bookmarkNavigator"
                                        and not nav["bookmark_groups"],
                                    }
                                )

                pages.append(page_info)

        except Exception as e:
            self.logger.warning(f"Error parsing pages for navigation: {e}")

        return pages

    def _find_bookmark_references(self, visual_data: Dict) -> List[str]:
        """Bookmark ids a visual points at (G-11).

        PBIR action buttons store ``bookmark: {"expr": {"Literal": {"Value":
        "'<id>'"}}}`` (the old matcher only took strings / ``{"name"}`` and
        extracted nothing); navigators point at a group and a selected bookmark.
        Legacy string shapes are still accepted.
        """
        refs = get_bookmark_references(visual_data)
        references: List[str] = list(refs["bookmarks"]) + [
            b for b in refs["selected_bookmarks"] if b not in refs["bookmarks"]
        ]

        def literal(value: Any) -> Optional[str]:
            if isinstance(value, str) and value:
                return value
            if isinstance(value, dict):
                if isinstance(value.get("name"), str):
                    return value["name"]
                lit = ((value.get("expr") or {}).get("Literal") or {}).get("Value")
                if isinstance(lit, str):
                    v, kind = decode_literal(lit)
                    return v if kind == "string" and isinstance(v, str) else lit
            return None

        def search(obj: Any) -> None:
            if isinstance(obj, dict):
                for key, value in obj.items():
                    if key.lower() in (
                        "bookmark",
                        "bookmarkname",
                        "bookmarktarget",
                        "targetbookmark",
                    ):
                        ident = literal(value)
                        if ident and ident not in references:
                            references.append(ident)
                    else:
                        search(value)
            elif isinstance(obj, list):
                for item in obj:
                    search(item)

        search(visual_data)
        return references

    def _analyze_bookmark_usage(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Analyze bookmark usage across the report."""
        bookmarks = result.get("bookmarks", [])
        pages = result.get("pages", [])

        # Collect all bookmark IDs and display names
        bookmark_ids = {b["id"] for b in bookmarks}

        # Collect all bookmark references from navigation: action buttons,
        # navigators' groups (every child of the group) and "all bookmarks"
        # navigators (no group set)
        referenced_bookmarks: Set[str] = set()
        groups = result.get("bookmark_groups") or {}
        shows_all = False
        for page in pages:
            for ref in page.get("bookmark_references", []):
                referenced_bookmarks.add(ref)
            for btn in page.get("navigation_buttons", []):
                for g in btn.get("bookmark_groups", []) or []:
                    referenced_bookmarks.update(groups.get(g, []))
                shows_all = shows_all or bool(btn.get("shows_all_bookmarks"))
        if shows_all:
            referenced_bookmarks.update(bookmark_ids)

        # Identify orphaned bookmarks (not referenced anywhere)
        orphaned = []
        for bookmark in bookmarks:
            if (
                bookmark["id"] not in referenced_bookmarks
                and bookmark["display_name"] not in referenced_bookmarks
            ):
                orphaned.append(bookmark["id"])
                bookmark["is_orphaned"] = True
            else:
                bookmark["is_orphaned"] = False

        # Categorize bookmarks
        categories = {
            "navigation": [],
            "filter_state": [],
            "visual_state": [],
            "spotlight": [],
            "combined_filter_visual": [],
            "selection": [],
            "drill_state": [],
            "unknown": [],
        }

        for bookmark in bookmarks:
            category = bookmark.get("category", "unknown")
            if category in categories:
                categories[category].append(bookmark["display_name"])
            else:
                categories["unknown"].append(bookmark["display_name"])

        result["categories"] = {k: v for k, v in categories.items() if v}

        # Identify issues
        issues = []

        # Orphaned bookmarks
        if orphaned:
            issues.append(
                {
                    "type": "orphaned_bookmarks",
                    "severity": "warning",
                    "message": f"{len(orphaned)} bookmark(s) are not referenced by any navigation",
                    "bookmarks": orphaned,
                }
            )

        # Naming issues
        naming_issues = []
        for bookmark in bookmarks:
            if bookmark.get("naming_issues"):
                naming_issues.append(
                    {"bookmark": bookmark["display_name"], "issues": bookmark["naming_issues"]}
                )

        if naming_issues:
            issues.append(
                {
                    "type": "naming_issues",
                    "severity": "info",
                    "message": f"{len(naming_issues)} bookmark(s) have naming convention issues",
                    "details": naming_issues,
                }
            )

        # High complexity bookmarks
        high_complexity = [b for b in bookmarks if b.get("complexity_score", 0) >= 7]
        if high_complexity:
            issues.append(
                {
                    "type": "high_complexity",
                    "severity": "info",
                    "message": f"{len(high_complexity)} bookmark(s) have high complexity scores",
                    "bookmarks": [b["display_name"] for b in high_complexity],
                }
            )

        result["issues"] = issues

        # Build navigation structure
        nav_structure = {"pages": {}}
        for page in pages:
            page_name = page.get("display_name") or page.get("id")
            nav_buttons = page.get("navigation_buttons", [])
            if nav_buttons:
                nav_structure["pages"][page_name] = {"buttons": len(nav_buttons), "targets": []}
                for btn in nav_buttons:
                    nav_structure["pages"][page_name]["targets"].extend(
                        btn.get("bookmark_refs", [])
                    )

        result["navigation_structure"] = nav_structure

        return result

    def _generate_summary(self, result: Dict[str, Any]) -> Dict[str, Any]:
        """Generate summary statistics."""
        bookmarks = result.get("bookmarks", [])
        categories = result.get("categories", {})
        issues = result.get("issues", [])

        summary = {
            "total_bookmarks": len(bookmarks),
            "by_category": {k: len(v) for k, v in categories.items()},
            "orphaned_count": len([b for b in bookmarks if b.get("is_orphaned")]),
            "avg_complexity": 0,
            "max_complexity": 0,
            "issue_count": len(issues),
            "warning_count": len([i for i in issues if i.get("severity") == "warning"]),
            "info_count": len([i for i in issues if i.get("severity") == "info"]),
        }

        if bookmarks:
            complexity_scores = [b.get("complexity_score", 0) for b in bookmarks]
            summary["avg_complexity"] = round(sum(complexity_scores) / len(complexity_scores), 1)
            summary["max_complexity"] = max(complexity_scores)

        return summary
