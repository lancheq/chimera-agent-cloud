"""30 例留出病例的固定 Query 四组检索对照。

A 原始混合库(2889) / B v2 无 P1 / C v2 有 P1 / D 纯2025旧切分(1375)

Query 由病例资料按场景模板自动构造（不使用 Agent 生成的 query），
四组共用同一 query、同一 dense/BM25/RRF/重排参数（v2 冻结配置）。
"""

import json
import re
import sys
from pathlib import Path

import numpy as np
import torch
from transformers import AutoModelForSequenceClassification, AutoTokenizer

POOL = ("/home/hedongze/CHIMERA-agent/data/round2_materials_20261121/"
        "train_release_transfer/20261121_104011_78d1168e/train_release_chimera_phase2/task1")
V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
OLD = "/tmp/kb_export/old_kb.npz"
SAMPLE = "/tmp/t1_sample.json"
RRF_K = 60

GEN = re.compile(r"\b(eau|aua|esur|isup|sios|guidelines?)\b", re.I)
strip = lambda q: re.sub(r"\s+", " ", GEN.sub(" ", q)).strip()
TOK = re.compile(r"[a-z0-9]+")
tok = lambda s: TOK.findall(s.lower())

sample = json.load(open(SAMPLE))["sample"]


def case_features(cid):
    d = POOL + "/" + cid
    clin = json.load(open(d + "/prostate-biopsy-decision-clinical-data.json"))
    pr = json.load(open(d + "/structured-prompt.json"))
    rad = clin.get("radiology_report", "") or ""
    m = re.search(r"PI-?RADS\s*(?:score|category)?\s*[:\-]?\s*([0-9](?:\s*[-–]\s*[0-9])?)", rad, re.I)
    pirads = m.group(1).strip() if m else ""
    psa = ""
    for r in clin.get("laboratory_results", []) or []:
        if str(r.get("name", "")).upper() == "PSA":
            psa = str(r.get("val", "")).split()[0]
    vol = re.search(r"volume[^0-9]{0,20}([0-9.]+)\s*mL", rad, re.I)
    psad = ""
    if psa and vol:
        try:
            psad = round(float(psa) / float(vol.group(1)), 2)
        except Exception:
            psad = ""
    return {"pirads": pirads, "psa": psa, "psad": psad, "age": pr.get("age"),
            "bx": pr.get("bx"), "dre": pr.get("dre"), "fh": clin.get("family_history", ""),
            "cspca": clin.get("cspca")}


def make_query(scene, f):
    a = f["age"] or ""
    p = f"PI-RADS {f['pirads']}" if f["pirads"] else "MRI result"
    d = f"PSA density {f['psad']}" if f["psad"] else ""
    if scene == "initial_diagnosis":
        q = f"initial biopsy indication elevated PSA {f['psa']} {p} {d} in {a}-year-old male"
    elif scene == "post_negative_review":
        q = f"repeat biopsy after negative biopsy persistent PSA elevation {p} {d} PSA {f['psa']}"
    else:
        q = f"re-evaluation after positive biopsy rising PSA {p} {d} follow-up biopsy schedule"
    return re.sub(r"\s+", " ", q).strip()


# ---------- 语料 ----------
old = np.load(OLD, allow_pickle=True)
o_ids, o_vecs, o_texts, o_ver = list(old["ids"]), old["vectors"], old["texts"], old["versions"]

v2 = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]
core = [r for r in v2 if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
n_ids = [r["id"] for r in core]
n_ret = [r["retrieval_text"] for r in core]
n_vec = np.load(V2 / "vectors.npy")


def norm(m):
    return m / (np.linalg.norm(m, axis=1, keepdims=True) + 1e-9)


m26 = o_ver == "2026"
m25 = o_ver == "2025"
CORPUS = {
    "A_old_mixed": ([str(i) for i in o_ids], norm(o_vecs), [str(t) for t in o_texts], None),
    "B_v2_noP1": (n_ids, norm(n_vec), n_ret, core),
    "C_v2_P1": (n_ids, norm(n_vec), n_ret, core),
    "D_old2025": ([str(o_ids[k]) for k in np.where(m25)[0]],
                  norm(o_vecs[m25]), [str(o_texts[k]) for k in np.where(m25)[0]], None),
}


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


from sentence_transformers import SentenceTransformer

enc = SentenceTransformer(EMB, device="cpu", local_files_only=True)
BM = {g: BM25(c[2]) for g, c in CORPUS.items()}
rt = AutoTokenizer.from_pretrained(RER)
rm = AutoModelForSequenceClassification.from_pretrained(RER)
rm.eval()
print("ready", flush=True)

SCENE_CTX = {
    "initial_diagnosis": ["mri_biopsy_decision", "biopsy_indication_strategy"],
    "post_negative_review": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
    "post_positive_review": ["active_surveillance_confirmed"],
}
BRANCH = re.compile(r"^(when|if|in men|in patients|where|for patients)", re.I)
BY_SEC = {}
for r in core:
    if r["kind"] == "recommendation":
        BY_SEC.setdefault(r.get("section_id"), []).append(r["id"])
pos_n = {i: k for k, i in enumerate(n_ids)}

out = []
for scene, ids in sample.items():
    for cid in ids:
        try:
            f = case_features(cid)
        except Exception as e:
            print("feat fail", cid, e, flush=True)
            continue
        q = make_query(scene, f)
        q_use = strip(q)
        v = np.array(enc.encode_query(q_use))
        v = v / (np.linalg.norm(v) + 1e-9)
        rec = {"case": cid, "scene": scene, "query": q, "features": f, "groups": {}}
        for g, (ids_g, vecs, texts, meta) in CORPUS.items():
            sims = vecs @ v
            d_idx = np.argsort(-sims)[:20]
            bs = BM[g].scores(tok(q_use))
            b_idx = np.argsort(-bs)[:20]
            fused, pool = {}, {}
            for r0, i in enumerate(d_idx):
                pool[i] = 1
                fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + r0 + 1)
            for r0, i in enumerate(b_idx):
                if bs[i] <= 0:
                    continue
                pool[i] = 1
                fused[i] = fused.get(i, 0) + 1.0 / (RRF_K + r0 + 1)
            order = sorted(pool, key=lambda i: -fused.get(i, 0))[:30]
            pairs = [(q_use, texts[i]) for i in order]
            inp = rt(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
            with torch.no_grad():
                sc = rm(**inp).logits.squeeze(-1).tolist()
            rk = [ids_g[order[j]] for j in np.argsort(-np.array(sc))]
            top5 = rk[:5]

            ctx = list(top5)
            if g == "C_v2_P1" and meta:
                for i in top5:
                    r = meta[pos_n[i]]
                    if r["kind"] == "recommendation" and BRANCH.match(r["original_text"].strip()):
                        for s in BY_SEC.get(r.get("section_id"), []):
                            if s not in ctx and BRANCH.match(meta[pos_n[s]]["original_text"].strip()):
                                ctx.append(s)

            # 场景统计
            exp = SCENE_CTX[scene]
            st = {"applicable": 0, "mismatch": 0, "unknown": 0, "nonclinical": 0}
            if meta:
                for i in ctx:
                    r = meta[pos_n[i]]
                    if r["doc_type"] == "nonclinical":
                        st["nonclinical"] += 1
                    elif r["clinical_context"] == ["unknown"]:
                        st["unknown"] += 1
                    elif set(r["clinical_context"]) & set(exp):
                        st["applicable"] += 1
                    else:
                        st["mismatch"] += 1
            if meta:
                body = "\n".join(meta[pos_n[i]]["original_text"] for i in ctx)
            else:
                idx_map = {v: k for k, v in enumerate(ids_g)}
                body = "\n".join(texts[idx_map[i]] for i in ctx)
            tok_n = len(rt(body)["input_ids"]) if body else 0
            rec["groups"][g] = {"top5": top5, "ctx_n": len(ctx), "tokens": tok_n, "scene_stats": st}
        out.append(rec)
        print(cid, scene, "done", flush=True)

json.dump(out, open("/tmp/t1_retrieval_abcd.json", "w"), ensure_ascii=False, indent=1)
print("WROTE", len(out), "cases", flush=True)

# 汇总
print("\n=== 汇总（按组）")
for g in CORPUS:
    agg = {"applicable": 0, "mismatch": 0, "unknown": 0, "nonclinical": 0, "n": 0, "tok": 0}
    for r in out:
        s = r["groups"][g]["scene_stats"]
        for k in ("applicable", "mismatch", "unknown", "nonclinical"):
            agg[k] += s[k]
        agg["n"] += r["groups"][g]["ctx_n"]
        agg["tok"] += r["groups"][g]["tokens"]
    print("%-14s ctx=%-4d 适用=%-4d 错配=%-4d 未知=%-4d 非临床=%-3d 平均token=%.0f" %
          (g, agg["n"], agg["applicable"], agg["mismatch"], agg["unknown"],
           agg["nonclinical"], agg["tok"] / max(len(out), 1)))
