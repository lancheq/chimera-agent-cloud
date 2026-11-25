"""修正表格的章节归属：用版面标题位置而不是 caption 编号。

caption（Table 4.3）与章节号（§4.4）不是一一对应，之前用 caption 编号回填是错的。
改为：在表格上方（跨页则回退到上一页）找最近的 H3/H2 标题，取其编号与标题。
同时标记跨页重复检测（同一 caption 出现在连续两页）。
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz

sys.path.insert(0, "/tmp")
PDF = "/home/hedongze/CHIMERA-agent/guideline_dataset/raw/eau_2025_prostate.pdf"
V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")

CTX3 = {
    "5.5.7": ["mri_biopsy_decision"], "5.5.8": ["mri_biopsy_decision"],
    "5.7.1": ["repeat_biopsy_prior_negative"],
    "6.2.1.3": ["active_surveillance_confirmed"],
    "6.4.8": ["biochemical_recurrence"],
    "6.7.12": ["metastatic_crpc"], "6.7.13": ["metastatic_crpc"], "6.7.14": ["metastatic_crpc"],
    "5.6.4": ["biopsy_technique"],
}
CTX2 = {"1": ["nonclinical_methodology"], "2": ["nonclinical_methodology"],
        "3": ["epidemiology_background"], "4": ["classification_staging"],
        "5": ["diagnostic_evaluation"], "6": ["treatment"], "7": ["follow_up"],
        "8": ["quality_of_life"]}
CTX2R = {"5.1": ["early_detection_screening"], "5.2": ["diagnostic_tools"],
         "5.3": ["pathology_processing"], "5.4": ["risk_assessment_pre_biopsy"],
         "5.5": ["biopsy_indication_strategy"], "5.6": ["biopsy_technique"],
         "5.7": ["repeat_biopsy_prior_result"], "5.8": ["clinical_staging"],
         "6.1": ["treatment_fitness_assessment"], "6.2": ["active_surveillance_confirmed"],
         "6.3": ["watchful_waiting_or_local_treatment"], "6.4": ["biochemical_recurrence"],
         "6.5": ["hormonal_therapy"], "6.6": ["metastatic_crsPC"], "6.7": ["castration_resistant"],
         "4.4": ["tnm_staging"], "4.5": ["classification_staging"]}


def ctx_of(sid):
    sid = (sid or "").strip().rstrip(".")
    if not sid or not re.match(r"^\d", sid):
        return ["unknown"]
    for k, v in CTX3.items():
        if sid == k or sid.startswith(k + "."):
            return v
    two = ".".join(sid.split(".")[:2])
    if two in CTX2R:
        return CTX2R[two]
    return CTX2.get(sid.split(".")[0], ["unknown"])


NUM = re.compile(r"^(\d+(?:\.\d+)*)\.?$")
NUM_T = re.compile(r"^(\d+(?:\.\d+)*)\.?\s+(.+)$")


def headings(pno):
    """返回该页标题 [(y, level, id, title)]，level 1/2/3。"""
    out = []
    page = doc[pno - 1]
    for b in page.get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            spans = l["spans"]
            if not spans:
                continue
            t = "".join(s["text"] for s in spans).strip()
            if not t:
                continue
            sz = max(s["size"] for s in spans)
            fn = spans[0]["font"]
            y = l["bbox"][1]
            if sz >= 16:
                m = NUM_T.match(t)
                out.append((y, 1, m.group(1) if m else "", m.group(2) if m else t))
            elif 9.4 <= sz < 12 and "Bold" in fn and "Italic" not in fn:
                m = NUM_T.match(t)
                if m:
                    out.append((y, 2, m.group(1), m.group(2)))
            elif abs(sz - 8.8) < 0.5 and ("Italic" in fn):
                if NUM.match(t):
                    out.append((y, 3, t.rstrip("."), ""))
                else:
                    m = NUM_T.match(t)
                    if m:
                        out.append((y, 3, m.group(1), m.group(2)))
    return sorted(out)


doc = fitz.open(PDF)
recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]

# 缓存每页标题
H = {p: headings(p) for p in range(1, doc.page_count + 1)}

# 全文档标题序列，用于向前回溯
flat = []
for p in range(1, doc.page_count + 1):
    for h in H[p]:
        flat.append((p, h[0], h[1], h[2], h[3]))

def caption_y(pno, cap):
    """caption 行在页面上的 y 坐标（caption 位于表格上方）。"""
    head = (cap or "")[:22]
    if not head:
        return 0.0
    for b in doc[pno - 1].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if t and head[:14] in t:
                return l["bbox"][1]
    return 0.0


fixed = 0
for r in recs:
    if r["kind"] != "table":
        continue
    pno = r["page"]
    y = caption_y(pno, r.get("caption", ""))
    # 该页 y 之前的最近标题
    cand = [h for h in H.get(pno, []) if h[0] <= y + 6] or H.get(pno, [])
    if cand:
        y0, lvl, hid, htitle = cand[-1]
    else:
        # 回退到上一页最后一个标题
        prev = [h for p in range(pno - 1, max(pno - 3, 0), -1) for h in H.get(p, [])]
        if not prev:
            continue
        y0, lvl, hid, htitle = prev[-1]
    # 若最近标题是 h2 而更早还有 h3（同一节内），保留 h3
    old = r.get("section_id", "")
    if hid and hid != old:
        r["section_id"] = hid
        if htitle:
            r["section_title"] = htitle[:120]
        fixed += 1
    r["clinical_context"] = ctx_of(r["section_id"])
    r["parent_id"] = "parent-" + r["section_id"] if r["section_id"] else ""

# 跨页重复 caption 标记
by_cap = defaultdict(list)
for r in recs:
    if r["kind"] == "table" and r.get("caption"):
        by_cap[r["caption"].strip()].append(r)
dupg = 0
for cap, lst in by_cap.items():
    if len(lst) > 1:
        pages = sorted(x["page"] for x in lst)
        for x in lst:
            x["dup_caption"] = f"capdup-{abs(hash(cap)) % 10000:04d}"
        dupg += 1

V2.joinpath("records_v2.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")

tabs = [r for r in recs if r["kind"] == "table"]
print(json.dumps({
    "tables": len(tabs),
    "section_fixed": fixed,
    "unknown_ctx": sum(1 for t in tabs if t["clinical_context"] == ["unknown"]),
    "cross_page_dup_captions": dupg,
    "ctx": dict(Counter(c for t in tabs for c in t["clinical_context"]).most_common(10)),
}, ensure_ascii=False, indent=1))
