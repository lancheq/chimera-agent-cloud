"""Process the EAU guidelines PDF into a ChromaDB collection.

Extracts text from the PDF, chunks it with section awareness, computes
embeddings with google/embeddinggemma-300m, and persists the result to
``resources/guidelines_db/``.  Also saves the embedding model to
``resources/embedding_model/`` for use at runtime.

Run once before building the Docker image::

    python scripts/process_guidelines.py
    # or
    make process-guidelines
"""

import argparse
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
        "--output-dir",
        default="resources",
        help="Output directory for guidelines_db/ and embedding_model/",
    )
    args = parser.parse_args()

    pdf_path = args.pdf
    output_dir = Path(args.output_dir)

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
