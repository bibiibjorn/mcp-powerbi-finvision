"""Labelled evaluation of the client-masking engine (synthetic data only).

``python -m tools.masking_eval.run`` scores the engine per category: recall (no gold value left
readable), over-masking on text that must stay readable, and latency. Every name in the corpus is
invented; the corpus never touches a real model.
"""
