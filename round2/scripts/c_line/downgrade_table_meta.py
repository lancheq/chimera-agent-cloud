"""表格归属如实降级：caption 编号可追溯，但临床场景标低置信。

核对结论：EAU 的表格编号与章节号不对应（Table 4.3 在 §4.4，Table 5.2 在 §5.3），
版面标题自动定位也失败（会把 p20 的 Table 4.3 归到编号 "2"）。
因此：
  section_id     = caption 编号（可追溯到 PDF 的 Table/Figure 号）
  section_title  = 保留原值
  clinical_context = 由 caption 编号推断，并标 context_confidence="low"
  section_source = "caption_num"
不参与任何场景过滤（本轮也未启用）。
"""

import json
import re
from collections import Counter
from pathlib import Path

V2 = Path("/home/hedongze/CHIMERA-agent/guideline_dataset/rechunk_20261124_v2")
CAP_NUM = re.compile(r"^(?:Table|Figure)\s*(\d+(?:\.\d+)*)")

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


def ctx_of(num):
    if not num:
        return ["unknown"]
    two = ".".join(num.split(".")[:2])
    if two in CTX2R:
        return CTX2R[two]
    return CTX2.get(num.split(".")[0], ["unknown"])


recs = [json.loads(l) for l in open(V2 / "records_v2.jsonl", encoding="utf-8")]
for r in recs:
    if r["kind"] != "table":
        continue
    m = CAP_NUM.match(r.get("caption") or "")
    num = m.group(1) if m else ""
    r["section_id"] = num
    r["section_source"] = "caption_num"
    r["context_confidence"] = "low"
    r["clinical_context"] = ctx_of(num)
    r["parent_id"] = "parent-" + num if num else ""

V2.joinpath("records_v2.jsonl").write_text(
    "\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n", encoding="utf-8")

tabs = [r for r in recs if r["kind"] == "table"]
print(json.dumps({
    "tables": len(tabs),
    "with_caption_num": sum(1 for t in tabs if t["section_id"]),
    "context_low_confidence": sum(1 for t in tabs if t.get("context_confidence") == "low"),
    "ctx": dict(Counter(c for t in tabs for c in t["clinical_context"]).most_common(12)),
}, ensure_ascii=False, indent=1))
