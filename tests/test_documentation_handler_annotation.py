"""Regression: 08_Documentation_Word writes a .docx to disk on both `generate`
and `update`, so it must NOT be annotated readOnlyHint=True. When
security.read_only_mode is on, the dispatcher blocks tools that lack
readOnlyHint — a mislabeled writer silently bypasses that lockdown.
"""

from server.handlers.documentation_handler import register_documentation_handlers
from server.registry import HandlerRegistry


def test_documentation_word_is_not_read_only():
    reg = HandlerRegistry()
    register_documentation_handlers(reg)
    td = reg._handlers["08_Documentation_Word"]
    assert td.annotations["readOnlyHint"] is False, (
        "08_Documentation_Word writes a .docx — marking it read-only lets it "
        "bypass read_only_mode lockdown."
    )
    # It's a benign disk write, not a destructive model/report mutation.
    assert td.annotations["destructiveHint"] is False
