"""Optional image-embedding predictor tool — template, default-off.

The baseline does not consume the frozen image embeddings in
``features.json``. This module is a ready-to-wire *template* showing how a
participant can expose a predictor as an MCP tool **without** leaking raw
feature vectors into the LLM context: the tool loads the embeddings via
:class:`~chimera_agent_baseline.features.FeatureStore`, runs a model, and
returns only a compact score/label the agent can reason over.

Enable it with ``agent.predictor.enabled=true``. Then replace
:func:`run_predictor` with your trained head — it receives **all** of a
patient's embeddings (MRI / biopsy / prostatectomy, whichever are present), so
you can use a single origin or fuse them multimodally. E.g. a Cox head over the
fused features for task 3, or a classifier for tasks 1 / 2.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable

from chimera_agent_baseline.features import FeatureStore, Vector

log = logging.getLogger(__name__)

PREDICTOR_TOOL_NAME = "get_image_predictor"


def run_predictor(features: dict[str, list[Vector]]) -> dict:
    """STUB — replace with your trained head.

    Receives **all** of a patient's frozen foundation-model embeddings, keyed
    by origin (``mri`` / ``biopsy`` / ``prostatectomy``); each value is a list
    of vectors. Use any subset or fuse them. Must return a small
    JSON-serialisable dict (a score / label / risk), **never** the raw vectors.
    This placeholder returns no prediction so the wiring runs end-to-end
    without a model.
    """
    return {
        "prediction": None,
        "detail": "stub predictor — replace tools.predictor.run_predictor with your trained head",
    }


def make_predictor_tool(feature_store: FeatureStore) -> Callable[[str], str]:
    """Build the ``get_image_predictor`` MCP tool function."""

    def get_image_predictor(case_id: str) -> str:
        features = feature_store.get(case_id)
        if not features:
            return json.dumps({"case_id": case_id, "note": "No embeddings available for this case."})
        result = run_predictor(features)
        # Report only which origins were present + the compact predictor output;
        # never echo the raw vectors back to the agent.
        origins = {origin: len(vectors) for origin, vectors in features.items()}
        return json.dumps({"case_id": case_id, "origins": origins, **result})

    get_image_predictor.__name__ = PREDICTOR_TOOL_NAME
    get_image_predictor.__doc__ = (
        "Run the image-embedding predictor over the patient's available embeddings "
        "(MRI / biopsy / prostatectomy, whichever are present) and return a compact "
        "score/label (not the raw vectors). Use it when the imaging / pathology "
        "embeddings could change your decision."
    )
    get_image_predictor.__annotations__ = {"case_id": str, "return": str}
    return get_image_predictor
