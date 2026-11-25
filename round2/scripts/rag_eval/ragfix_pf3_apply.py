#!/usr/bin/env python
"""Apply PF3: QA channel dense matching + normalised loader + init-time BM25 fallback.

Prereq: new lib at resources/guideline_qa.jsonl (old backed up as .bak_183) and
        resources/guideline_qa.embeddings.npy precomputed (else falls back to BM25).

Run on the server:
  ~/miniconda3/envs/chimera/bin/python ragfix_pf3_apply.py
"""

import hashlib
import shutil
import sys
from pathlib import Path

RAG = Path("/home/hedongze/CHIMERA-agent/src/chimera_agent_baseline/rag.py")
BAK = RAG.with_suffix(".py.bak_ragfix_pf3_20261026")

OLD_INIT = """        # Load QA library
        self._qa = self._load_guideline_qa(resource_dir)
        log.info("Loaded %d QA pairs", len(self._qa))
"""

NEW_INIT = """        # Load QA library (normalised) + dense vectors if precomputed, else BM25 once
        self._qa = self._load_guideline_qa(resource_dir)
        log.info("Loaded %d QA pairs", len(self._qa))
        self._qa_vecs = self._load_qa_embeddings(resource_dir)
        if self._qa_vecs is None and self._qa:
            self._qa_bm25 = _build_bm25([_tokenize(q["question"]) for q in self._qa])
        else:
            self._qa_bm25 = None
"""

OLD_LOAD = """    def _load_guideline_qa(self, resource_dir: Path) -> list[dict]:
        \"\"\"Load section-grounded QA pairs from JSONL.\"\"\"
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
"""

NEW_LOAD = """    def _load_guideline_qa(self, resource_dir: Path) -> list[dict]:
        \"\"\"Load section-grounded QA pairs from JSONL (normalises old and new formats).\"\"\"
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
        \"\"\"Load precomputed QA question embeddings; None means use BM25 fallback.\"\"\"
        path = resource_dir / "guideline_qa.embeddings.npy"
        if not path.exists() or not self._qa:
            return None
        try:
            import numpy as np

            vecs = np.load(str(path))
            if vecs.shape[0] != len(self._qa):
                log.warning(
                    "QA embeddings stale (%d vecs vs %d QA items) — using BM25",
                    vecs.shape[0], len(self._qa),
                )
                return None
            log.info("Loaded QA dense embeddings: %s", vecs.shape)
            return vecs
        except Exception:
            log.warning("Failed to load QA embeddings — using BM25", exc_info=True)
            return None
"""

OLD_QA = """    def _qa_search(self, query_text: str, k: int) -> list[dict]:
        \"\"\"Search QA library by BM25 over questions.\"\"\"
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
                    "text": f"Q: {q['question']}\\nA: {q['answer']}",
                    "page": None,
                    "section": q.get("section", ""),
                    "score": round(scores[idx], 4),
                    "type": "qa",
                    "guideline_id": q.get("guideline_id", ""),
                    "evidence_level": q.get("evidence_level", ""),
                }
            )
        return hits
"""

NEW_QA = """    def _qa_search(self, query_text: str, k: int) -> list[dict]:
        \"\"\"Search QA library: dense cosine if embeddings available, else BM25.\"\"\"
        if not self._qa:
            return []

        scored: list[tuple[int, float]] = []
        if self._qa_vecs is not None:
            try:
                import numpy as np

                qv = np.array(_embed_query(query_text))
                sims = (self._qa_vecs @ qv) / (
                    np.linalg.norm(self._qa_vecs, axis=1) * (np.linalg.norm(qv) or 1.0)
                )
                order = np.argsort(-sims)[:k]
                scored = [(int(i), float(sims[i])) for i in order if sims[i] > QA_DENSE_FLOOR]
            except Exception:
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
                body = f"{head}\\n{q['support_quote']}"
            else:
                body = f"Q: {q['question']}\\nA: {q['answer']}"
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
"""

# module-level constant inserted right after RRF weights block
OLD_CONST_ANCHOR = """    RRF_K = 60  # Reciprocal Rank Fusion constant
"""
NEW_CONST_ANCHOR = """    RRF_K = 60  # Reciprocal Rank Fusion constant
    # QA dense relevance floor: gemma-300m cosines have a high baseline; hits below
    # this are treated as no-match (calibrated on qa_channel_eval 2026-10-26).
    QA_DENSE_FLOOR = 0.30
"""


def main():
    src = RAG.read_text(encoding="utf-8")
    for old, name in ((OLD_INIT, "init"), (OLD_LOAD, "loader"), (OLD_QA, "qa_search"), (OLD_CONST_ANCHOR, "const")):
        n = src.count(old)
        if n != 1:
            print(f"ABORT: {name} pattern matched {n} times (expected 1)")
            sys.exit(1)

    if not BAK.exists():
        shutil.copy2(RAG, BAK)
        print(f"backup: {BAK}")

    patched = src.replace(OLD_INIT, NEW_INIT).replace(OLD_LOAD, NEW_LOAD)
    patched = patched.replace(OLD_QA, NEW_QA).replace(OLD_CONST_ANCHOR, NEW_CONST_ANCHOR)
    RAG.write_text(patched, encoding="utf-8")

    for p in (BAK, RAG):
        print(p.name, hashlib.md5(p.read_bytes()).hexdigest())
    print("APPLIED PF3 OK")


if __name__ == "__main__":
    main()
