"""
ID Generator for PBIP Report Authoring

Generates unique identifiers for pages, visuals, and bookmarks in PBIP format.
PBIP uses 20-character hex strings for page/visual IDs and standard GUIDs for
platform metadata.
"""

import secrets
import uuid


def generate_visual_id() -> str:
    """Generate a 20-character hex string for page/visual/bookmark IDs.

    This matches the format used by Power BI Desktop for all internal
    identifiers within PBIP report definitions.

    Returns:
        20-character lowercase hex string (e.g., "b81ad76bede761849700")
    """
    return secrets.token_hex(10)


def generate_guid() -> str:
    """Generate a standard UUID4 for .platform and .pbir files.

    Returns:
        Standard UUID string (e.g., "550e8400-e29b-41d4-a716-446655440000")
    """
    return str(uuid.uuid4())
