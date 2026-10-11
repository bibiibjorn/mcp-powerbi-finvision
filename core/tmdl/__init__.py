"""
TMDL Automation Module

This module provides comprehensive TMDL (Tabular Model Definition Language) automation capabilities:
- Unified TMDL parsing with typed domain models
- Validation and linting of TMDL files
- Bulk editing operations (find/replace, rename)
- Script generation for programmatic model creation

Version: 4.0.0
"""

from .unified_parser import UnifiedTmdlParser, parse_tmdl_model
from .models import (
    TmdlAnnotation,
    TmdlCalculationItem,
    TmdlColumn,
    TmdlCulture,
    TmdlDatabase,
    TmdlDatasource,
    TmdlExpression,
    TmdlHierarchy,
    TmdlHierarchyLevel,
    TmdlMeasure,
    TmdlModel,
    TmdlModelProperties,
    TmdlPartition,
    TmdlPerspective,
    TmdlPerspectiveItem,
    TmdlRelationship,
    TmdlRole,
    TmdlTable,
    TmdlTablePermission,
    TmdlTranslation,
)
from .validator import TmdlValidator, ValidationResult, LintIssue
from .bulk_editor import TmdlBulkEditor, ReplaceResult, RenameResult
from .script_generator import TmdlScriptGenerator
from .measure_migrator import TmdlMeasureMigrator, MigrationResult, MeasureInfo

__all__ = [
    # Unified parser
    "UnifiedTmdlParser",
    "parse_tmdl_model",
    # Domain models
    "TmdlAnnotation",
    "TmdlCalculationItem",
    "TmdlColumn",
    "TmdlCulture",
    "TmdlDatabase",
    "TmdlDatasource",
    "TmdlExpression",
    "TmdlHierarchy",
    "TmdlHierarchyLevel",
    "TmdlMeasure",
    "TmdlModel",
    "TmdlModelProperties",
    "TmdlPartition",
    "TmdlPerspective",
    "TmdlPerspectiveItem",
    "TmdlRelationship",
    "TmdlRole",
    "TmdlTable",
    "TmdlTablePermission",
    "TmdlTranslation",
    # Validation
    "TmdlValidator",
    "ValidationResult",
    "LintIssue",
    # Bulk editing
    "TmdlBulkEditor",
    "ReplaceResult",
    "RenameResult",
    # Script generation
    "TmdlScriptGenerator",
    # Measure migration
    "TmdlMeasureMigrator",
    "MigrationResult",
    "MeasureInfo",
]
