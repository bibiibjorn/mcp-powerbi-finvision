"""02_QA_Linguistic handler — Q&A linguistic schema CRUD on PBIP semantic models."""

from typing import Any, Dict
import logging

from server.registry import ToolDefinition
from server.tool_schemas import REQUEST_ID_PROPERTY
from core.operations.qa_linguistic_manager import QaLinguisticManager
from server.handlers._authoring_utils import _truthy, gate_and_wrap
from core.validation.error_response import error_envelope

logger = logging.getLogger(__name__)

_VALID_OPS = {"load", "audit", "set_include_in_qa", "add_synonym", "remove_synonym"}

# T0-2 gating: write ops mutate linguisticMetadata.json on disk. Previously
# this handler bypassed gate_and_wrap entirely so destructive writes passed
# under LIGHT with no audit log entry and no reload hint.
_READ_ONLY_QA_OPS = {"load", "audit"}
_CATASTROPHIC_QA_OPS: set = set()


def _dispatch(args: Dict[str, Any], _unused) -> Dict[str, Any]:
    op = args.get("operation")
    pbip_path = args.get("pbip_path")
    # C-17/G-23: default = the model's own `culture:` (en-GB on S01), not en-US.
    culture = args.get("culture") or None
    if op not in _READ_ONLY_QA_OPS and _truthy(args.get("dry_run")):
        # A-15 (wave 3): gate_and_wrap skips the gate for dry_run=true, and this
        # handler used to ignore the flag - so dry_run=true WROTE the file ungated.
        return {
            "success": True,
            "dry_run": True,
            "operation": op,
            "would_change": {
                k: args.get(k)
                for k in ("entity", "synonym", "include", "state", "culture")
                if args.get(k) is not None
            },
            "note": "Nothing was written. load / audit show the current linguistic state.",
        }
    mgr = QaLinguisticManager()
    if op == "load":
        return mgr.load(pbip_path=pbip_path, culture=culture)
    if op == "audit":
        return mgr.audit(pbip_path=pbip_path, culture=culture)
    if op == "set_include_in_qa":
        entity = args.get("entity")
        if not entity:
            return error_envelope("missing_argument", "entity is required")
        return mgr.set_include_in_qa(
            pbip_path=pbip_path,
            culture=culture,
            entity=entity,
            include=bool(args.get("include", True)),
        )
    if op == "add_synonym":
        entity = args.get("entity")
        synonym = args.get("synonym")
        if not entity or not synonym:
            return error_envelope("missing_argument", "entity and synonym are required")
        return mgr.add_synonym(
            pbip_path=pbip_path,
            culture=culture,
            entity=entity,
            synonym=synonym,
            state=args.get("state", "Authored"),
        )
    if op == "remove_synonym":
        entity = args.get("entity")
        synonym = args.get("synonym")
        if not entity or not synonym:
            return error_envelope("missing_argument", "entity and synonym are required")
        return mgr.remove_synonym(
            pbip_path=pbip_path,
            culture=culture,
            entity=entity,
            synonym=synonym,
        )
    return error_envelope("operation_failed", f"Unhandled operation '{op}'")


def handle_qa_linguistic(args: Dict[str, Any]) -> Dict[str, Any]:
    op = args.get("operation")
    if op not in _VALID_OPS:
        return error_envelope(
            "invalid_operation",
            f"Unknown operation '{op}'",
            valid_operations=sorted(_VALID_OPS),
        )
    pbip_path = args.get("pbip_path")
    if not pbip_path:
        return error_envelope("missing_argument", "pbip_path is required")

    return gate_and_wrap(
        args,
        op,
        tool_name="02_QA_Linguistic",
        dispatch_fn=_dispatch,
        pbip_path_or_defn=pbip_path,
        read_only_ops=_READ_ONLY_QA_OPS,
        catastrophic_ops=_CATASTROPHIC_QA_OPS,
    )


def register_qa_linguistic_handler(registry):
    registry.register(
        ToolDefinition(
            name="02_QA_Linguistic",
            description=(
                "Q&A linguistic schema of a PBIP semantic model (offline; Desktop closed for "
                "writes): load, audit (entities without synonyms), add_synonym, "
                "remove_synonym, set_include_in_qa. Reads cultures/<culture>.tmdl (model.bim "
                "and linguisticMetadata.json layouts too). Improves Copilot grounding."
            ),
            guide_notes=(
                "Q&A linguistic schema CRUD on PBIP semantic models. Read, audit, "
                "or surgically mutate the linguistic metadata stored inline in "
                "definition/cultures/<culture>.tmdl (TMDL projects; model.bim and "
                "legacy linguisticMetadata.json layouts also supported): list "
                "entities without synonyms, toggle IncludeInQnA, add/remove "
                "synonyms. Used to improve Copilot grounding quality. Operates "
                "offline on disk; PBI Desktop must be closed for writes to land."
            ),
            handler=handle_qa_linguistic,
            input_schema={
                "type": "object",
                "properties": {
                    "operation": {
                        "type": "string",
                        "enum": sorted(_VALID_OPS),
                        "description": "Operation to perform.",
                    },
                    # F-27: idempotency key (deferred in tools/list)
                    "request_id": dict(REQUEST_ID_PROPERTY),
                    "pbip_path": {
                        "type": "string",
                        "description": ".pbip, its folder or the .SemanticModel folder.",
                        "x-doc": (
                            "Path to the PBIP project: .pbip file, its folder, or the "
                            ".SemanticModel folder (required when a folder holds several models)."
                        ),
                    },
                    "culture": {
                        "type": "string",
                        "description": (
                            "Culture code (e.g. 'en-GB'). Default: the model's own "
                            "`culture:` from model.tmdl (fallback 'en-US')."
                        ),
                    },
                    "entity": {
                        "type": "string",
                        "description": "Entity name (set_include_in_qa, add/remove_synonym).",
                    },
                    "synonym": {
                        "type": "string",
                        "description": "Synonym word (add/remove_synonym).",
                    },
                    "include": {
                        "type": "boolean",
                        "description": "IncludeInQnA flag (set_include_in_qa).",
                    },
                    "state": {
                        "type": "string",
                        "default": "Authored",
                        "description": "Term state (add_synonym, default 'Authored').",
                    },
                    "dry_run": {
                        "type": "boolean",
                        "description": "Writes: describe the change, write nothing.",
                    },
                },
                "required": ["operation", "pbip_path"],
            },
            category="pbip",
            sort_order=205,
            annotations={
                "readOnlyHint": False,
                "destructiveHint": True,
                "idempotentHint": False,
                "openWorldHint": True,
            },
        )
    )
