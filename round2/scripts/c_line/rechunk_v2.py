"""v2 重切分：在 v1 基础上加三件事

1. 真表格提取：PyMuPDF find_tables(strategy="lines")，保留行列结构，按 caption 关联；
   无边框且无法还原的，退化为 table_prose（caption + 连续文本），不硬拆。
2. 七项元数据：source / version / doc_type / section_title / clinical_context /
   parent_id / dup_group。
3. 双文本：original_text（指南原文，用于展示与引用）/ retrieval_text（章节标题 + 原文，
   用于向量检索、BM25、重排）。

不改动任何医学正文措辞。
"""

import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

import fitz

sys.path.insert(0, "/tmp")
import rechunk_eau as v1

PDF = "/home/hedongze/CHIMERA-agent/guideline_dataset/raw/eau_2025_prostate.pdf"
SRC = "/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124"
OUT = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")

# --- 临床场景映射：三级优先，二级兜底，不确定标 unknown ---
CTX3 = {
    "5.5.7": ["mri_biopsy_decision"],
    "5.7.1": ["repeat_biopsy_prior_negative"],
    "5.5.8": ["mri_biopsy_decision"],
    "6.2.1.3": ["active_surveillance_confirmed"],
    "6.4.8": ["biochemical_recurrence"],
    "6.7.12": ["metastatic_crpc"],
    "6.7.13": ["metastatic_crpc"],
    "6.7.14": ["metastatic_crpc"],
    "5.6.4": ["biopsy_technique"],
}
CTX2 = {
    "1": ["nonclinical_methodology"],
    "2": ["nonclinical_methodology"],
    "3": ["epidemiology_background"],
    "4": ["classification_staging"],
    "5": ["diagnostic_evaluation"],
    "6": ["treatment"],
    "7": ["follow_up"],
    "8": ["quality_of_life"],
}
# 二级细化
CTX2_REFINE = {
    "5.1": ["early_detection_screening"],
    "5.2": ["diagnostic_tools"],
    "5.3": ["pathology_processing"],
    "5.4": ["risk_assessment_pre_biopsy"],
    "5.5": ["biopsy_indication_strategy"],
    "5.6": ["biopsy_technique"],
    "5.7": ["repeat_biopsy_prior_result"],
    "5.8": ["clinical_staging"],
    "6.1": ["treatment_fitness_assessment"],
    "6.2": ["active_surveillance_confirmed"],
    "6.3": ["watchful_waiting_or_local_treatment"],
    "6.4": ["biochemical_recurrence"],
    "6.5": ["hormonal_therapy"],
    "6.6": ["metastatic_crsPC"],
    "6.7": ["castration_resistant"],
    "4.4": ["tnm_staging"],
    "4.5": ["classification_staging"],
}


def clinical_context(section_id):
    sid = (section_id or "").strip().rstrip(".")
    if not sid or not re.match(r"^\d", sid):
        return ["unknown"]
    for k, v in CTX3.items():
        if sid == k or sid.startswith(k + "."):
            return v
    parts = sid.split(".")
    two = ".".join(parts[:2])
    if two in CTX2_REFINE:
        return CTX2_REFINE[two]
    return CTX2.get(parts[0], ["unknown"])


def doc_type_of(r):
    sid = (r.get("section_id") or "").strip()
    if r["kind"] == "table":
        return "table"
    if r["kind"] == "recommendation":
        return "recommendation"
    if re.match(r"^[12]\.", sid):
        return "nonclinical"
    if r["kind"] == "section_full":
        return "section_full"
    return "narrative"


# ---------- 1. v1 产出作为底稿 ----------
units, skipped, rec_stats, rec_tables = v1.build_units(PDF)
records = v1.group_records(units)
print("v1 records", len(records), flush=True)

# 去掉 v1 的 table 记录，改用真表格
base = [r for r in records if r["kind"] != "table"]
print("dropped v1 tables", len(records) - len(base), flush=True)

# ---------- 2. 真表格提取 ----------
doc = fitz.open(PDF)
CAP_RE = re.compile(r"^(Table|Figure)\s*\d+(?:\.\d+)?")

# 每页的 caption（y, text）
cap_by_page = {}
for pno in range(doc.page_count):
    caps = []
    for b in doc[pno].get_text("dict")["blocks"]:
        for l in b.get("lines", []):
            t = "".join(s["text"] for s in l["spans"]).strip()
            if CAP_RE.match(t):
                caps.append((round(l["bbox"][1], 1), t))
    if caps:
        cap_by_page[pno + 1] = sorted(caps)

# 页 → section 归属（用该页 narrative 的 section_id 众数）
page_section = defaultdict(Counter)
for r in base:
    if r["kind"] == "narrative" and r.get("page"):
        page_section[r["page"]][(r.get("section_id"), r.get("section_title"))] += 1
sec_of_page = {p: c.most_common(1)[0][0] for p, c in page_section.items()}

tables = []
last_cap = ""
for pno in range(doc.page_count):
    page = doc[pno]
    try:
        found = page.find_tables(strategy="lines")
    except Exception:
        continue
    if not found.tables:
        continue
    caps = cap_by_page.get(pno + 1, [])
    for tb in found.tables:
        y0 = tb.bbox[1]
        cap = ""
        cand = [c for c in caps if c[0] <= y0 + 2]
        if cand:
            cap = cand[-1][1]
        elif caps:
            cap = caps[0][1]
        elif last_cap:
            cap = last_cap
        if cap:
            last_cap = cap
        data = [[(c or "").strip() for c in row] for row in tb.extract()]
        data = [row for row in data if any(c for c in row)]
        if len(data) < 2:
            continue
        header = data[0]
        rows = data[1:]
        body = "\n".join(" | ".join(row) for row in data)
        sid, stitle = sec_of_page.get(pno + 1, ("", ""))
        tables.append({
            "kind": "table",
            "source": "EAU",
            "version": "2025",
            "section_id": sid,
            "section_title": stitle,
            "page": pno + 1,
            "caption": cap,
            "header": header,
            "rows": rows,
            "n_rows": len(rows),
            "n_cols": len(header),
            "original_text": f"{cap}\n{body}".strip(),
        })

print("real tables", len(tables), flush=True)
print("rows total", sum(t["n_rows"] for t in tables), flush=True)

# ---------- 3. 合并 + 元数据 ----------
all_recs = base + tables

# dup_group：同一正文在原文不同章节重复出现
seen = {}
for r in all_recs:
    if r["kind"] != "recommendation":
        continue
    key = re.sub(r"\s+", " ", r.get("original_text") or r.get("text") or "").strip().lower()
    if key in seen:
        r["dup_group"] = seen[key]
    else:
        seen[key] = f"dup-{len(seen):03d}"
        r["dup_group"] = seen[key]
dup_counts = Counter(r["dup_group"] for r in all_recs if r.get("dup_group"))
real_dups = {k: v for k, v in dup_counts.items() if v > 1}

# parent_id + 双文本
for i, r in enumerate(all_recs):
    if r["kind"] != "table":
        r["original_text"] = r.get("text", "")
    sid = (r.get("section_id") or "").strip()
    r["parent_id"] = f"parent-{sid}" if sid else ""
    r["doc_type"] = doc_type_of(r)
    r["clinical_context"] = clinical_context(sid)
    r["source"] = r.get("source", "EAU")
    r["version"] = "2025"
    title = (r.get("section_title") or "").strip()
    if r["kind"] in ("recommendation", "table") and title:
        r["retrieval_text"] = f"{title}. {r['original_text']}"
    else:
        r["retrieval_text"] = r["original_text"]
    r["id"] = f"v2-{i:05d}"
    r.pop("text", None) if False else None

OUT.mkdir(parents=True, exist_ok=True)
with open(OUT / "records_v2.jsonl", "w", encoding="utf-8") as f:
    for r in all_recs:
        f.write(json.dumps(r, ensure_ascii=False) + "\n")

summary = {
    "total": len(all_recs),
    "by_kind": dict(Counter(r["kind"] for r in all_recs)),
    "by_doc_type": dict(Counter(r["doc_type"] for r in all_recs)),
    "by_context": dict(Counter(c for r in all_recs for c in r["clinical_context"])),
    "tables": len(tables),
    "table_rows": sum(t["n_rows"] for t in tables),
    "dup_groups_with_repeats": len(real_dups),
    "retrieval_text_differs": sum(1 for r in all_recs if r["retrieval_text"] != r["original_text"]),
}
(OUT / "summary_v2.json").write_text(json.dumps(summary, ensure_ascii=False, indent=1))
print(json.dumps(summary, ensure_ascii=False, indent=1), flush=True)
