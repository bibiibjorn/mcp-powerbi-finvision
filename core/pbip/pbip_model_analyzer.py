"""
PBIP Model Analyzer - Parses TMDL files and builds semantic model object graph.

Delegates to core.tmdl.unified_parser.UnifiedTmdlParser for actual parsing.
Provides backward-compatible dictionary output for existing consumers.
"""

import logging
import os
from typing import Any, Dict

from core.tmdl.unified_parser import UnifiedTmdlParser

logger = logging.getLogger(__name__)


class TmdlModelAnalyzer:
    """Analyzes TMDL files and builds semantic model object graph.

    This is the primary entry point for PBIP model analysis. It delegates
    to UnifiedTmdlParser for parsing and returns dictionary format for
    backward compatibility with existing consumers (pbip_handler,
    hybrid_analysis_handler, pbip_orchestrator, hybrid_analyzer, etc.).
    """

    def __init__(self):
        """Initialize the model analyzer."""
        self.logger = logger

    def analyze_model(self, model_folder: str) -> Dict[str, Any]:
        """
        Parse all TMDL files in the semantic model folder.

        Args:
            model_folder: Path to the .SemanticModel folder

        Returns:
            Dictionary with complete model structure

        Raises:
            FileNotFoundError: If model folder doesn't exist
            ValueError: If model format is invalid
        """
        if not os.path.exists(model_folder):
            raise FileNotFoundError(f"Model folder not found: {model_folder}")

        definition_path = os.path.join(model_folder, "definition")
        if not os.path.isdir(definition_path):
            raise ValueError(f"No definition folder found in {model_folder}")

        self.logger.info(f"Analyzing model: {model_folder}")

        try:
            parser = UnifiedTmdlParser(model_folder)
            parsed = parser.parse_full_model()

            # Convert to dict format for backward compatibility
            result = parsed.to_dict()
            result["model_folder"] = model_folder

            self.logger.info(
                f"Model analysis complete: {len(result['tables'])} tables, "
                f"{sum(len(t.get('measures', [])) for t in result['tables'])} measures"
            )

            return result

        except Exception as e:
            self.logger.error(f"Error analyzing model: {e}")
            raise

    def analyze_model_typed(self, model_folder: str):
        """
        Parse all TMDL files and return typed TmdlModel.

        Args:
            model_folder: Path to the .SemanticModel folder

        Returns:
            TmdlModel with all parsed components
        """
        if not os.path.exists(model_folder):
            raise FileNotFoundError(f"Model folder not found: {model_folder}")

        parser = UnifiedTmdlParser(model_folder)
        return parser.parse_full_model()
