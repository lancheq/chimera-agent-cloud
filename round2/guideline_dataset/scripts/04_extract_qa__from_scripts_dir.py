"""04_extract_qa.py — Extract clinical decision QA pairs from normalized guidelines.

Each QA record:
{
  "id": str,
  "task": "T1" | "T2" | "T3",
  "question": str,
  "answer": str,
  "decision": str,
  "confidence": "high" | "medium" | "low",
  "guideline_id": str,       # e.g. "EAU-5.4.2.2"
  "section": str,            # section_title of first grounding
  "grounding": [{"source","section_id","section_title","page"}],
  "evidence_level": str
}
"""
import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
NORM = PROJECT_ROOT / "data_backup" / "guideline_dataset" / "normalized" / "guidelines_unified.jsonl"
OUT = PROJECT_ROOT / "guideline_dataset" / "qa" / "guideline_qa.jsonl"
RESOURCES_OUT = PROJECT_ROOT / "resources" / "guideline_qa.jsonl"


def load_records():
    with open(NORM, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def find_section(records, source, section_id):
    for r in records:
        if r["source"] == source and r["section_id"] == section_id:
            return r
    return None


def make_grounding(records, specs):
    g = []
    for src, sid in specs:
        r = find_section(records, src, sid)
        if r:
            g.append({
                "source": r["source"],
                "section_id": r["section_id"],
                "section_title": r["section_title"],
                "page": r["page"],
            })
    return g


def _qa_entry(qa_id, task, rule, records):
    grounding = make_grounding(records, rule["grounding_specs"])
    first = grounding[0] if grounding else {}
    return {
        "id": qa_id,
        "task": task,
        "question": rule["question"],
        "answer": rule["answer"],
        "decision": rule["decision"],
        "confidence": rule["confidence"],
        "guideline_id": f"{first.get('source', 'EAU')}-{first.get('section_id', '')}",
        "section": first.get("section_title", ""),
        "grounding": grounding,
        "evidence_level": rule["evidence_level"],
    }


def build_t1_qa(records):
    rules = [
        {"question": "PI-RADS 5 + PSA 12 + PSAd 0.25，是否推荐活检？",
         "answer": "是，强烈推荐活检。PI-RADS 5 提示 csPCa 可能性很高，结合 PSAd>0.20 显著升高风险。EAU 建议靶向活检联合系统活检。",
         "decision": "YES", "confidence": "high",
         "grounding_specs": [("EAU", "5.4.2.2"), ("EAU", "5.6.4"), ("PIRADS", "PIRADS-0")],
         "evidence_level": "1a"},
        {"question": "PI-RADS 4 + PSA 8 + PSAd 0.12，是否推荐活检？",
         "answer": "是，推荐活检。PI-RADS 4 提示 csPCa 可能性高。EAU 建议 PI-RADS>=4 时推荐活检。",
         "decision": "YES", "confidence": "high",
         "grounding_specs": [("EAU", "5.4.2.2"), ("EAU", "5.6.4")],
         "evidence_level": "2a"},
        {"question": "PI-RADS 3 + PSA 6 + PSAd 0.18，是否推荐活检？",
         "answer": "是，推荐活检。PI-RADS 3 + PSAd>0.15 提示风险升高。EAU 建议考虑活检。",
         "decision": "YES", "confidence": "medium",
         "grounding_specs": [("EAU", "5.4.2.2")],
         "evidence_level": "2a"},
        {"question": "PI-RADS 3 + PSA 5 + PSAd 0.08，是否推荐活检？",
         "answer": "可暂缓活检。PI-RADS 3 + PSAd<0.10 时 NPV 89-94%。EAU 建议可考虑短期随访 MRI。",
         "decision": "NO", "confidence": "medium",
         "grounding_specs": [("EAU", "5.4.2.2"), ("AUA", "AUA-3")],
         "evidence_level": "2a"},
        {"question": "PI-RADS 1-2 + PSA 4 + PSAd 0.06，是否推荐活检？",
         "answer": "不推荐活检。PI-RADS 1-2 + PSAd<0.10 支持低风险。EAU 建议暂缓活检。",
         "decision": "NO", "confidence": "high",
         "grounding_specs": [("EAU", "5.4.2.2"), ("PIRADS", "PIRADS-0")],
         "evidence_level": "2a"},
        {"question": "MRI 阴性（PI-RADS 1）+ PSAd 0.22，是否推荐活检？",
         "answer": "是，考虑活检。PSAd>0.20 时 ISUP GG>=2 风险 27-40%。EAU 建议系统活检。",
         "decision": "YES", "confidence": "medium",
         "grounding_specs": [("EAU", "5.4.2.2")],
         "evidence_level": "2a"},
        {"question": "既往阴性活检 + PSA 从 4 升到 7，下一步？",
         "answer": "不应仅因一次阴性活检停止筛查。应重复 PSA、行 MRI、用风险计算器，MRI 可疑则靶向+系统活检。",
         "decision": "FOLLOW_UP", "confidence": "high",
         "grounding_specs": [("AUA", "AUA-3")],
         "evidence_level": "2a"},
        {"question": "DWI 缺失，仅有 T2W，PI-RADS 如何处理？",
         "answer": "PI-RADS 不可靠。DWI 是 PZ 主导序列，缺失时无法准确评分。应补充 DWI 或降级置信度。",
         "decision": "INSUFFICIENT", "confidence": "high",
         "grounding_specs": [("PIRADS", "PIRADS-2")],
         "evidence_level": "N/A"},
        # --- 灰区补充 ---
        {"question": "PSA 8 + PI-RADS 3 是否活检？PSAd 0.15 临界值如何决策？",
         "answer": "PI-RADS 3 + PSAd 0.15 处于临界值。EAU 建议 PSAd>0.15 时考虑活检，=0.15 应结合年龄、家族史、前列腺体积。倾向活检。",
         "decision": "YES", "confidence": "medium",
         "grounding_specs": [("EAU", "5.4.2.2")],
         "evidence_level": "2a"},
        {"question": "PSA 3 + PI-RADS 3 + PSAd 0.07，是否可以安全地不活检？",
         "answer": "可以暂缓。PI-RADS 3 + PSAd<0.10 时 NPV 89-94%。建议短期随访 MRI（6-12 月）。",
         "decision": "NO", "confidence": "high",
         "grounding_specs": [("EAU", "5.4.2.2"), ("AUA", "AUA-3")],
         "evidence_level": "2a"},
    ]
    return [_qa_entry(f"qa-t1-{i:03d}", "T1", r, records) for i, r in enumerate(rules)]


def build_t2_qa(records):
    rules = [
        {"question": "低危 PCa（cT1c, PSA 5, GG1, <3 针阳性），首选治疗？",
         "answer": "主动监测。EAU 建议低危 PCa（GG1, PSA<10, cT1-2a）首选 AS，定期 PSA+MRI+重复活检。",
         "decision": "ACTIVE_SURVEILLANCE", "confidence": "high",
         "grounding_specs": [("EAU", "6.3.1.2"), ("EAU", "6.3.1.4")],
         "evidence_level": "1a"},
        {"question": "中危 PCa（GG2, PSA 8, cT2a），治疗选择？",
         "answer": "确定性治疗：RP 或 EBRT。favorable intermediate 可选 RP/EBRT/AS；unfavorable 应确定性治疗。",
         "decision": "RP_OR_EBRT", "confidence": "high",
         "grounding_specs": [("EAU", "6.3.2.6"), ("EAU", "6.3.2.3")],
         "evidence_level": "1a"},
        {"question": "高危 PCa（GG4, PSA 25, cT3a），推荐治疗？",
         "answer": "RP+扩大淋巴清扫 或 EBRT+长期 ADT（2-3 年）。多模式治疗。",
         "decision": "RP_EBRT_ADT", "confidence": "high",
         "grounding_specs": [("EAU", "6.3.2.6"), ("EAU", "6.3.3")],
         "evidence_level": "1a"},
        {"question": "局限期高危 PCa + cN1，推荐治疗？",
         "answer": "局部治疗（RP 或 EBRT）联合长期强化激素治疗。EAU 建议 cN1 应局部治疗+长期 ADT。",
         "decision": "LOCAL_PLUS_ADT", "confidence": "high",
         "grounding_specs": [("EAU", "6.3.4.2")],
         "evidence_level": "2a"},
        {"question": "高龄 75 岁+合并症+低危 PCa，预期寿命<10 年，治疗选择？",
         "answer": "观察或 AS。EAU 建议预期寿命<10 年的低危 PCa 可观察（WW）。",
         "decision": "WATCHFUL_WAITING", "confidence": "medium",
         "grounding_specs": [("EAU", "6.3.1.1"), ("EAU", "6.3.1.2")],
         "evidence_level": "3"},
        {"question": "病理 WSI 缺失，T2 治疗决策如何处理？",
         "answer": "无法做 Gleason 分级，应降级为仅基于 MRI+PSA 的粗略风险分层，confidence 降低。",
         "decision": "INSUFFICIENT", "confidence": "high",
         "grounding_specs": [("EAU", "6.3.4.2")],
         "evidence_level": "N/A"},
        # --- 灰区补充 ---
        {"question": "中危 favorably intermediate（GG1 多针, PSA 8, cT2a）能否选 AS？",
         "answer": "可谨慎考虑。favorable intermediate risk 可选 AS，需严格入组：GG1, <50% 针阳性, PSA<10。任何升级即转确定性治疗。",
         "decision": "ACTIVE_SURVEILLANCE", "confidence": "medium",
         "grounding_specs": [("EAU", "6.3.2.6"), ("EAU", "6.3.1.2")],
         "evidence_level": "2a"},
    ]
    return [_qa_entry(f"qa-t2-{i:03d}", "T2", r, records) for i, r in enumerate(rules)]


def build_t3_qa(records):
    rules = [
        {"question": "根治术后 6 周 PSA 不可测（<0.05），BCR 风险？",
         "answer": "术后 PSA 不可测是良好预后指标。5 年 BCR-free rate 86%。建议定期随访 PSA。",
         "decision": "LOW_RISK", "confidence": "high",
         "grounding_specs": [("EAU", "6.4.1"), ("EAU", "7.4")],
         "evidence_level": "2a"},
        {"question": "RP 术后 pT3a + GG4 + 切缘阳性，BCR 风险？",
         "answer": "高危 BCR。EAU 建议考虑辅助 RT 或早期 salvage RT。",
         "decision": "HIGH_RISK", "confidence": "high",
         "grounding_specs": [("EAU", "6.4.5"), ("EAU", "6.4.1")],
         "evidence_level": "1a"},
        {"question": "术后 PSA 持续可测（0.15 ng/mL, 6 周），如何评估？",
         "answer": "PSA persistence。持续可测提示残余病灶或微转移。需评估 PSA 倍增时间、PSMA PET/CT、salvage RT+ADT。",
         "decision": "HIGH_RISK", "confidence": "high",
         "grounding_specs": [("EAU", "6.4.1"), ("EAU", "6.4.4.1.5")],
         "evidence_level": "2a"},
        {"question": "BCR 定义是什么？术后 PSA>=0.2 ng/mL 确认升高。",
         "answer": "EAU/AUA 定义：RP 术后 BCR = 两次连续 PSA>=0.2 ng/mL 确认升高。PSA 倍增时间<12 月提示高危。",
         "decision": "DEFINITION", "confidence": "high",
         "grounding_specs": [("EAU", "6.4.2"), ("EAU", "7.4")],
         "evidence_level": "1a"},
        {"question": "术后 PSA 随访只有 1 个点，能否评估 BCR 风险？",
         "answer": "信息不足。需至少 2-3 个 PSA 点才能计算倍增时间。",
         "decision": "INSUFFICIENT", "confidence": "high",
         "grounding_specs": [("EAU", "6.4.1.1")],
         "evidence_level": "N/A"},
        {"question": "BCR 后 salvage RT 的最佳时机？",
         "answer": "早期 salvage RT（PSA<0.5 时）效果最佳。PSA 越低效果越好。联合短期 ADT 可改善预后。",
         "decision": "EARLY_SALVAGE_RT", "confidence": "high",
         "grounding_specs": [("EAU", "6.4.5.1.1"), ("EAU", "6.4.5.1.2")],
         "evidence_level": "1a"},
        # --- 灰区补充 ---
        {"question": "术后 PSA 0.1 ng/mL（可测但低于 BCR 阈值 0.2），如何处理？",
         "answer": "PSA 可测但未达 BCR 定义。EAU 建议密切随访，缩短 PSA 间隔至 1-3 月。若上升趋势确认且 PSA->0.2，则定义 BCR，考虑早期 salvage RT。",
         "decision": "FOLLOW_UP", "confidence": "high",
         "grounding_specs": [("EAU", "6.4.2"), ("EAU", "6.4.5.1.1")],
         "evidence_level": "2a"},
    ]
    return [_qa_entry(f"qa-t3-{i:03d}", "T3", r, records) for i, r in enumerate(rules)]


def main():
    records = load_records()
    print(f"Loaded {len(records)} normalized guideline records")

    all_qa = build_t1_qa(records) + build_t2_qa(records) + build_t3_qa(records)
    print(f"Total QA pairs: {len(all_qa)}")
    print(f"  T1: {sum(1 for q in all_qa if q['task']=='T1')}")
    print(f"  T2: {sum(1 for q in all_qa if q['task']=='T2')}")
    print(f"  T3: {sum(1 for q in all_qa if q['task']=='T3')}")

    grounded = sum(1 for q in all_qa if q["grounding"])
    print(f"QA with grounding: {grounded}/{len(all_qa)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for q in all_qa:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"Wrote: {OUT}")

    RESOURCES_OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(RESOURCES_OUT, "w", encoding="utf-8") as f:
        for q in all_qa:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"Wrote: {RESOURCES_OUT}")

    print("\n=== Sample ===")
    for q in all_qa[:3]:
        print(f"[{q['id']}] {q['task']} | {q['decision']} | guideline_id={q['guideline_id']} | section={q['section'][:40]}")


if __name__ == "__main__":
    main()
