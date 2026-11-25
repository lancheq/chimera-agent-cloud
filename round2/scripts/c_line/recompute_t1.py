"""四组检索对照的统计修正：

1. 旧库（A/D）从 chroma 重新导出 section 元数据，映射 clinical_context
2. 场景匹配支持层级（父类标签算适用）
3. 重算 30 例 × 4 组

不重跑检索，只重算统计；检索结果来自 /tmp/t1_retrieval_abcd.json。
"""

import json
import re
import shutil
from pathlib import Path

import numpy as np

SRC = Path("/home/hedongze/CHIMERA-agent/resources/guidelines_db")
WORK = Path("/tmp/kb_meta")
DST = WORK / "guidelines_db"

if not (WORK / "old_sections.json").exists():
    if DST.exists():
        shutil.rmtree(DST)
    WORK.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SRC, DST)
    import chromadb

    client = chromadb.PersistentClient(path=str(DST))
    col = client.get_collection("guidelines")
    got = col.get(include=["metadatas"])
    sec = {}
    for i, m in zip(got["ids"], got["metadatas"]):
        sec[str(i)] = (m or {}).get("section", "")
    (WORK / "old_sections.json").write_text(json.dumps(sec, ensure_ascii=False))
    print("exported sections", len(sec))
else:
    sec = json.load(open(WORK / "old_sections.json"))
    print("cached sections", len(sec))

# --- 场景映射（与 v2 同一套，三级优先/二级兜底） ---
CTX3 = {"5.5.7": ["mri_biopsy_decision"], "5.5.8": ["mri_biopsy_decision"],
        "5.7.1": ["repeat_biopsy_prior_negative"],
        "6.2.1.3": ["active_surveillance_confirmed"],
        "6.4.8": ["biochemical_recurrence"],
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


def ctx_of(sid):
    """旧库 section 形如 '5.5.7 Recommendations for...'，提取开头编号；
    后接期刊引用样式的判为伪标题。"""
    s = (sid or "").strip()
    if not s:
        return ["unknown"]
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


# --- 层级匹配：父类标签也算适用 ---
ANCESTOR = {
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
        if e in s:
            return True
        if s & ANCESTOR.get(e, set()):
            return True
    return False


SCENE_CTX = {
    "initial_diagnosis": ["mri_biopsy_decision", "biopsy_indication_strategy"],
    "post_negative_review": ["repeat_biopsy_prior_result", "repeat_biopsy_prior_negative"],
    "post_positive_review": ["active_surveillance_confirmed"],
}

data = json.load(open("/tmp/t1_retrieval_abcd.json"))
v2 = [json.loads(l) for l in open(
    "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2/records_v2.jsonl")]
v2map = {r["id"]: r for r in v2}
o = np.load("/tmp/kb_export/old_kb.npz", allow_pickle=True)
otexts = [str(t) for t in o["texts"]]
oidx = {str(i): k for k, i in enumerate(o["ids"])}

rows = []
for rec in data:
    scene = rec["scene"]
    exp = SCENE_CTX[scene]
    for g, gd in rec["groups"].items():
        st = {"applicable": 0, "mismatch": 0, "unknown": 0, "nonclinical": 0}
        for rid in gd["top5"]:
            if g.startswith(("B_", "C_")):
                r = v2map.get(rid)
                if not r:
                    continue
                ctxs = r["clinical_context"]
                nonclin = r["doc_type"] == "nonclinical"
            else:
                sid = sec.get(rid, "")
                ctxs = ctx_of(sid)
                nonclin = ctxs == ["nonclinical_methodology"]
            if nonclin:
                st["nonclinical"] += 1
            elif ctxs == ["unknown"]:
                st["unknown"] += 1
            elif matches(ctxs, exp):
                st["applicable"] += 1
            else:
                st["mismatch"] += 1
        gd["scene_stats_v2"] = st
    rows.append(rec)

print("\n%-14s %-6s %-6s %-6s %-6s %-8s %-8s" %
      ("group", "ctx", "适用", "错配", "未知", "非临床", "适用率"))
for g in ("A_old_mixed", "B_v2_noP1", "C_v2_P1", "D_old2025"):
    agg = {"applicable": 0, "mismatch": 0, "unknown": 0, "nonclinical": 0}
    n = 0
    tok = 0
    for rec in rows:
        s = rec["groups"][g]["scene_stats_v2"]
        for k in agg:
            agg[k] += s[k]
        n += rec["groups"][g]["ctx_n"]
        tok += rec["groups"][g]["tokens"]
    judged = n - agg["nonclinical"]
    print("%-14s %-6d %-6d %-6d %-6d %-8d %-8.1f%% tok=%.0f" %
          (g, n, agg["applicable"], agg["mismatch"], agg["unknown"],
           agg["nonclinical"], 100 * agg["applicable"] / max(judged, 1), tok / len(rows)))

print("\n=== 分场景（适用率）")
for scene in ("initial_diagnosis", "post_negative_review", "post_positive_review"):
    sub = [r for r in rows if r["scene"] == scene]
    print(f"\n{scene}  n={len(sub)}")
    for g in ("A_old_mixed", "B_v2_noP1", "C_v2_P1", "D_old2025"):
        agg = {"applicable": 0, "mismatch": 0, "unknown": 0, "nonclinical": 0}
        n = 0
        for rec in sub:
            s = rec["groups"][g]["scene_stats_v2"]
            for k in agg:
                agg[k] += s[k]
            n += rec["groups"][g]["ctx_n"]
        judged = n - agg["nonclinical"]
        print("  %-14s 适用%-4d 错配%-4d 未知%-3d 适用率%.0f%%" %
              (g, agg["applicable"], agg["mismatch"], agg["unknown"],
               100 * agg["applicable"] / max(judged, 1)))

json.dump(rows, open("/tmp/t1_retrieval_abcd_v2.json", "w"), ensure_ascii=False, indent=1)
print("\nWROTE /tmp/t1_retrieval_abcd_v2.json")
