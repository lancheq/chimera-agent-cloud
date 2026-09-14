"""Verify the sklearn cross-version fix on REAL cases inside the real image.

GC failure (run 17a095b4):
    predictor_task1/2.joblib were pickled by scikit-learn 1.9.0; the image
    ships 1.6.1. The 1.9.0 pickle carries no ``multi_class`` key, so
    ``LogisticRegression.get_params()`` raises AttributeError on every call --
    and ``predict_proba`` calls it internally. The GC log only showed
    "predictor failed", because the exception is caught upstream.

This probe forces the entire Predictor path (not just the estimator) on real
case directories and reports, for each task:
  * load OK / get_params / predict / predict_proba
  * the actual decision + probabilities BEFORE the fix
  * the same AFTER applying ``clf.multi_class = "auto"`` (the value the models
    were trained with)

Mount cases and models read-only:

    docker run --rm --network=none \
      -v "$PWD/chimera-agent-baseline/data:/cases:ro" \
      -v "$PWD/model:/opt/ml/model:ro" \
      -v "$PWD/.probe_mount:/mnt:ro" \
      --entrypoint python3 chimera-agent:submit /mnt/predictor_fix_verify.py
"""
import sys
import traceback
from pathlib import Path

sys.path.insert(0, "/opt/app")

import numpy as np  # noqa: E402

from src.chimera_agent_baseline.predictor import Predictor  # noqa: E402

CASES = Path("/cases")
MODEL_DIR = Path("/opt/ml/model/predictor")


def first_case(task: int) -> Path | None:
    d = CASES / f"task{task}" / "agent_input"
    if not d.exists():
        return None
    subs = sorted(p for p in d.iterdir() if p.is_dir())
    return subs[0] if subs else None


def show(label: str, pred: dict) -> None:
    print(f"    {label}: decision={pred.get('decision') or pred.get('event')}"
          f"  probabilities={pred.get('probabilities')}")


for task in (1, 2, 3):
    case = first_case(task)
    print(f"===== task {task} (case {case.name if case else 'NONE'}) =====")
    if case is None:
        print("  no case dir, skip")
        continue

    p = Predictor(MODEL_DIR)

    # --- before -------------------------------------------------------------
    try:
        pred = p.predict(case, task)
        show("BEFORE", pred)
        before_ok = True
    except Exception as e:
        print(f"    BEFORE: FAILED {type(e).__name__}: {e}")
        before_ok = False

    # --- apply the candidate fix -------------------------------------------
    m = p._models.get(task)
    clf = (m or {}).get("clf")
    patched = []
    if clf is not None and not hasattr(clf, "multi_class"):
        clf.multi_class = "auto"
        patched.append("clf.multi_class='auto'")
    try:
        pred = p.predict(case, task)
        show("AFTER ", pred)
        after_ok = True
    except Exception:
        print("    AFTER: still FAILED")
        traceback.print_exc()
        after_ok = False

    print(f"  patched: {patched or '(nothing needed)'}")
    print(f"  result : BEFORE {'ok' if before_ok else 'FAIL'} -> "
          f"AFTER {'ok' if after_ok else 'FAIL'}")
    print()

print("PROBE DONE")
