"""Download the bge-reranker-base cross-encoder into resources/reranker_model/.

The reranker is used by GuidelinesSearch._rerank() to re-score hybrid
retrieval candidates on CPU (no GPU contention). The Grand Challenge
container has no network at runtime, so this must be run during
development and the model baked into the image.

    python scripts/download_reranker.py
    # or
    make fetch-reranker
"""

import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

MODEL_ID = "BAAI/bge-reranker-base"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the bge-reranker-base reranker for offline use")
    parser.add_argument(
        "--output-dir",
        default="resources",
        help="Directory holding reranker_model/ (default: resources)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if reranker_model/ already exists",
    )
    args = parser.parse_args()

    model_path = Path(args.output_dir) / "reranker_model"
    if model_path.exists() and not args.force:
        log.info("Reranker model already present at %s — skipping (use --force to re-download)", model_path)
        return

    from transformers import AutoModelForSequenceClassification, AutoTokenizer

    log.info("Downloading reranker model: %s", MODEL_ID)
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID)
    model = AutoModelForSequenceClassification.from_pretrained(MODEL_ID)

    log.info("Saving reranker model to %s", model_path)
    model_path.mkdir(parents=True, exist_ok=True)
    tokenizer.save_pretrained(str(model_path))
    model.save_pretrained(str(model_path))

    log.info("Done. GuidelinesSearch will auto-detect %s at runtime.", model_path)


if __name__ == "__main__":
    main()
