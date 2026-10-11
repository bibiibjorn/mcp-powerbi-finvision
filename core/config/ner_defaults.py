"""The default NER model name, in a module of its own: the NER worker process needs it before it
loads gliner / torch and must not import the masking engine (``core.client_guard``) for it."""

# urchade/gliner_multi_pii-v1 since 2026-10-08: on the synthetic NL/FR/EN sets of
# tools/masking_eval/ner_models.py it found 101 of 107 names (the earlier default, its own
# fine-tune E3-JSI/gliner-multi-pii-domains-v1, 96), 0 false hits for both, same library.
# Config client_guard.ner.model overrides it.
DEFAULT_MODEL = "urchade/gliner_multi_pii-v1"
