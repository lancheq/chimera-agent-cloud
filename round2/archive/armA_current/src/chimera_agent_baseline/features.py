"""Per-case feature-embedding loader.

Each patient may ship a single ``prostate-modality-level-neural-representations.json`` alongside ``structured-prompt.json``
and ``*-clinical-data.json``, holding frozen foundation-model embeddings of
different origin, separated by JSON attribute::

    {
      "MRI image":           [[...]],          # one vector   (all tasks)
      "Biopsy slide":        [[...], [...]],   # one or more  (tasks 2, 3)
      "Prostatectomy slide": [[...], [...]]    # one or more  (task 3 only)
    }

Each origin maps to a **list of feature vectors** (a list of JSON arrays) so
loading is uniform across origins — including MRI, which is a single-element
list. Vectors are the raw foundation-model output (e.g. 960-d) and are **not**
meant to enter the LLM context directly: build a predictor or tool on top of
them and feed the agent a compact score/label instead.

The baseline does not consume features. This loader is provided for
participants who want to; it is fully decoupled from the agent graph and the
MCP server.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

log = logging.getLogger(__name__)

#: Per-case embeddings filename, alongside ``structured-prompt.json`` / ``*-clinical-data.json``.
FEATURES_FILENAME = "prostate-modality-level-neural-representations.json"

#: Known embedding origins (JSON attributes inside ``features.json``).
FEATURE_ORIGINS = ("MRI image", "Biopsy slide", "Prostatectomy slide")

#: Which origins appear for each task (others are absent from the file).
FEATURE_ORIGINS_BY_TASK: dict[int, tuple[str, ...]] = {
    1: ("MRI image",),
    2: ("MRI image", "Biopsy slide"),
    3: ("MRI image", "Biopsy slide", "Prostatectomy slide"),
}

#: A single feature vector.
Vector = list[float]


def _as_vector_list(value: list) -> list[Vector]:
    """Coerce an origin's value to a list of vectors.

    Accepts the canonical list-of-arrays form, and also tolerates a single
    bare vector (a flat list of numbers) by wrapping it in a one-element list.
    """
    if not value:
        return []
    if isinstance(value[0], (int, float)):
        return [list(value)]
    return [list(v) for v in value]


def _normalise(payload: dict) -> dict[str, list[Vector]]:
    """Extract the known origins from a ``prostate-modality-level-neural-representations.json`` payload."""
    out: dict[str, list[Vector]] = {}
    for origin in FEATURE_ORIGINS:
        if origin in payload and payload[origin] is not None:
            out[origin] = _as_vector_list(payload[origin])
    return out


class FeatureStore:
    """Loads per-case ``prostate-modality-level-neural-representations.json`` files and indexes them by ``case_id``.

    Layout mirrors the agent input::

        data_dir/
          <case>/prostate-modality-level-neural-representations.json   # optional, alongside prompt.json / clinical.json

    Cases without a ``prostate-modality-level-neural-representations.json`` are simply absent from the store;
    origins absent for a case (e.g. ``Prostatectomy slide`` outside task 3) are
    absent from that case's mapping.
    """

    def __init__(self, data_dir: str | Path):
        self._features: dict[str, dict[str, list[Vector]]] = {}
        self._load(Path(data_dir))

    def _load(self, data_dir: Path) -> None:
        if not data_dir.is_dir():
            log.warning("Data dir %s is not a directory", data_dir)
            return

        subdirs = sorted(p for p in data_dir.iterdir() if p.is_dir() and (p / FEATURES_FILENAME).exists())
        for sub in subdirs:
            features_file = sub / FEATURES_FILENAME
            try:
                payload = json.loads(features_file.read_text())
            except json.JSONDecodeError:
                log.warning("Skipping %s: invalid JSON", features_file)
                continue
            case_id = payload.get("case_id") or sub.name
            self._features[case_id] = _normalise(payload)
        log.info("Loaded features for %d cases from %s", len(self._features), data_dir)

    def list_case_ids(self) -> list[str]:
        return list(self._features.keys())

    def get(self, case_id: str) -> dict[str, list[Vector]]:
        """Return all embeddings for a case, keyed by origin (``{}`` if none)."""
        return self._features.get(case_id, {})

    def get_origin(self, case_id: str, origin: str) -> list[Vector]:
        """Return the feature vectors for one origin (``[]`` if absent)."""
        return self._features.get(case_id, {}).get(origin, [])
