"""三件事：
1. 统一分母 + 补齐 C 组 P1 新增 6 条的分类 + 病例级完整决策证据覆盖率
2. A/D 在阴性后复评的差异中，EAU 2026 版的实际贡献
3. PI-RADS 5 失败病例（PT-pseudo_62c93b0c3dba）逐阶段定位 §5.5.7 的丢失点
"""

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np
import torch
from sentence_transformers import SentenceTransformer
from transformers import AutoModelForSequenceClassification, AutoTokenizer

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
EMB = "/home/hedongze/CHIMERA-agent/resources/embedding_model"
RER = "/home/hedongze/CHIMERA-agent/resources/reranker_model"
OLD = "/tmp/kb_export/old_kb.npz"
DATA = "/tmp/t1_retrieval_abcd.json"
SECFILE = "/tmp/kb_meta/old_sections.json"
RRF_K = 60

CTX3 = {"5.5.7": ["mri_biopsy_decision"], "5.5.8": ["mri_biopsy_decision"],
        "5.7.1": ["repeat_biopsy_prior_negative"],
        "6.2.1.3": ["active_surveillance_confirmed"], "6.4.8": ["biochemical_recurrence"],
        "6.7.12": ["metastatic_crpc"], "6.7.13": ["metastatic_crpc"],
        "6.7.14": ["metastatic_crpc"], "5.6.4": ["biopsy_technique"]}
CTX2R = {"5.1": ["early_detection_screening"], "5.2": ["diagnostic_tools"],
         "5.3": ["pathology_processing"], "5.4": ["risk_assessment_pre_biopsy"],
         "5.5": ["biopsy_indication_strategy"], "5.6": ["biopsy_technique"],
         "5.7": ["repeat_biopsy_prior_result"], "5.8": ["clinical_staging"],
         "6.1": ["treatment_fitness_assessment"], "6.2": ["active_surveillance_confirmed"],
         "6.3": ["watchful_waiting_or_local_treatment"], "6.4": ["biochemical_recurrence"],
         "6.5": ["hormonal_therapy"], "6.6": ["metastatic_crsPC"], "6.7": ["castration_resistant"],
         "4.4": ["tnm_staging"], "4.5": ["classification_staging"]}
CTX1 = {"1": ["nonclinical_methodology"], "2": ["nonclinical_methodology"],
        "3": ["epidemiology_background"], "4": ["classification_staging"],
        "5": ["diagnostic_evaluation"], "6": ["treatment"], "7": ["follow_up"],
        "8": ["quality_of_life"]}
LEAD = re.compile(r"^\s*(\d+(?:\.\d+){0,3})")
JOURNAL = re.compile(r"(Lancet|Eur Urol|J Clin Oncol|N Engl J Med|J Urol|BJU Int|Radiology|Oncol)", re.I)


def ctx_of_old(sid):
    s = (sid or "").strip()
    m = LEAD.match(s)
    if not m:
        return ["unknown"]
    num = m.group(1)
    rest = s[m.end():]
    if JOURNAL.search(rest) and re.match(r"^\s+\d", rest):
        return ["unknown"]
    for k, v in CTX3.items():
        if num == k or num.startswith(k + "."):
            return v
    two = ".".join(num.split(".")[:2])
    if two in CTX2R:
        return CTX2R[two]
    return CTX1.get(num.split(".")[0], ["unknown"])


ANC = {
    "mri_biopsy_decision": {"diagnostic_evaluation", "biopsy_indication_strategy"},
    "biopsy_indication_strategy": {"diagnostic_evaluation"},
    "repeat_biopsy_prior_result": {"diagnostic_evaluation"},
    "repeat_biopsy_prior_negative": {"repeat_biopsy_prior_result", "diagnostic_evaluation"},
    "diagnostic_tools": {"diagnostic_evaluation"},
    "risk_assessment_pre_biopsy": {"diagnostic_evaluation"},
    "biopsy_technique": {"diagnostic_evaluation"},
    "early_detection_screening": {"diagnostic_evaluation"},
    "active_surveillance_confirmed": {"treatment", "follow_up"},
    "watchful_waiting_or_local_treatment": {"treatment"},
    "biochemical_recurrence": {"treatment", "follow_up"},
}


def matches(item_ctx, exp):
    s = set(item_ctx)
    for e in exp:
        if e in s or (s & ANC.get(e, set())):
            return True
    return False


SCENE_CTX = {
    "initial_diagnosis": ["mri_biopsy_decision", "biopsy_indication_strategy"],
    "post_negative_review": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
    "post_positive_review": ["active_surveillance_confirmed"],
}

v2 = {r["id"]: r for r in
      (json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8"))}
v2list = [r for r in v2.values() if r["kind"] in ("narrative", "recommendation", "table", "nonclinical")]
sec = json.load(open(SECFILE))
o = np.load(OLD, allow_pickle=True)
o_ids = [str(i) for i in o["ids"]]
o_ver = o["versions"]
o_idx = {i: k for k, i in enumerate(o_ids)}
data = json.load(open(DATA))

BRANCH = re.compile(r"^(when|if|in men|in patients|where|for patients)", re.I)
BY_SEC = defaultdict(list)
for r in v2list:
    if r["kind"] == "recommendation":
        BY_SEC[r.get("section_id")].append(r["id"])


def classify_v2(rid, exp):
    r = v2.get(rid)
    if not r:
        return "unknown"
    if r["doc_type"] == "nonclinical":
        return "nonclinical"
    c = r["clinical_context"]
    if c == ["unknown"]:
        return "unknown"
    return "applicable" if matches(c, exp) else "mismatch"


def classify_old(rid, exp):
    sid = sec.get(rid, "")
    c = ctx_of_old(sid)
    if c == ["nonclinical_methodology"]:
        return "nonclinical"
    if c == ["unknown"]:
        return "unknown"
    return "applicable" if matches(c, exp) else "mismatch"


# ---------- 1. 统一分母 + C 组 P1 补齐 + 病例级覆盖率 ----------
print("=" * 78)
print("1) 统一分母（分母=全部返回条目，不排除非临床）")
print("%-14s %-7s %-6s %-6s %-6s %-7s %-9s %-10s" %
      ("组", "总条目", "适用", "错配", "未知", "非临床", "适用率", "病例级覆盖"))

summary = {}
for g in ("A_old_mixed", "B_v2_noP1", "C_v2_P1", "D_old2025"):
    cnt = Counter()
    total = 0
    case_ok = 0
    for rec in data:
        exp = SCENE_CTX[rec["scene"]]
        top5 = rec["groups"][g]["top5"]
        items = list(top5)
        if g == "C_v2_P1":
            # 补齐 P1 新增（按冻结规则重放）
            for rid in top5:
                r = v2.get(rid)
                if r and r["kind"] == "recommendation" and BRANCH.match(r["original_text"].strip()):
                    for s in BY_SEC.get(r.get("section_id"), []):
                        if s not in items and BRANCH.match(v2[s]["original_text"].strip()):
                            items.append(s)
        for rid in items:
            c = classify_v2(rid, exp) if g.startswith(("B_", "C_")) else classify_old(rid, exp)
            cnt[c] += 1
            total += 1
        if any((classify_v2(rid, exp) if g.startswith(("B_", "C_")) else classify_old(rid, exp)) == "applicable"
               and (v2[rid]["kind"] == "recommendation" if rid in v2 else True)
               for rid in items):
            case_ok += 1
    summary[g] = {"total": total, "cnt": dict(cnt), "case_ok": case_ok}
    print("%-14s %-7d %-6d %-6d %-6d %-7d %-9.1f%% %d/30" %
          (g, total, cnt["applicable"], cnt["mismatch"], cnt["unknown"],
           cnt["nonclinical"], 100 * cnt["applicable"] / max(total, 1), case_ok))

# ---------- 2. A/D 差异中的 2026 贡献 ----------
print("\n" + "=" * 78)
print("2) A（混合）vs D（纯2025）差异中 2026 版的贡献")
for scene in ("initial_diagnosis", "post_negative_review", "post_positive_review"):
    sub = [r for r in data if r["scene"] == scene]
    for g in ("A_old_mixed", "D_old2025"):
        c26 = c25 = 0
        app26 = app25 = 0
        for rec in sub:
            exp = SCENE_CTX[scene]
            for rid in rec["groups"][g]["top5"]:
                k = o_idx.get(rid)
                ver = o_ver[k] if k is not None else "?"
                ok = classify_old(rid, exp) == "applicable"
                if ver == "2026":
                    c26 += 1
                    app26 += ok
                elif ver == "2025":
                    c25 += 1
                    app25 += ok
        print(f"  {scene:22s} {g:13s} 2026: {c26:2d}条(适用{app26:2d}) | 2025: {c25:2d}条(适用{app25:2d})")

# ---------- 3. PI-RADS 5 病例逐阶段定位 ----------
print("\n" + "=" * 78)
print("3) PT-pseudo_62c93b0c3dba 的 §5.5.7 逐阶段定位")

case = next(r for r in data if r["case"] == "PT-pseudo_62c93b0c3dba" and r["scene"] == "initial_diagnosis")
q = case["query"]
GEN = re.compile(r"\b(eau|aua|esur|isup|guidelines?)\b", re.I)
q_use = re.sub(r"\s+", " ", GEN.sub(" ", q)).strip()
print("query:", q_use)

n_ids = [r["id"] for r in v2list]
n_ret = [r["retrieval_text"] for r in v2list]
n_vec = np.load(V2 / "vectors.npy")
n_vec = n_vec / (np.linalg.norm(n_vec, axis=1, keepdims=True) + 1e-9)
npos = {i: k for k, i in enumerate(n_ids)}

TOK = re.compile(r"[a-z0-9]+")
tok = lambda s: TOK.findall(s.lower())


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

    def scores(self, qq):
        out = np.zeros(self.n, dtype="float32")
        for i in range(self.n):
            tf, dl = self.tf[i], self.dl[i]
            s = 0.0
            for w in qq:
                c = tf.get(w)
                if c:
                    s += self.idf.get(w, 0) * c * (self.k1 + 1) / (
                        c + self.k1 * (1 - self.b + self.b * dl / max(self.avg, 1)))
            out[i] = s
        return out


enc = SentenceTransformer(EMB, device="cpu", local_files_only=True)
bm = BM25(n_ret)
rt = AutoTokenizer.from_pretrained(RER)
rm = AutoModelForSequenceClassification.from_pretrained(RER)
rm.eval()

v = np.array(enc.encode_query(q_use))
v = v / (np.linalg.norm(v) + 1e-9)
sims = n_vec @ v
d_idx = np.argsort(-sims)[:20]
bs = bm.scores(tok(q_use))
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
pairs = [(q_use, n_ret[i]) for i in order]
inp = rt(pairs, padding=True, truncation=True, return_tensors="pt", max_length=512)
with torch.no_grad():
    sc = rm(**inp).logits.squeeze(-1).tolist()
rk = [n_ids[order[j]] for j in np.argsort(-np.array(sc))]

targets = [i for i in n_ids if v2[i]["kind"] == "recommendation" and v2[i].get("section_id") == "5.5.7"]
print(f"\n§5.5.7 共 {len(targets)} 条推荐，逐条看落在哪一阶段：")
print("%-12s %-8s %-8s %-8s %-8s %s" % ("id", "dense", "bm25", "rrf30", "rerank", "文本"))
for tid in targets:
    k = npos[tid]
    dn = int(np.where(d_idx == k)[0][0]) + 1 if k in d_idx else None
    bn = int(np.where(b_idx == k)[0][0]) + 1 if k in b_idx else None
    rf = order.index(k) + 1 if k in order else None
    rr = rk.index(tid) + 1 if tid in rk else None
    print("%-12s %-8s %-8s %-8s %-8s %s" %
          (tid, dn, bn, rf, rr, v2[tid]["original_text"][:44].replace("\n", " ")))
