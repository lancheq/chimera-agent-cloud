"""Download the RAG embedding model into ``resources/embedding_model/``.

The guidelines vector DB (``resources/guidelines_db/``) is pre-built and
committed to the repo, but the embedding model used to encode queries at
runtime is ~1.2 GB and gitignored. This script fetches it so it can be
baked into the Grand Challenge container (the Dockerfile copies all of
``resources/`` into the image, and the container has no network access at
runtime).

Use this when you want the pre-built DB as-is — it does NOT touch the
PDF or rebuild the corpus. To rebuild the DB from the source PDF instead,
use ``scripts/process_guidelines.py`` (maintainer-only; needs the PDF).

    python scripts/download_embedding_model.py
    # or
    make fetch-embedding-model

The model is saved with ``SentenceTransformer.save()`` — the same on-disk
format ``process_guidelines.py`` produces and the runtime loads via
``SentenceTransformer(path)`` — so it stays compatible with the committed
DB. Accept the license on https://huggingface.co/google/embeddinggemma-300m
and set ``HF_TOKEN`` (e.g. in ``.env``) before running.
"""

import argparse
import logging
from pathlib import Path

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

# Must match scripts/process_guidelines.py — the committed DB was built with
# this exact model, so query embeddings must come from the same one.
MODEL_ID = "google/embeddinggemma-300m"


def main() -> None:
    parser = argparse.ArgumentParser(description="Download the RAG embedding model for runtime use")
    parser.add_argument(
        "--output-dir",
        default="resources",
        help="Directory holding embedding_model/ (default: resources)",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-download even if embedding_model/ already exists",
    )
    args = parser.parse_args()

    model_path = Path(args.output_dir) / "embedding_model"
    if model_path.exists() and not args.force:
        log.info("Embedding model already present at %s — skipping (use --force to re-download)", model_path)
        return

    from sentence_transformers import SentenceTransformer

    log.info("Downloading embedding model: %s", MODEL_ID)
    model = SentenceTransformer(MODEL_ID)

    log.info("Saving embedding model to %s", model_path)
    model.save(str(model_path))

    log.info("Done. The committed resources/guidelines_db/ + this model are all the RAG needs.")


if __name__ == "__main__":
    main()
