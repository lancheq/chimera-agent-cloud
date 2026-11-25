"""v2 运行时适配器：drop-in 替换 GuidelinesSearch，只换数据源。

不改变任何检索算法——dense 20 / BM25 20 / RRF 30 / 重排 max_len 512 / 正文 5 /
P1 回填 ≤2 小节 / F6 泛词处理，与 FREEZE_v2.md 一致。

对外接口与 rag.py 的 GuidelinesSearch.query 相同：返回 list[dict]，
字段 text / page / section / score / type。

type 取值：
  chunk  主命中（前 5）
  branch P1 回填的分支条目
  qa     QA 旁路（原库 428 条，独立于知识库切分）

用法：
    from v2_search_adapter import V2GuidelineSearch
    s = V2GuidelineSearch(v2_dir, resource_dir, reranker_dir, p1=True)
    hits = s.query("biopsy indication ...")
    stages = s.query_debug("...")   # 验收用：返回各阶段 id/rank/score
"""

import json
import os
import re
import socket
from pathlib import Path

import numpy as np
import torch

COLLECTION_TOP = 5
DENSE_K = 20
BM25_K = 20
RRF_K = 60
RERANK_IN = 30
P1_MAX_SECTIONS = 2
QA_K = 3
QA_FLOOR = 0.30
MAX_LEN = 512

GEN = re.compile(r"\b(eau|aua|esur|isup|sios|guidelines?)\b", re.I)
TOK = re.compile(r"[a-z0-9]+")
SOCKET_PATH = Path(os.environ.get("CHIMERA_EMBED_SOCKET", "/tmp/chimera_embed.sock"))


def strip_generic(q):
    return re.sub(r"\s+", " ", GEN.sub(" ", q)).strip()


def tok(s):
    return TOK.findall(s.lower())


class BM25:
    def __init__(self, corpus, k1=1.5, b=0.75):
        self.k1, self.b = k1, b
        self.tf, self.dl, df = [], [], {}
        for t in corpus:
            d = tok(t)
            self.dl.append(len(d))
            c = {}
            for w in d:
                c[w] = c.get(w, 0) + 1
            self.tf.append(c)
            for w in set(d):
                df[w] = df.get(w, 0) + 1
        self.n = len(corpus)
        self.avg = sum(self.dl) / max(self.n, 1)
        self.idf = {w: np.log(1 + (self.n - c + 0.5) / (c + 0.5)) for w, c in df.items()}

    def scores(self, q):
        out = np.zeros(self.n, dtype="float32")
        for i in range(self.n):
            tf, dl = self.tf[i], self.dl[i]
            s = 0.0
            for w in q:
                c = tf.get(w)
                if c:
                    s += self.idf.get(w, 0) * c * (self.k1 + 1) / (
                        c + self.k1 * (1 - self.b + self.b * dl / max(self.avg, 1)))
            out[i] = s
        return out


BRANCH = re.compile(r"^(when|if|in men|in patients|where|for patients)", re.I)


class V2GuidelineSearch:
    def __init__(self, v2_dir, resource_dir, reranker_dir, p1=True, qa=True,
                 embedding_dir=None):
        self.v2_dir = Path(v2_dir)
        self.resource_dir = Path(resource_dir)
        self.reranker_dir = Path(reranker_dir)
        self.use_p1 = p1
        self.use_qa = qa
        self._enc = None
        self._embedding_dir = embedding_dir or str(self.resource_dir / "embedding_model")

        recs = [json.loads(l) for l in open(self.v2_dir / "records_v2.jsonl", encoding="utf-8")]
        self.units = [r for r in recs
                      if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
        self.parents = {r.get("parent_id"): r for r in recs if r["kind"] == "section_full"}
        self.ids = [r["id"] for r in self.units]
        self.pos = {i: k for k, i in enumerate(self.ids)}
        self.ret = [r["retrieval_text"] for r in self.units]
        self.orig = [r["original_text"] for r in self.units]

        v = np.load(self.v2_dir / "vectors.npy").astype("float32")
        assert v.shape[0] == len(self.units), f"{v.shape[0]} vs {len(self.units)}"
        self.vec = v / (np.linalg.norm(v, axis=1, keepdims=True) + 1e-9)

        self.bm25 = BM25(self.ret)

        by_sec = {}
        for r in self.units:
            if r["kind"] == "recommendation":
                by_sec.setdefault(r.get("section_id"), []).append(r["id"])
        self.by_sec = by_sec

        from transformers import AutoModelForSequenceClassification, AutoTokenizer
        self.rt = AutoTokenizer.from_pretrained(str(self.reranker_dir))
        self.rm = AutoModelForSequenceClassification.from_pretrained(str(self.reranker_dir))
        self.rm.eval()

        self.qa = []
        if self.use_qa:
            self._load_qa()

    # ---------- 编码 ----------
    def _encode(self, text):
        """优先走 EmbeddingService socket（与 Agent 运行时一致），否则本地加载。"""
        if SOCKET_PATH.exists():
            try:
                s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                s.connect(str(SOCKET_PATH))
                s.sendall(json.dumps(text).encode())
                s.shutdown(socket.SHUT_WR)
                data = b""
                while True:
                    ch = s.recv(65536)
                    if not ch:
                        break
                    data += ch
                s.close()
                return np.array(json.loads(data.decode()), dtype="float32")
            except Exception:
                pass
        if self._enc is None:
            from sentence_transformers import SentenceTransformer
            self._enc = SentenceTransformer(self._embedding_dir, device="cpu",
                                            local_files_only=True)
        return np.array(self._enc.encode_query(text), dtype="float32")

    def _load_qa(self):
        p = self.resource_dir / "guideline_qa.jsonl"
        npy = self.resource_dir / "guideline_qa.embeddings.npy"
        if not p.exists() or not npy.exists():
            return
        for line in p.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            rec = json.loads(line)
            g0 = (rec.get("grounding") or [{}])[0]
            sec_id = str(g0.get("section_id", ""))
            if not sec_id and rec.get("guideline_id"):
                sec_id = str(rec["guideline_id"]).split("-", 1)[-1]
            self.qa.append({"question": rec.get("question", ""),
                            "answer": rec.get("answer", ""),
                            "section_id": sec_id,
                            "section_title": g0.get("section_title", rec.get("section", "")),
                            "source": g0.get("source", ""),
                            "support_quote": rec.get("support_quote", "")})
        vecs = np.load(npy)
        self.qa_vecs = vecs if vecs.shape[0] == len(self.qa) else None

    # ---------- 检索 ----------
    def _dense(self, q, k=DENSE_K):
        v = self._encode(q)
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = self.vec @ v
        idx = np.argsort(-sims)[:k]
        return idx, sims

    def _bm25(self, q, k=BM25_K):
        bs = self.bm25.scores(tok(q))
        return np.argsort(-bs)[:k], bs

    def _rrf(self, d_idx, sims, b_idx, bs):
        fused, pool = {}, {}
        for rank, i in enumerate(d_idx):
            pool[i] = 1
            fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + rank + 1)
        for rank, i in enumerate(b_idx):
            if bs[i] <= 0:
                continue
            pool[i] = 1
            fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + rank + 1)
        order = sorted(pool, key=lambda i: -fused.get(i, 0))
        return order, fused

    def _rerank(self, q, order, top=RERANK_IN):
        cand = order[:top]
        if not cand:
            return [], []
        pairs = [(q, self.ret[i]) for i in cand]
        inp = self.rt(pairs, padding=True, truncation=True, return_tensors="pt",
                      max_length=MAX_LEN)
        with torch.no_grad():
            sc = self.rm(**inp).logits.squeeze(-1).tolist()
        ranked = sorted(zip(cand, sc), key=lambda x: -x[1])
        return [i for i, _ in ranked], [float(s) for _, s in ranked]

    def _p1(self, top_ids):
        """命中条目若为条件型推荐，补齐同小节分支；按首次命中顺序，最多 2 小节。"""
        if not self.use_p1:
            return []
        secs, extra = [], []
        for rid in top_ids:
            r = self.units[self.pos[rid]]
            if r["kind"] != "recommendation":
                continue
            if not BRANCH.match(r["original_text"].strip()):
                continue
            sec = r.get("section_id")
            if sec in secs:
                continue
            if len(secs) >= P1_MAX_SECTIONS:
                break
            secs.append(sec)
            for s in self.by_sec.get(sec, []):
                if s in top_ids or s in extra:
                    continue
                if BRANCH.match(self.units[self.pos[s]]["original_text"].strip()):
                    extra.append(s)
        return extra

    def _qa(self, q, k=QA_K):
        if not self.qa or getattr(self, "qa_vecs", None) is None:
            return []
        v = self._encode(q)
        v = v / (np.linalg.norm(v) + 1e-9)
        sims = (self.qa_vecs @ v) / (
            np.linalg.norm(self.qa_vecs, axis=1) * (np.linalg.norm(v) or 1.0) + 1e-9)
        out = []
        for i in np.argsort(-sims)[:k]:
            if sims[i] <= QA_FLOOR:
                continue
            qq = self.qa[int(i)]
            head = f"[{qq['source']} {qq['section_id']}]".strip()
            if qq.get("section_title"):
                head += f" {qq['section_title']}"
            body = qq.get("support_quote") or f"Q: {qq['question']}\nA: {qq['answer']}"
            out.append({"text": f"{head}\n{body}", "page": None,
                        "section": qq.get("section_id", ""),
                        "score": round(float(sims[i]), 4), "type": "qa"})
        return out

    # ---------- 对外 ----------
    def query(self, query_text, top_k=COLLECTION_TOP):
        st = self.query_debug(query_text, top_k)
        out = []
        for rid in st["final"]:
            r = self.units[self.pos[rid]]
            out.append({"text": r["original_text"], "page": r.get("page"),
                        "section": r.get("section_id"), "score": st["final_scores"][rid],
                        "type": "chunk"})
        for rid in st["p1_extra"]:
            r = self.units[self.pos[rid]]
            out.append({"text": r["original_text"], "page": r.get("page"),
                        "section": r.get("section_id"), "score": None,
                        "type": "branch"})
        out.extend(st["qa"])
        return out

    def query_debug(self, query_text, top_k=COLLECTION_TOP):
        q = strip_generic(query_text)
        d_idx, sims = self._dense(q)
        b_idx, bs = self._bm25(q)
        order, fused = self._rrf(d_idx, sims, b_idx, bs)
        ranked, scores = self._rerank(q, order)
        final = [self.ids[i] for i in ranked[:top_k]]
        final_scores = {self.ids[i]: round(sc, 4) for i, sc in zip(ranked[:top_k], scores[:top_k])}
        extra = self._p1(final)
        return {
            "query_used": q,
            "dense": [{"id": self.ids[i], "rank": r + 1, "score": round(float(sims[i]), 4)}
                      for r, i in enumerate(d_idx)],
            "bm25": [{"id": self.ids[i], "rank": r + 1, "score": round(float(bs[i]), 4)}
                     for r, i in enumerate(b_idx)],
            "rrf": [{"id": self.ids[i], "rank": r + 1, "score": round(fused[i], 6)}
                    for r, i in enumerate(order)],
            "rerank": [{"id": self.ids[i], "rank": r + 1, "score": round(sc, 4)}
                       for r, (i, sc) in enumerate(zip(ranked, scores))],
            "final": final,
            "final_scores": final_scores,
            "p1_extra": extra,
            "qa": self._qa(q),
            "counts": {"dense": len(d_idx), "bm25": len(b_idx), "rrf": len(order),
                       "rerank": len(ranked), "final": len(final), "p1": len(extra)},
        }
