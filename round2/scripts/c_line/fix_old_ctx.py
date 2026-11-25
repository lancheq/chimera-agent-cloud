"""修旧库 section 解析（编号+标题格式），并诊断初次诊断场景适用率异常。

旧库 section 实际形如 "8.3.2.5 Recommendations for quality of life in men undergoing systemic"，
之前的纯编号正则会把它整体过滤成 unknown。
"""

import json
import re
from collections import Counter

import numpy as np

WORK = "/tmp/kb_meta/old_sections.json"
sec = json.load(open(WORK))

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


def ctx_of(sid):
    """提取开头编号；若后文像期刊引用则判为伪标题。"""
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


c = Counter()
for k, v in sec.items():
    ctx = ctx_of(v)
    c[ctx[0]] += 1
print("旧库 section → context 分布（2889 条）:")
for k, v in c.most_common(14):
    print(f"  {k:34s} {v:5d}")

unk = [v for v in sec.values() if ctx_of(v) == ["unknown"]]
print(f"\n仍 unknown: {len(unk)} ({len(unk)/len(sec):.1%})")
for x in unk[:5]:
    print("   ", repr(x[:70]))

# 诊断初次诊断场景的命中
data = json.load(open("/tmp/t1_retrieval_abcd.json"))
v2 = {r["id"]: r for r in
      (json.loads(l) for l in open("/home/hedongze/CHIMERA-agent/guideline_dataset/"
                                   "rechunk_20261124_v2/records_v2.jsonl"))}
print("\n=== 初次诊断前 3 例，B 组 top5 的小节与场景")
n = 0
for rec in data:
    if rec["scene"] != "initial_diagnosis":
        continue
    n += 1
    if n > 3:
        break
    print(f'\n{rec["case"]}  query="{rec["query"][:90]}"')
    print(f'  features: PI-RADS={rec["features"]["pirads"]} psad={rec["features"]["psad"]} psa={rec["features"]["psa"]}')
    for rid in rec["groups"]["B_v2_noP1"]["top5"]:
        r = v2.get(rid)
        if r:
            print(f'   {rid} {r["doc_type"]:14s} {str(r.get("section_id")):8s} {r["clinical_context"]} {r["original_text"][:50]}')
    print(f'  旧库 A 组 top5 section:', [sec.get(x, "")[:24] for x in rec["groups"]["A_old_mixed"]["top5"]])
