"""Process the EAU guidelines PDF into a ChromaDB collection.

Extracts text from the PDF, chunks it with section awareness, computes
embeddings with google/embeddinggemma-300m, and persists the result to
``resources/guidelines_db/``.  Also saves the embedding model to
``resources/embedding_model/`` for use at runtime.

Run once before building the Docker image::

    python scripts/process_guidelines.py
    # or
    make process-guidelines

Augment mode (merge an external normalized JSONL into the existing DB)::

    python scripts/process_guidelines.py \
        --input guideline_dataset/normalized/guidelines_unified.jsonl --rebuild

``--rebuild`` keeps every chunk already present in ``resources/guidelines_db``
(official EAU 1501 chunks) and adds re-chunked entries from ``--input`` with
metadata ``{source, section, page}``.  The existing DB directory is expected
to be backed up beforehand (e.g. ``resources/guidelines_db.bak``).
"""

import argparse
import json
import logging
import re
import shutil
from pathlib import Path

import fitz  # pymupdf

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)

CHUNK_SIZE = 1000
CHUNK_OVERLAP = 200
COLLECTION_NAME = "guidelines"
MODEL_ID = "google/embeddinggemma-300m"


# ---------------------------------------------------------------------------
# PDF extraction
# ---------------------------------------------------------------------------


def extract_pages(pdf_path: str) -> list[dict]:
    """Extract and clean text from each page of the PDF."""
    doc = fitz.open(pdf_path)
    pages = []
    for i, page in enumerate(doc):
        text = page.get_text("text")
        text = clean_text(text)
        if text.strip():
            pages.append({"page": i + 1, "text": text})
    log.info("Extracted text from %d / %d pages", len(pages), len(doc))
    return pages


# ---------------------------------------------------------------------------
# Text cleaning
# ---------------------------------------------------------------------------

# Repeated page header pattern (e.g. "PROSTATE CANCER - LIMITED UPDATE MARCH 2026")
_PAGE_HEADER_RE = re.compile(r"^\d+\s*\n\s*PROSTATE CANCER.*?(?:\d{4})\s*$", re.MULTILINE)
# Lines that are just a page number
_PAGE_NUMBER_RE = re.compile(r"^\s*\d{1,3}\s*$", re.MULTILINE)
# Runs of 3+ whitespace characters (tabs, spaces, newlines mixed)
_MULTI_WHITESPACE_RE = re.compile(r"[ \t]{2,}")
_MULTI_NEWLINE_RE = re.compile(r"\n{3,}")
# Tab-separated TOC-style lines: "6.1.2\tLife expectancy\t\t56"
_TOC_LINE_RE = re.compile(r"^\d+(?:\.\d+)*(?:\.[a-z])?\t.*\t+\d+\s*$", re.MULTILINE)


def clean_text(text: str) -> str:
    """Clean extracted PDF text using common heuristics.

    - Strip repeated page headers and bare page numbers
    - Remove table-of-contents lines (tab-separated with page numbers)
    - Normalize unicode whitespace (thin spaces, non-breaking spaces)
    - Collapse runs of tabs/spaces into a single space
    - Collapse 3+ consecutive newlines into 2
    - Strip leading/trailing whitespace per line
    """
    # Remove page headers and page numbers
    text = _PAGE_HEADER_RE.sub("", text)
    text = _PAGE_NUMBER_RE.sub("", text)

    # Remove TOC lines
    text = _TOC_LINE_RE.sub("", text)

    # Normalize unicode whitespace
    text = text.replace("\u2009", " ")  # thin space
    text = text.replace("\u00a0", " ")  # non-breaking space
    text = text.replace("\u200b", "")  # zero-width space
    text = text.replace("\ufeff", "")  # BOM

    # Replace tabs with spaces
    text = text.replace("\t", " ")

    # Collapse multiple spaces into one
    text = _MULTI_WHITESPACE_RE.sub(" ", text)

    # Strip each line, remove blank lines
    lines = [line.strip() for line in text.split("\n")]
    text = "\n".join(line for line in lines if line)

    # Collapse excessive newlines
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)

    return text.strip()


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------

SECTION_RE = re.compile(r"^(\d+(?:\.\d+)*)\s+(.+)", re.MULTILINE)


def detect_section(text: str, offset: int, section_headers: list[tuple[int, str]]) -> str:
    """Find the most recent section header before the given offset."""
    current = ""
    for pos, header in section_headers:
        if pos <= offset:
            current = header
        else:
            break
    return current


def chunk_text(pages: list[dict]) -> list[dict]:
    """Chunk extracted pages into overlapping segments with section metadata."""
    # Build a single text with page markers
    full_text = ""
    page_offsets: list[tuple[int, int]] = []  # (start_offset, page_number)
    for p in pages:
        page_offsets.append((len(full_text), p["page"]))
        full_text += p["text"] + "\n"

    # Detect section headers
    section_headers: list[tuple[int, str]] = []
    for match in SECTION_RE.finditer(full_text):
        header = f"{match.group(1)} {match.group(2).strip()}"
        section_headers.append((match.start(), header))
    log.info("Detected %d section headers", len(section_headers))

    # Sliding window with smart break points
    chunks = []
    pos = 0
    while pos < len(full_text):
        end = pos + CHUNK_SIZE

        # Try to break at a paragraph boundary
        if end < len(full_text):
            para_break = full_text.rfind("\n\n", pos + CHUNK_SIZE // 2, end + 100)
            if para_break > pos:
                end = para_break
            else:
                # Try sentence boundary
                sent_break = full_text.rfind(". ", pos + CHUNK_SIZE // 2, end + 50)
                if sent_break > pos:
                    end = sent_break + 1

        chunk_text_str = full_text[pos:end].strip()
        if len(chunk_text_str) < 50:  # skip near-empty chunks
            pos = end
            continue

        # Determine page number and section
        page_num = 1
        for offset, pnum in reversed(page_offsets):
            if offset <= pos:
                page_num = pnum
                break

        section = detect_section(full_text, pos, section_headers)

        chunks.append(
            {
                "text": chunk_text_str,
                "page": page_num,
                "section": section,
            }
        )

        # Advance with overlap
        pos = max(pos + 1, end - CHUNK_OVERLAP)

    log.info("Created %d chunks (size=%d, overlap=%d)", len(chunks), CHUNK_SIZE, CHUNK_OVERLAP)
    return chunks


# ---------------------------------------------------------------------------
# Embedding + ChromaDB
# ---------------------------------------------------------------------------


def build_database(
    chunks: list[dict],
    db_path: Path,
    model_save_path: Path,
) -> None:
    """Embed chunks and persist to ChromaDB."""
    from sentence_transformers import SentenceTransformer

    log.info("Loading embedding model: %s", MODEL_ID)
    model = SentenceTransformer(MODEL_ID)

    # Compute document embeddings
    texts = [c["text"] for c in chunks]
    log.info("Encoding %d chunks...", len(texts))
    embeddings = model.encode_document(texts, show_progress_bar=True)
    log.info("Embedding shape: %s", embeddings.shape)

    # Persist to ChromaDB
    if db_path.exists():
        shutil.rmtree(db_path)
    db_path.mkdir(parents=True)

    import chromadb

    client = chromadb.PersistentClient(path=str(db_path))
    collection = client.create_collection(name=COLLECTION_NAME)

    # ChromaDB has a batch limit; add in chunks of 5000
    batch_size = 5000
    for i in range(0, len(texts), batch_size):
        end = min(i + batch_size, len(texts))
        collection.add(
            ids=[f"chunk-{j:04d}" for j in range(i, end)],
            documents=texts[i:end],
            embeddings=embeddings[i:end].tolist(),
            metadatas=[{"page": c["page"], "section": c["section"]} for c in chunks[i:end]],
        )

    log.info("Persisted %d chunks to %s", collection.count(), db_path)

    # Save embedding model for runtime use
    log.info("Saving embedding model to %s", model_save_path)
    model.save(str(model_save_path))


# ---------------------------------------------------------------------------
# External normalized JSONL ingestion (--input/--rebuild mode)
# ---------------------------------------------------------------------------

# Control chars (e.g. \u0007 BEL from PDF extraction) except \n and \t
_CTRL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_entry_text(text: str) -> str:
    """Normalize external entry text: strip control chars, tidy whitespace."""
    text = _CTRL_CHARS_RE.sub("", text)
    text = text.replace("\u00a0", " ").replace("\u200b", "")
    text = text.replace("\t", " ")
    text = _MULTI_WHITESPACE_RE.sub(" ", text)
    text = _MULTI_NEWLINE_RE.sub("\n\n", text)
    return text.strip()


def load_jsonl_entries(path: Path) -> list[dict]:
    """Load normalized guideline entries from JSONL."""
    entries = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            e = json.loads(line)
            e["text"] = clean_entry_text(e.get("text", ""))
            if e["text"]:
                entries.append(e)
    log.info("Loaded %d entries from %s", len(entries), path)
    return entries


def chunk_entries(entries: list[dict]) -> list[dict]:
    """Chunk normalized entries with a sliding window, keeping section metadata.

    Each entry carries its own ``source`` / ``section_id`` / ``section_title`` /
    ``page``; windows never cross entry boundaries so metadata stays accurate.
    """
    chunks = []
    for e in entries:
        text = e["text"]
        section_id = str(e.get("section_id") or "").strip()
        section_title = str(e.get("section_title") or "").strip()
        section = f"{section_id} {section_title}".strip()
        source = str(e.get("source") or "").strip() or "UNKNOWN"
        page = e.get("page")

        pos = 0
        while pos < len(text):
            end = min(pos + CHUNK_SIZE, len(text))
            if end < len(text):
                para_break = text.rfind("\n\n", pos + CHUNK_SIZE // 2, end + 100)
                if para_break > pos:
                    end = para_break
                else:
                    sent_break = text.rfind(". ", pos + CHUNK_SIZE // 2, end + 50)
                    if sent_break > pos:
                        end = sent_break + 1
            seg = text[pos:end].strip()
            if len(seg) >= 50:
                chunks.append({"text": seg, "page": page, "section": section, "source": source})
            pos = max(pos + 1, end - CHUNK_OVERLAP) if end < len(text) else end

    log.info("Created %d chunks from %d entries", len(chunks), len(entries))
    return chunks


def build_database_augmented(
    existing_db_path: Path,
    new_chunks: list[dict],
    db_path: Path,
    model_save_path: Path,
) -> None:
    """Merge existing DB chunks with new chunks, re-embed everything, persist."""
    import chromadb
    from sentence_transformers import SentenceTransformer

    # Load existing chunks from the current DB
    existing_chunks: list[dict] = []
    if existing_db_path.exists():
        from chromadb.api.shared_system_client import SharedSystemClient

        client = chromadb.PersistentClient(path=str(existing_db_path))
        col = client.get_collection(COLLECTION_NAME)
        data = col.get(include=["documents", "metadatas"])
        for doc, meta in zip(data["documents"], data["metadatas"]):
            existing_chunks.append(
                {
                    "text": doc,
                    "page": meta.get("page"),
                    "section": meta.get("section", ""),
                    "source": meta.get("source", "EAU"),
                }
            )
        log.info("Keeping %d existing chunks from %s", len(existing_chunks), existing_db_path)
        # Release sqlite handles before the DB dir gets replaced later
        del col, client, data
        SharedSystemClient.clear_system_cache()

    all_chunks = existing_chunks + new_chunks
    log.info("Total chunks after merge: %d", len(all_chunks))

    # Prefer the locally saved embedding model (offline-safe)
    if model_save_path.exists():
        model_id = str(model_save_path)
    else:
        model_id = MODEL_ID
    log.info("Loading embedding model: %s", model_id)
    model = SentenceTransformer(model_id)

    texts = [c["text"] for c in all_chunks]
    log.info("Encoding %d chunks...", len(texts))
    embeddings = model.encode_document(texts, show_progress_bar=True)
    log.info("Embedding shape: %s", embeddings.shape)

    # Persist to a fresh ChromaDB (same path as the old DB: clear the
    # in-process client cache so the new PersistentClient sees the new dir)
    from chromadb.api.shared_system_client import SharedSystemClient

    if db_path.exists():
        shutil.rmtree(db_path)
    db_path.mkdir(parents=True)
    SharedSystemClient.clear_system_cache()

    client = chromadb.PersistentClient(path=str(db_path))
    collection = client.create_collection(name=COLLECTION_NAME)

    n_existing = len(existing_chunks)
    batch_size = 5000
    for i in range(0, len(texts), batch_size):
        end = min(i + batch_size, len(texts))
        ids = [
            f"chunk-{j:04d}" if j < n_existing else f"unified-{j - n_existing:04d}"
            for j in range(i, end)
        ]
        collection.add(
            ids=ids,
            documents=texts[i:end],
            embeddings=embeddings[i:end].tolist(),
            # Chroma 1.x metadata values must not be None — omit such keys
            metadatas=[
                {k: v for k, v in (("page", c["page"]), ("section", c["section"]), ("source", c["source"])) if v is not None}
                for c in all_chunks[i:end]
            ],
        )

    log.info("Persisted %d chunks to %s", collection.count(), db_path)

    # Refresh the saved embedding model copy
    log.info("Saving embedding model to %s", model_save_path)
    model.save(str(model_save_path))


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(description="Process guidelines PDF into ChromaDB")
    parser.add_argument(
        "--pdf",
        default="docs/internal/guidelines.pdf",
        help="Path to the guidelines PDF",
    )
    parser.add_argument(
        "--input",
        default=None,
        help="Normalized guidelines JSONL to merge into the DB (with --rebuild)",
    )
    parser.add_argument(
        "--rebuild",
        action="store_true",
        help="Merge existing DB chunks with --input JSONL entries and re-embed",
    )
    parser.add_argument(
        "--output-dir",
        default="resources",
        help="Output directory for guidelines_db/ and embedding_model/",
    )
    args = parser.parse_args()

    output_dir = Path(args.output_dir)

    if args.rebuild:
        if not args.input:
            parser.error("--rebuild requires --input <jsonl>")
        input_path = Path(args.input)
        if not input_path.exists():
            raise FileNotFoundError(f"Input JSONL not found: {input_path}")

        entries = load_jsonl_entries(input_path)
        new_chunks = chunk_entries(entries)
        build_database_augmented(
            existing_db_path=output_dir / "guidelines_db",
            new_chunks=new_chunks,
            db_path=output_dir / "guidelines_db",
            model_save_path=output_dir / "embedding_model",
        )
        log.info("Done. Run 'make gc-build' to bake into the container.")
        return

    pdf_path = args.pdf
    if not Path(pdf_path).exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    # Extract and chunk
    pages = extract_pages(pdf_path)
    chunks = chunk_text(pages)

    # Embed and persist
    build_database(
        chunks,
        db_path=output_dir / "guidelines_db",
        model_save_path=output_dir / "embedding_model",
    )

    log.info("Done. Run 'make gc-build' to bake into the container.")


if __name__ == "__main__":
    main()
