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

from chimera_agent_baseline.runtime_audit import component_event

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
        env = dict(os.environ)
        # Local snapshot only: hub probes hang for minutes on network-blackholed
        # hosts (d208 2026-10-26: 15s offline vs >400s hang). GC container is offline too.
        env.setdefault("HF_HUB_OFFLINE", "1")
        env.setdefault("TRANSFORMERS_OFFLINE", "1")
        self._proc = subprocess.Popen(
            [sys.executable, "-u", "-c", _EMBED_SERVER_CODE, model_path, str(SOCKET_PATH)],
            stdout=subprocess.PIPE,
            text=True,
            env=env,
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
    # QA dense relevance floor: gemma-300m cosines have a high baseline; hits below
    # this are treated as no-match (calibrated on qa_channel_eval 2026-10-26).
    QA_DENSE_FLOOR = 0.30
    # Rerank cost is linear in candidate count; top-30 of RRF covers >95% of gold
    # sections (eval 2026-10-25: rr_fixed on top-30 matched or beat full-list rerank).
    RERANK_CANDIDATES = 30
    DENSE_WEIGHT = 0.8  # Dense retriever weight in RRF
    BM25_WEIGHT = 0.2  # BM25 retriever weight in RRF

    def __init__(self, resource_dir: str | Path, reranker_dir: str | Path | None = None):
        resource_dir = Path(resource_dir)
        # Reranker may be provided independently (e.g. under /opt/ml/model),
        # separate from the image's resource_dir (guidelines DB).
        reranker_dir = Path(reranker_dir) if reranker_dir is not None else None
        if reranker_dir is not None and not reranker_dir.exists():
            _fallback = resource_dir / "reranker_model"
            if _fallback.exists():
                log.warning(
                    "reranker_dir %s not found - falling back to %s "
                    "(run.py passes model_dir/reranker_model; model ships under resources/)",
                    reranker_dir, _fallback,
                )
                reranker_dir = _fallback
        if reranker_dir is None:
            reranker_dir = resource_dir
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

        # Load QA library (normalised) + dense vectors if precomputed, else BM25 once
        self._qa = self._load_guideline_qa(resource_dir)
        log.info("Loaded %d QA pairs", len(self._qa))
        self._qa_vecs = self._load_qa_embeddings(resource_dir)
        if self._qa_vecs is None and self._qa:
            self._qa_bm25 = _build_bm25([_tokenize(q["question"]) for q in self._qa])
        else:
            self._qa_bm25 = None

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
        """Load section-grounded QA pairs from JSONL (normalises old and new formats)."""
        for path in [
            resource_dir / "guideline_qa.jsonl",
            resource_dir / "guideline_dataset" / "qa" / "guideline_qa.jsonl",
        ]:
            if path.exists():
                qa = []
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        rec = json.loads(line)
                        g0 = (rec.get("grounding") or [{}])[0]
                        sec_id = str(g0.get("section_id", ""))
                        if not sec_id and rec.get("guideline_id"):
                            sec_id = str(rec["guideline_id"]).split("-", 1)[-1]
                        qa.append(
                            {
                                "question": rec.get("question", ""),
                                "answer": rec.get("answer", ""),
                                "section_id": sec_id,
                                "section_title": g0.get("section_title", rec.get("section", "")),
                                "source": g0.get("source", ""),
                                "support_quote": rec.get("support_quote", ""),
                                "confidence": rec.get("confidence", ""),
                                "task": rec.get("task", ""),
                            }
                        )
                return qa
        log.info("No guideline_qa.jsonl found — QA search disabled")
        return []

    def _load_qa_embeddings(self, resource_dir: Path):
        """Load precomputed QA question embeddings; None means use BM25 fallback."""
        path = resource_dir / "guideline_qa.embeddings.npy"
        if not path.exists() or not self._qa:
            component_event("qa_embedding_load", configured=bool(self._qa),
                            status="fallback" if self._qa else "disabled",
                            reason="QA embedding file missing" if self._qa else "QA library absent",
                            fallback="bm25", required=bool(self._qa), resource=str(path))
            return None
        try:
            import numpy as np

            vecs = np.load(str(path))
            if vecs.shape[0] != len(self._qa):
                component_event("qa_embedding_load", configured=True, status="fallback",
                                reason=f"{vecs.shape[0]} vectors for {len(self._qa)} QA entries",
                                fallback="bm25", required=True, resource=str(path))
                # A-7（2026-10-28）：行数不符意味着「换了 jsonl 但没重建 npy」。
                # 默认仍退 BM25（保持既有行为），但 CHIMERA_QA_STRICT=1 时直接抛错：
                # 这种静默降级会让两臂的检索机制被换掉而日志只留一行 warning，
                # 是 A/B 不可比的隐蔽来源。
                if os.environ.get("CHIMERA_QA_STRICT", "0").strip().lower() in (
                    "1", "true", "on", "yes",
                ):
                    raise RuntimeError(
                        f"QA embeddings stale: {vecs.shape[0]} vecs vs {len(self._qa)} QA items "
                        f"({path}). Rebuild embeddings, or set CHIMERA_QA_STRICT=0 to fall back to BM25."
                    )
                log.warning(
                    "QA embeddings stale (%d vecs vs %d QA items) — using BM25",
                    vecs.shape[0], len(self._qa),
                )
                return None
            log.info("Loaded QA dense embeddings: %s", vecs.shape)
            component_event("qa_embedding_load", configured=True, status="executed", resource=str(path))
            return vecs
        except RuntimeError:
            raise
        except Exception as exc:
            component_event("qa_embedding_load", configured=True, status="fallback", reason=str(exc),
                            fallback="bm25", required=True, resource=str(path))
            log.warning("Failed to load QA embeddings — using BM25", exc_info=True)
            return None

    def _load_reranker(self, model_path: Path):
        """Load cross-encoder reranker (optional, CPU-only)."""
        # B3 消融开关（2026-10-27）：CHIMERA_RERANKER=off 时跳过加载，
        # 检索退回 RRF 分数；默认 on，不影响既有行为。
        if os.environ.get("CHIMERA_RERANKER", "on").strip().lower() in ("off", "0", "false", "no"):
            component_event("reranker_load", configured=False, status="disabled", fallback="rrf")
            log.info("reranker disabled by CHIMERA_RERANKER — using RRF score fallback")
            return None
        if not model_path.exists():
            component_event("reranker_load", configured=True, status="fallback", reason="Model path missing",
                            fallback="rrf", required=True, resource=str(model_path))
            log.info("No reranker model at %s — using RRF score fallback", model_path)
            return None
        try:
            from transformers import AutoModelForSequenceClassification, AutoTokenizer

            tokenizer = AutoTokenizer.from_pretrained(str(model_path))
            model = AutoModelForSequenceClassification.from_pretrained(str(model_path))
            model.eval()
            component_event("reranker_load", configured=True, status="executed", resource=str(model_path))
            log.info("Loaded reranker model from %s (CPU)", model_path)
            return {"tokenizer": tokenizer, "model": model}
        except Exception as exc:
            component_event("reranker_load", configured=True, status="fallback", reason=str(exc),
                            fallback="rrf", required=True, resource=str(model_path))
            log.warning("Failed to load reranker model — falling back to RRF", exc_info=True)
            return None

    # -- Search pipeline ----------------------------------------------------

    def _encode_query(self, text: str) -> list[float]:
        """Default socket encoder; guarded snapshots bind their own encoder."""
        return _embed_query(text)

    def _requires_embedding_socket(self) -> bool:
        return True

    def _encoder_resource(self) -> str:
        return str(SOCKET_PATH)

    def query(self, query_text: str, top_k: int = DEFAULT_TOP_K) -> list[dict]:
        """Search guidelines using hybrid retrieval + reranking + QA."""
        # 1) Dense recall top-20 + BM25 recall top-20 -> merge via RRF
        dense_hits = self._dense_search(query_text, 20)
        bm25_hits = self._bm25_search(query_text, 20)
        candidates = self._merge_rrf(dense_hits, bm25_hits)

        # 2) Rerank (cross-encoder if available, else RRF score)
        ranked = self._rerank(query_text, candidates[:self.RERANK_CANDIDATES])

        # 3) QA hits (section-grounded)
        qa_hits = self._qa_search(query_text, 3)

        # 4) Format
        return self._format(ranked[:top_k], qa_hits)

    def _dense_search(self, query_text: str, k: int) -> list[dict]:
        """Dense vector search via embedding service + ChromaDB."""
        if self._requires_embedding_socket() and not SOCKET_PATH.exists():
            component_event("dense_retrieval", configured=True, status="fallback", reason="Embedding socket missing",
                            fallback="bm25", required=True, resource=str(SOCKET_PATH))
            return []
        try:
            query_embedding = self._encode_query(query_text)
        except Exception as exc:
            component_event("dense_retrieval", configured=True, status="fallback", reason=str(exc),
                            fallback="bm25", required=True)
            log.warning("Dense search failed - embedding service error", exc_info=True)
            return []

        results = self._collection.query(query_embeddings=[query_embedding], n_results=k)
        component_event("dense_retrieval", configured=True, status="executed", resource=self._encoder_resource())
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

            pairs = [(query_text, c["text"]) for c in candidates]
            inputs = tokenizer(
                pairs, padding=True, truncation=True, return_tensors="pt", max_length=512
            )
            with torch.no_grad():
                scores = model(**inputs).logits.squeeze(-1).tolist()

            for i, c in enumerate(candidates):
                c["rerank_score"] = round(float(scores[i]), 4)

            candidates.sort(key=lambda c: -c["rerank_score"])
            return candidates
        except Exception as exc:
            component_event("reranker_inference", configured=True, status="fallback", reason=str(exc),
                            fallback="rrf", required=True)
            log.warning("Reranker inference failed - using RRF score", exc_info=True)
            for c in candidates:
                c["rerank_score"] = c.get("rrf_score", 0)
            return candidates

    def _qa_search(self, query_text: str, k: int) -> list[dict]:
        """Search QA library: dense cosine if embeddings available, else BM25."""
        if not self._qa:
            return []

        scored: list[tuple[int, float]] = []
        if self._qa_vecs is not None:
            try:
                import numpy as np

                qv = np.array(self._encode_query(query_text))
                sims = (self._qa_vecs @ qv) / (
                    np.linalg.norm(self._qa_vecs, axis=1) * (np.linalg.norm(qv) or 1.0)
                )
                order = np.argsort(-sims)[:k]
                scored = [(int(i), float(sims[i])) for i in order if sims[i] > self.QA_DENSE_FLOOR]
            except Exception as exc:
                component_event("qa_dense_retrieval", configured=True, status="fallback", reason=str(exc),
                                fallback="bm25", required=True)
                log.warning("QA dense search failed — falling back to BM25", exc_info=True)
                scored = []
        if not scored and self._qa_bm25 is not None:
            scores = self._qa_bm25.get_scores(_tokenize(query_text))
            ranked = sorted(range(len(scores)), key=lambda i: -scores[i])[:k]
            scored = [(i, float(scores[i])) for i in ranked if scores[i] > 0]

        hits = []
        for idx, score in scored:
            q = self._qa[idx]
            head = f"[{q['source']} {q['section_id']}]".strip()
            if q.get("section_title"):
                head += f" {q['section_title']}"
            if q.get("support_quote"):
                body = f"{head}\n{q['support_quote']}"
            else:
                body = f"Q: {q['question']}\nA: {q['answer']}"
            hits.append(
                {
                    "text": body,
                    "page": None,
                    "section": q.get("section_id", ""),
                    "score": round(score, 4),
                    "type": "qa",
                    "guideline_id": f"{q['source']}-{q['section_id']}" if q.get("section_id") else "",
                    "evidence_level": "",
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
