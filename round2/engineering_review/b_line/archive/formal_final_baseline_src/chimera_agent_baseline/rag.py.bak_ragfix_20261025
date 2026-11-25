"""Guidelines retrieval via ChromaDB + embeddinggemma-300m.

Hybrid retrieval: dense (ChromaDB) + sparse (BM25) with RRF fusion,
optional cross-encoder reranking, and section-grounded QA library.

Architecture::

    Main process (inference.py / run.py)
        └── EmbeddingService (subprocess, loads model once)
                └── listens on /tmp/chimera_embed.sock

    MCP server (short-lived per tool call)
        └── GuidelinesSearch.query()
                └── socket connect → encode query → ChromaDB dense search
                    + BM25 sparse search → RRF fusion → rerank
                    + QA library section-grounded hits
"""

import json
import logging
import math
import os
import re
import socket
import subprocess
import sys
from pathlib import Path

log = logging.getLogger(__name__)

COLLECTION_NAME = "guidelines"
DEFAULT_TOP_K = 5
SOCKET_PATH = Path(os.environ.get("CHIMERA_EMBED_SOCKET", "/tmp/chimera_embed.sock"))

# ---------------------------------------------------------------------------
# Embedding service — started once by the main process
# ---------------------------------------------------------------------------

_EMBED_SERVER_CODE = """\
import json, socket, sys, logging
logging.disable(logging.CRITICAL)

from sentence_transformers import SentenceTransformer
model = SentenceTransformer(sys.argv[1], device="cpu")

sock_path = sys.argv[2]
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
sock.bind(sock_path)
sock.listen(8)
print("ready", flush=True)

while True:
    conn, _ = sock.accept()
    try:
        data = b""
        while True:
            chunk = conn.recv(4096)
            if not chunk:
                break
            data += chunk
        query = json.loads(data.decode())
        embedding = model.encode_query(query).tolist()
        conn.sendall(json.dumps(embedding).encode())
    except Exception:
        pass
    finally:
        conn.close()
"""


class EmbeddingService:
    """Long-lived subprocess serving query embeddings over a Unix socket."""

    def __init__(self, model_path: str):
        log.info("Starting embedding service (model: %s)", model_path)
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _EMBED_SERVER_CODE, model_path, str(SOCKET_PATH)],
            stdout=subprocess.PIPE,
            text=True,
        )
        line = self._proc.stdout.readline().strip()
        if line != "ready":
            raise RuntimeError(f"Embedding service failed to start: {line}")
        log.info("Embedding service ready at %s", SOCKET_PATH)

    def stop(self):
        if self._proc.poll() is None:
            self._proc.terminate()
            self._proc.wait(timeout=5)
        if SOCKET_PATH.exists():
            SOCKET_PATH.unlink()


def start_embedding_service(embedding_model_dir: str | Path) -> EmbeddingService | None:
    """Start the embedding service if the embedding model is available."""
    model_path = Path(embedding_model_dir)
    if not model_path.exists():
        log.info("No embedding model at %s — skipping embedding service", model_path)
        return None
    return EmbeddingService(str(model_path))


# ---------------------------------------------------------------------------
# Socket client — used by GuidelinesSearch inside MCP subprocesses
# ---------------------------------------------------------------------------


def _embed_query(text: str) -> list[float]:
    """Encode a query via the embedding service socket."""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(str(SOCKET_PATH))
    sock.sendall(json.dumps(text).encode())
    sock.shutdown(socket.SHUT_WR)
    data = b""
    while True:
        chunk = sock.recv(65536)
        if not chunk:
            break
        data += chunk
    sock.close()
    return json.loads(data.decode())


# ---------------------------------------------------------------------------
# Tokenizer + BM25 (uses rank_bm25 if installed, built-in fallback otherwise)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"[a-z0-9]+|[一-鿿]")


def _tokenize(text: str) -> list[str]:
    """Tokenize text for BM25. Splits on non-word chars, lowercases."""
    return _TOKEN_RE.findall(text.lower())


class _BM25Okapi:
    """Minimal BM25Okapi fallback when rank_bm25 is not installed."""

    def __init__(self, corpus, k1=1.5, b=0.75):
        self.k1 = k1
        self.b = b
        self.corpus = corpus
        n = len(corpus)
        self.doc_len = [len(doc) for doc in corpus]
        self.avgdl = sum(self.doc_len) / n if n > 0 else 0
        self.tf = []
        df = {}
        for doc in corpus:
            freq = {}
            for term in doc:
                freq[term] = freq.get(term, 0) + 1
            self.tf.append(freq)
            for term in freq:
                df[term] = df.get(term, 0) + 1
        self.idf = {}
        for term, freq in df.items():
            self.idf[term] = math.log((n - freq + 0.5) / (freq + 0.5) + 1)

    def get_scores(self, query):
        scores = [0.0] * len(self.corpus)
        for i, freq in enumerate(self.tf):
            dl = self.doc_len[i] or 1
            for term in query:
                if term not in freq:
                    continue
                idf = self.idf.get(term, 0)
                f = freq[term]
                scores[i] += idf * (f * (self.k1 + 1)) / (
                    f + self.k1 * (1 - self.b + self.b * dl / (self.avgdl or 1))
                )
        return scores

    def get_top_n(self, query, documents, n=5):
        scores = self.get_scores(query)
        top_indices = sorted(range(len(scores)), key=lambda i: -scores[i])[:n]
        return [documents[i] for i in top_indices]


def _build_bm25(corpus):
    """Build BM25 index, preferring rank_bm25, falling back to built-in."""
    try:
        from rank_bm25 import BM25Okapi
        return BM25Okapi(corpus)
    except ImportError:
        log.info("rank_bm25 not installed — using built-in BM25 fallback")
        return _BM25Okapi(corpus)


# ---------------------------------------------------------------------------
# Guidelines search — used by the MCP server
# ---------------------------------------------------------------------------


class GuidelinesSearch:
    """Search over a pre-built ChromaDB collection of guideline chunks.

    Hybrid retrieval: dense (ChromaDB) + sparse (BM25) with RRF fusion,
    optional cross-encoder reranking, and section-grounded QA library.

    Does NOT load the embedding model. Connects to the embedding
    service socket for query encoding — fast startup.
    """

    RRF_K = 60  # Reciprocal Rank Fusion constant
    DENSE_WEIGHT = 0.8  # Dense retriever weight in RRF
    BM25_WEIGHT = 0.2  # BM25 retriever weight in RRF

    def __init__(self, resource_dir: str | Path, reranker_dir: str | Path | None = None):
        resource_dir = Path(resource_dir)
        # Reranker may be provided independently (e.g. under /opt/ml/model),
        # separate from the image's resource_dir (guidelines DB).
        reranker_dir = Path(reranker_dir) if reranker_dir is not None else resource_dir
        db_path = resource_dir / "guidelines_db"

        if not db_path.exists():
            raise FileNotFoundError(
                f"Guidelines DB not found at {db_path}. Run: python scripts/process_guidelines.py"
            )

        import chromadb

        self._client = chromadb.PersistentClient(path=str(db_path))
        self._collection = self._client.get_collection(COLLECTION_NAME)
        log.info("Loaded guidelines collection: %d chunks", self._collection.count())

        # Build BM25 index from all chunks
        self._chunks = self._load_chunks()
        self._bm25 = _build_bm25([c["tokens"] for c in self._chunks])
        log.info("Built BM25 index over %d chunks", len(self._chunks))

        # Load QA library
        self._qa = self._load_guideline_qa(resource_dir)
        log.info("Loaded %d QA pairs", len(self._qa))

        # Load reranker (optional, CPU-only)
        self._reranker = self._load_reranker(reranker_dir)

    # -- Indexing / loading -------------------------------------------------

    def _load_chunks(self) -> list[dict]:
        """Load all chunks from ChromaDB for BM25 indexing."""
        all_data = self._collection.get(include=["documents", "metadatas"])
        chunks = []
        for doc_id, doc, meta in zip(
            all_data["ids"], all_data["documents"], all_data["metadatas"]
        ):
            chunks.append(
                {
                    "id": doc_id,
                    "text": doc,
                    "page": meta.get("page"),
                    "section": meta.get("section", ""),
                    "tokens": _tokenize(doc),
                }
            )
        return chunks

    def _load_guideline_qa(self, resource_dir: Path) -> list[dict]:
        """Load section-grounded QA pairs from JSONL."""
        for path in [
            resource_dir / "guideline_qa.jsonl",
            resource_dir / "guideline_dataset" / "qa" / "guideline_qa.jsonl",
        ]:
            if path.exists():
                qa = []
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            qa.append(json.loads(line))
                return qa
        log.info("No guideline_qa.jsonl found — QA search disabled")
        return []

    def _load_reranker(self, model_path: Path):
        """Load cross-encoder reranker (optional, CPU-only)."""
        if not model_path.exists():
            log.info("No reranker model at %s — using RRF score fallback", model_path)
            return None
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(str(model_path))
            model = AutoModelForSequenceClassification.from_pretrained(str(model_path))
            model.eval()
            log.info("Loaded reranker model from %s (CPU)", model_path)
            return {"tokenizer": tokenizer, "model": model}
        except Exception:
            log.warning("Failed to load reranker model — falling back to RRF", exc_info=True)
            return None

    # -- Search pipeline ----------------------------------------------------

    def query(self, query_text: str, top_k: int = DEFAULT_TOP_K) -> list[dict]:
        """Search guidelines using hybrid retrieval + reranking + QA."""
        # 1) Dense recall top-20 + BM25 recall top-20 -> merge via RRF
        dense_hits = self._dense_search(query_text, 20)
        bm25_hits = self._bm25_search(query_text, 20)
        candidates = self._merge_rrf(dense_hits, bm25_hits)

        # 2) Rerank (cross-encoder if available, else RRF score)
        ranked = self._rerank(query_text, candidates)

        # 3) QA hits (section-grounded)
        qa_hits = self._qa_search(query_text, 3)

        # 4) Format
        return self._format(ranked[:top_k], qa_hits)

    def _dense_search(self, query_text: str, k: int) -> list[dict]:
        """Dense vector search via embedding service + ChromaDB."""
        if not SOCKET_PATH.exists():
            return []
        try:
            query_embedding = _embed_query(query_text)
        except Exception:
            log.warning("Dense search failed - embedding service error", exc_info=True)
            return []

        results = self._collection.query(query_embeddings=[query_embedding], n_results=k)
        hits = []
        for i, doc in enumerate(results["documents"][0]):
            meta = results["metadatas"][0][i] if results["metadatas"] else {}
            distance = results["distances"][0][i] if results["distances"] else None
            hits.append(
                {
                    "id": results["ids"][0][i],
                    "text": doc,
                    "page": meta.get("page"),
                    "section": meta.get("section", ""),
                    "dense_score": round(1 - distance, 4) if distance is not None else None,
                    "bm25_score": None,
                }
            )
        return hits

    def _bm25_search(self, query_text: str, k: int) -> list[dict]:
        """BM25 sparse search over guideline chunks."""
        tokens = _tokenize(query_text)
        if not tokens:
            return []
        scores = self._bm25.get_scores(tokens)
        ranked_indices = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
        hits = []
        for idx in ranked_indices:
            if scores[idx] <= 0:
                continue
            chunk = self._chunks[idx]
            hits.append(
                {
                    "id": chunk["id"],
                    "text": chunk["text"],
                    "page": chunk["page"],
                    "section": chunk["section"],
                    "dense_score": None,
                    "bm25_score": round(scores[idx], 4),
                }
            )
        return hits

    def _merge_rrf(self, dense_hits: list[dict], bm25_hits: list[dict]) -> list[dict]:
        """Merge dense and BM25 hits via Reciprocal Rank Fusion."""
        rrf_scores: dict[str, float] = {}
        candidate_map: dict[str, dict] = {}

        for rank, hit in enumerate(dense_hits):
            cid = hit["id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0) + self.DENSE_WEIGHT / (self.RRF_K + rank + 1)
            if cid not in candidate_map:
                candidate_map[cid] = hit.copy()

        for rank, hit in enumerate(bm25_hits):
            cid = hit["id"]
            rrf_scores[cid] = rrf_scores.get(cid, 0) + self.BM25_WEIGHT / (self.RRF_K + rank + 1)
            if cid not in candidate_map:
                candidate_map[cid] = hit.copy()
            else:
                if candidate_map[cid]["dense_score"] is None:
                    candidate_map[cid]["dense_score"] = hit["dense_score"]
                if candidate_map[cid]["bm25_score"] is None:
                    candidate_map[cid]["bm25_score"] = hit["bm25_score"]

        sorted_ids = sorted(rrf_scores.keys(), key=lambda cid: -rrf_scores[cid])
        result = []
        for cid in sorted_ids:
            candidate = candidate_map[cid].copy()
            candidate["rrf_score"] = round(rrf_scores[cid], 6)
            result.append(candidate)
        return result

    def _rerank(self, query_text: str, candidates: list[dict]) -> list[dict]:
        """Rerank candidates. Uses cross-encoder if available, else RRF score."""
        if not candidates:
            return []

        if self._reranker is None:
            for c in candidates:
                c["rerank_score"] = c.get("rrf_score", 0)
            return candidates

        try:
            import torch

            tokenizer = self._reranker["tokenizer"]
            model = self._reranker["model"]

            pairs = [(query_text, c["text"][:512]) for c in candidates]
            inputs = tokenizer(
                pairs, padding=True, truncation=True, return_tensors="pt", max_length=512
            )
            with torch.no_grad():
                scores = model(**inputs).logits.squeeze(-1).tolist()

            for i, c in enumerate(candidates):
                c["rerank_score"] = round(float(scores[i]), 4)

            candidates.sort(key=lambda c: -c["rerank_score"])
            return candidates
        except Exception:
            log.warning("Reranker inference failed - using RRF score", exc_info=True)
            for c in candidates:
                c["rerank_score"] = c.get("rrf_score", 0)
            return candidates

    def _qa_search(self, query_text: str, k: int) -> list[dict]:
        """Search QA library by BM25 over questions."""
        if not self._qa:
            return []

        qa_tokens = [_tokenize(q["question"]) for q in self._qa]
        qa_bm25 = _build_bm25(qa_tokens)

        query_tokens = _tokenize(query_text)
        scores = qa_bm25.get_scores(query_tokens)
        ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]

        hits = []
        for idx in ranked:
            if scores[idx] <= 0:
                continue
            q = self._qa[idx]
            hits.append(
                {
                    "text": f"Q: {q['question']}\nA: {q['answer']}",
                    "page": None,
                    "section": q.get("section", ""),
                    "score": round(scores[idx], 4),
                    "type": "qa",
                    "guideline_id": q.get("guideline_id", ""),
                    "evidence_level": q.get("evidence_level", ""),
                }
            )
        return hits

    def _format(self, ranked_chunks: list[dict], qa_hits: list[dict]) -> list[dict]:
        """Format final results for MCP output."""
        results = []
        for c in ranked_chunks:
            results.append(
                {
                    "text": c["text"],
                    "page": c.get("page"),
                    "section": c.get("section"),
                    "score": c.get("rerank_score", c.get("rrf_score", 0)),
                    "type": "chunk",
                }
            )
        results.extend(qa_hits)
        return results
