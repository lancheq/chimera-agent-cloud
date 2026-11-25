"""04_extract_qa.py
从归一化指南中提取临床决策 QA 对。

每条 QA:
{
  "id": str,
  "task": "T1" | "T2" | "T3",
  "question": str,
  "answer": str,
  "decision": str,  # 简短决策标签
  "confidence": "high" | "medium" | "low",
  "section_id": str,          # 主 grounding 章节 id
  "guideline_id": str,        # "<SOURCE>-<section_id>"
  "section": str,             # 主 grounding 章节标题
  "grounding": [
    {"source": "EAU", "section_id": "5.4.2.2", "section_title": "...", "page": 36},
    ...
  ],
  "evidence_level": str  # EAU LE: 1a/1b/2a/2b/3/4
}

两种模式：
1. 手工规则模式（默认）：构造 20 条种子 QA（与历史产出一致）。
2. API 模式（--api）：用 DeepSeek-V4-Flash（blsc OpenAI 兼容 API）从
   guidelines_unified.jsonl 的临床章节批量抽取 QA，带断点续跑
   （checkpoint: guideline_dataset/qa/guideline_qa_api.jsonl），
   完成后与 resources/guideline_qa.jsonl（现库 24 条）合并回写，
   使 QA 库从 24 条扩充到 150+ 条。

用法：
    python guideline_dataset/scripts/04_extract_qa.py          # 手工种子
    python guideline_dataset/scripts/04_extract_qa.py --api    # API 扩充
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
NORM = ROOT / "guideline_dataset" / "normalized" / "guidelines_unified.jsonl"
OUT = ROOT / "guideline_dataset" / "qa" / "guideline_qa.jsonl"
QA_API_OUT = ROOT / "guideline_dataset" / "qa" / "guideline_qa_api.jsonl"
QA_MASTER = ROOT / "resources" / "guideline_qa.jsonl"

# API 模式配置
BASE_URL = os.environ.get("LLMAPI_BASE_URL", "https://llmapi.blsc.cn/v1")
TEACHER_MODEL = os.environ.get("QA_TEACHER_MODEL", "DeepSeek-V4-Flash")
API_TARGET = 140  # 新增 QA 目标条数（24 现有 + 140 ≈ 164 ≥ 150）
SECTION_MIN_CHARS = 300
EAU_CLINICAL_PREFIXES = ("5", "6", "7")
SECTION_CHARS_LIMIT = 3200  # 送入教师模型的章节文本上限

DECISION_ENUMS = {
    "T1": ["YES", "NO", "FOLLOW_UP", "INSUFFICIENT"],
    "T2": [
        "ACTIVE_SURVEILLANCE", "WATCHFUL_WAITING", "RP", "EBRT", "RP_OR_EBRT",
        "RP_EBRT_ADT", "ADT", "CHEMO", "LOCAL_PLUS_ADT", "INSUFFICIENT",
    ],
    "T3": [
        "LOW_RISK", "HIGH_RISK", "EARLY_SALVAGE_RT", "FOLLOW_UP",
        "DEFINITION", "INSUFFICIENT",
    ],
}

TEACHER_PROMPT = """你是前列腺癌临床指南专家。基于下方指南章节内容，生成 1 条临床决策 QA 对，用于指南检索库（section grounding）。

要求：
1. question：真实灰区临床场景（含具体数值，如 PSA、PI-RADS、PSAd、GG 分级、cT 分期、术后 PSA 趋势等），风格与下方示例一致
2. answer：依据本章内容给出简明决策依据（2-4 句，中文，可引用关键数值/推荐等级）
3. task 按内容判定：T1=活检决策（PSA/MRI/PSAd/活检指征）；T2=治疗方案选择（局限期/进展期治疗策略）；T3=BCR 风险评估与随访（术后 PSA、salvage RT、随访方案）
4. decision 必须从允许值中选：{decisions}
5. evidence_level：章节中若给出推荐等级/证据等级（如 LE: 1a-4），照抄；没有则填 "N/A"
6. 只输出 JSON，不要 markdown 代码块，格式：
{{"task": "...", "question": "...", "answer": "...", "decision": "...", "confidence": "high|medium|low", "evidence_level": "..."}}

章节信息：{source} {section_id} {section_title} (p{page})

章节内容：
{content}"""


def load_records():
    with open(NORM, encoding="utf-8") as f:
        return [json.loads(l) for l in f]


def find_section(records, source, section_id):
    for r in records:
        if r["source"] == source and r["section_id"] == section_id:
            return r
    return None


def make_grounding(records, specs):
    """specs: [(source, section_id), ...]"""
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


def build_t1_qa(records):
    """T1 活检决策 QA"""

    # 基于 EAU 5.4.2.2 PSA Density + PI-RADS 矩阵
    t1_rules = [
        {
            "question": "PI-RADS 5 病灶 + PSA 12 ng/mL + PSAd 0.25 ng/mL/cc，是否推荐活检？",
            "answer": "是，强烈推荐活检。PI-RADS 5 提示 csPCa 可能性很高，结合 PSAd>0.20 显著升高风险。EAU 建议对此类患者行靶向活检联合系统活检。",
            "decision": "YES",
            "confidence": "high",
            "grounding_specs": [("EAU", "5.4.2.2"), ("EAU", "5.6.4"), ("PIRADS", "PIRADS-0")],
            "evidence_level": "1a",
        },
        {
            "question": "PI-RADS 4 病灶 + PSA 8 ng/mL + PSAd 0.12 ng/mL/cc，是否推荐活检？",
            "answer": "是，推荐活检。PI-RADS 4 提示 csPCa 可能性高。EAU 建议 PI-RADS≥4 时推荐活检，PSAd 0.10-0.15 为中等风险区但仍建议活检。",
            "decision": "YES",
            "confidence": "high",
            "grounding_specs": [("EAU", "5.4.2.2"), ("EAU", "5.6.4")],
            "evidence_level": "2a",
        },
        {
            "question": "PI-RADS 3 病灶 + PSA 6 ng/mL + PSAd 0.18 ng/mL/cc，是否推荐活检？",
            "answer": "是，推荐活检。PI-RADS 3 为中等不确定，但 PSAd>0.15 提示风险升高（ISUP GG≥2 风险可达 27-40%）。EAU 建议 PI-RADS 3 + PSAd>0.15 时考虑活检。",
            "decision": "YES",
            "confidence": "medium",
            "grounding_specs": [("EAU", "5.4.2.2")],
            "evidence_level": "2a",
        },
        {
            "question": "PI-RADS 3 病灶 + PSA 5 ng/mL + PSAd 0.08 ng/mL/cc，是否推荐活检？",
            "answer": "可暂缓活检。PI-RADS 3 + PSAd<0.10 时 NPV 升至 89-94%（biopsy-naive），csPCa 风险低。EAU 建议可考虑短期随访 MRI 或系统活检。AUA 同样建议风险足够低时可暂缓。",
            "decision": "NO",
            "confidence": "medium",
            "grounding_specs": [("EAU", "5.4.2.2"), ("AUA", "AUA-3")],
            "evidence_level": "2a",
        },
        {
            "question": "PI-RADS 1-2 + PSA 4 ng/mL + PSAd 0.06，是否推荐活检？",
            "answer": "不推荐活检。PI-RADS 1-2 提示 csPCa 可能性低，PSAd<0.10 进一步支持低风险。EAU 建议暂缓活检，定期随访 PSA。",
            "decision": "NO",
            "confidence": "high",
            "grounding_specs": [("EAU", "5.4.2.2"), ("PIRADS", "PIRADS-0")],
            "evidence_level": "2a",
        },
        {
            "question": "MRI 阴性（PI-RADS 1）+ PSAd 0.22 ng/mL/cc，是否推荐活检？",
            "answer": "是，考虑活检。虽然 MRI 阴性，但 PSAd>0.20 时 ISUP GG≥2 风险为 27-40%。EAU 建议对 PSAd 高且 MRI 阴性者考虑系统活检。",
            "decision": "YES",
            "confidence": "medium",
            "grounding_specs": [("EAU", "5.4.2.2")],
            "evidence_level": "2a",
        },
        {
            "question": "既往阴性活检 + 新发 PSA 升高（从 4 到 7 ng/mL），下一步如何处理？",
            "answer": "不应仅因一次阴性活检就停止筛查。应重新评估：1) 重复 PSA 确认升高；2) 行前列腺 MRI（若既往未行）；3) 使用含阴性活检保护效应的风险计算器；4) MRI 可疑则靶向+系统活检，MRI 阴性则系统活检。",
            "decision": "FOLLOW_UP",
            "confidence": "high",
            "grounding_specs": [("AUA", "AUA-3")],
            "evidence_level": "2a",
        },
        {
            "question": "DWI 序列缺失，仅有 T2W，PI-RADS 评分应如何处理？",
            "answer": "PI-RADS 评分不可靠。DWI 是 PZ 主导序列，缺失时无法准确评分 PZ 病变。应请求补充 DWI 序列或降级置信度，bpMRI（无 DCE）可接受但无 DWI 不可。",
            "decision": "INSUFFICIENT",
            "confidence": "high",
            "grounding_specs": [("PIRADS", "PIRADS-2")],
            "evidence_level": "N/A",
        },
    ]

    qa = []
    for i, rule in enumerate(t1_rules):
        qa.append({
            "id": f"qa-t1-{i:03d}",
            "task": "T1",
            "question": rule["question"],
            "answer": rule["answer"],
            "decision": rule["decision"],
            "confidence": rule["confidence"],
            "grounding": make_grounding(records, rule["grounding_specs"]),
            "evidence_level": rule["evidence_level"],
        })

    return qa


def build_t2_qa(records):
    """T2 治疗决策 QA（四选一）"""
    qa = []

    t2_rules = [
        {
            "question": "低危前列腺癌（cT1c, PSA 5, GG1, <3 针阳性），首选治疗策略？",
            "answer": "主动监测（Active Surveillance）。EAU 建议低危 PCa（ISUP GG1, PSA<10, cT1-2a）首选 AS，定期 PSA + MRI + 重复活检。仅当患者选择或进展时转确定性治疗。",
            "decision": "ACTIVE_SURVEILLANCE",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.3.1")],
            "evidence_level": "1a",
        },
        {
            "question": "中危前列腺癌（GG2, PSA 8, cT2a），治疗选择？",
            "answer": "确定性治疗：RP 或 EBRT 均可。EAU 建议中危（favorable intermediate）可选 RP/EBRT/AS；unfavorable intermediate（GG2 多针/PSA>10）应确定性治疗。考虑患者偏好与预期寿命。",
            "decision": "RP_OR_EBRT",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.3.2")],
            "evidence_level": "1a",
        },
        {
            "question": "高危前列腺癌（GG4, PSA 25, cT3a），推荐治疗？",
            "answer": "RP + 扩大淋巴清扫 或 EBRT + 长期 ADT（2-3 年）。EAU 建议高危局部 PCa 行多模式治疗：RP（含 ePLND）或 EBRT+ADT，部分需术后辅助 RT。考虑盆腔淋巴清扫。",
            "decision": "RP_EBRT_ADT",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.3.2"), ("EAU", "6.3")],
            "evidence_level": "1a",
        },
        {
            "question": "局限期高危 PCa + cN1（盆腔淋巴转移），推荐治疗？",
            "answer": "局部治疗（RP 或 EBRT）联合长期强化激素治疗。EAU 建议 cN1 PCa 应接受局部治疗 + 长期 ADT，而非仅观察。",
            "decision": "LOCAL_PLUS_ADT",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.3")],
            "evidence_level": "2a",
        },
        {
            "question": "高龄（75 岁）+ 多种合并症 + 低危 PCa，预期寿命<10 年，治疗选择？",
            "answer": "观察或 AS。EAU 建议预期寿命<10 年的低危 PCa 可观察（WW），不推荐确定性治疗。",
            "decision": "WATCHFUL_WAITING",
            "confidence": "medium",
            "grounding_specs": [("EAU", "6.1"), ("EAU", "6.3.1")],
            "evidence_level": "3",
        },
        {
            "question": "活检病理 WSI 缺失（pathology_embedding=[]），T2 治疗决策如何处理？",
            "answer": "无法做 Gleason 分级，T2 治疗决策应降级为仅基于 MRI + PSA 的粗略风险分层。应标注信息不足，confidence 降低，不可强行选确定性治疗。",
            "decision": "INSUFFICIENT",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.3")],
            "evidence_level": "N/A",
        },
    ]

    for i, rule in enumerate(t2_rules):
        qa.append({
            "id": f"qa-t2-{i:03d}",
            "task": "T2",
            "question": rule["question"],
            "answer": rule["answer"],
            "decision": rule["decision"],
            "confidence": rule["confidence"],
            "grounding": make_grounding(records, rule["grounding_specs"]),
            "evidence_level": rule["evidence_level"],
        })

    return qa


def build_t3_qa(records):
    """T3 BCR 风险预测 QA"""
    qa = []

    t3_rules = [
        {
            "question": "根治术后 6 周 PSA 不可测（<0.05），BCR 风险评估？",
            "answer": "术后 PSA 不可测是良好预后指标。UCSF 数据显示 5 年 BCR-free rate 86%（vs 67% 可测组）。建议定期随访 PSA，前 2 年每 3-6 月，之后每年。",
            "decision": "LOW_RISK",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.4.1"), ("EAU", "7.4")],
            "evidence_level": "2a",
        },
        {
            "question": "RP 术后病理 pT3a + GG4 + 切缘阳性，BCR 风险？",
            "answer": "高危 BCR。pT3 + GG4 + 切缘阳性显著升高 BCR 风险。EAU 建议此类患者考虑辅助 RT（尤其切缘阳性/ pT3）。但 AUA/ASTRO 建议不常规辅助 RT，倾向早期 salvage RT。",
            "decision": "HIGH_RISK",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.4.5"), ("EAU", "6.4.1")],
            "evidence_level": "1a",
        },
        {
            "question": "术后 PSA 持续可测（0.15 ng/mL，6 周时），如何评估？",
            "answer": "PSA persistence。术后 6-12 周 PSA 应不可测，持续可测提示残余病灶或微转移。需评估：PSA 倍增时间、影像学（PSMA PET/CT 优先）、考虑 salvage RT + ADT。",
            "decision": "HIGH_RISK",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.4.1"), ("EAU", "6.4.4.1.5")],
            "evidence_level": "2a",
        },
        {
            "question": "BCR 定义是什么？术后 PSA ≥0.2 ng/mL 确认升高。",
            "answer": "EAU/AUA 定义：RP 术后 BCR = 两次连续 PSA ≥0.2 ng/mL 确认升高。未复发、挽救治疗、迁移、死亡或失访视为删失（censored）。PSA 倍增时间<12 月提示高危。",
            "decision": "DEFINITION",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.4.2"), ("EAU", "7.4")],
            "evidence_level": "1a",
        },
        {
            "question": "根治术后 PSA 随访趋势数据只有 1 个点，能否评估 BCR 风险？",
            "answer": "信息不足。需至少 2-3 个 PSA 点（间隔 3-4 月）才能计算倍增时间。应调用 get_psa_trend 工具获取完整序列，不可仅凭单点判断。",
            "decision": "INSUFFICIENT",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.4.1.1")],
            "evidence_level": "N/A",
        },
        {
            "question": "BCR 后 salvage RT 的最佳时机？",
            "answer": "早期 salvage RT（PSA<0.5 ng/mL 时）效果最佳。EAU 建议在 PSA 达到可测阈值后尽早开始 salvage RT，PSA 越低效果越好。联合短期 ADT 可改善预后。",
            "decision": "EARLY_SALVAGE_RT",
            "confidence": "high",
            "grounding_specs": [("EAU", "6.4.5.1.1"), ("EAU", "6.4.5.1.2")],
            "evidence_level": "1a",
        },
    ]

    for i, rule in enumerate(t3_rules):
        qa.append({
            "id": f"qa-t3-{i:03d}",
            "task": "T3",
            "question": rule["question"],
            "answer": rule["answer"],
            "decision": rule["decision"],
            "confidence": rule["confidence"],
            "grounding": make_grounding(records, rule["grounding_specs"]),
            "evidence_level": rule["evidence_level"],
        })

    return qa


# ---------------------------------------------------------------------------
# API 模式：DeepSeek-V4-Flash 从指南章节批量抽取 QA
# ---------------------------------------------------------------------------

_load_env_done = False


def _load_dotenv() -> None:
    """Load environment variables from .env (development convenience)."""
    global _load_env_done
    if _load_env_done:
        return
    _load_env_done = True
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def get_client():
    _load_dotenv()
    from openai import OpenAI

    api_key = os.environ.get("LLMAPI_KEY")
    if not api_key:
        sys.exit("ERROR: LLMAPI_KEY not set. Export it or add to .env")
    return OpenAI(api_key=api_key, base_url=BASE_URL, max_retries=3, timeout=180)


def select_sections(records, target: int) -> list[dict]:
    """选取临床相关、内容充足的章节作为 QA 抽取素材（按指南结构顺序）。"""
    seen = set()
    selected = []
    for r in records:
        sid = str(r.get("section_id") or "").strip()
        src = r.get("source")
        txt = r.get("text", "")
        if not sid or sid in seen or len(txt) < SECTION_MIN_CHARS:
            continue
        if src == "EAU" and not sid.startswith(EAU_CLINICAL_PREFIXES):
            continue
        if src not in ("EAU", "AUA", "PIRADS", "NCCN"):
            continue
        seen.add(sid)
        selected.append(r)
        if len(selected) >= target:
            break
    return selected


def parse_teacher_json(raw: str) -> dict | None:
    """Parse the teacher's JSON output, tolerating markdown fences/noise."""
    raw = raw.strip()
    m = re.search(r"\{.*\}", raw, re.DOTALL)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None


def generate_qa_for_section(client, section: dict) -> dict | None:
    """Call the teacher model to generate one QA pair for a section."""
    src = section.get("source", "")
    sid = str(section.get("section_id") or "")
    title = str(section.get("section_title") or "")
    page = section.get("page")
    content = section.get("text", "")[:SECTION_CHARS_LIMIT]

    prompt = TEACHER_PROMPT.format(
        decisions="/".join(DECISION_ENUMS["T1"] + DECISION_ENUMS["T2"] + DECISION_ENUMS["T3"]),
        source=src,
        section_id=sid,
        section_title=title,
        page=page,
        content=content,
    )

    for attempt in range(2):
        try:
            resp = client.chat.completions.create(
                model=TEACHER_MODEL,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.3,
                max_tokens=4096,
                # DeepSeek-V4-Flash is a thinking model; disable reasoning so
                # the token budget goes to the JSON answer itself.
                extra_body={"reasoning_effort": "none"},
            )
            raw = resp.choices[0].message.content or ""
            d = parse_teacher_json(raw)
            if not d:
                continue
            task = d.get("task", "")
            if task not in ("T1", "T2", "T3"):
                continue
            question = str(d.get("question", "")).strip()
            answer = str(d.get("answer", "")).strip()
            decision = str(d.get("decision", "")).strip().upper()
            confidence = str(d.get("confidence", "medium")).strip().lower()
            if confidence not in ("high", "medium", "low"):
                confidence = "medium"
            evidence_level = str(d.get("evidence_level", "N/A")).strip()
            if not question or not answer or not decision:
                continue
            grounding = [{
                "source": src,
                "section_id": sid,
                "section_title": title,
                "page": page,
            }]
            return {
                "id": f"qa-api-{task.lower()}-",  # id 在合并阶段统一编号
                "task": task,
                "question": question,
                "answer": answer,
                "decision": decision,
                "confidence": confidence,
                "section_id": sid,
                "guideline_id": f"{src}-{sid}",
                "section": title,
                "grounding": grounding,
                "evidence_level": evidence_level,
            }
        except Exception as e:
            print(f"    API error ({attempt + 1}/2): {e}", flush=True)
            time.sleep(3)
    return None


def load_existing_api_qa() -> list[dict]:
    """Load checkpoint file (resumable)."""
    if not QA_API_OUT.exists():
        return []
    out = []
    with open(QA_API_OUT, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out


def run_api_mode(target: int):
    records = load_records()
    print(f"Loaded {len(records)} normalized guideline records")

    sections = select_sections(records, target * 2)  # 留冗余：失败章节可顺延补足
    print(f"Eligible sections: {len(sections)} (target {target} new QA)")

    done_sections = {q.get("section_id") for q in load_existing_api_qa()}
    pending = [s for s in sections if str(s.get("section_id")) not in done_sections]
    print(f"Resumable: {len(done_sections)} sections already done, {len(pending)} pending")

    client = get_client()
    print(f"Teacher: {TEACHER_MODEL} @ {BASE_URL}\n")

    out_f = open(QA_API_OUT, "a", encoding="utf-8")
    new_count = len(done_sections)
    for i, sec in enumerate(pending):
        if new_count >= target:
            break
        qa = generate_qa_for_section(client, sec)
        label = f"[{i + 1}/{len(pending)}] {sec.get('source')} {sec.get('section_id')}"
        if qa is None:
            print(f"{label} SKIP (no valid QA)", flush=True)
        else:
            out_f.write(json.dumps(qa, ensure_ascii=False) + "\n")
            out_f.flush()
            new_count += 1
            print(f"{label} OK {qa['task']}/{qa['decision']} | {qa['question'][:60]}", flush=True)
        time.sleep(0.3)
    out_f.close()

    merge_api_qa()
    print_qa_stats(QA_MASTER)


def merge_api_qa():
    """现有 QA 库（resources/guideline_qa.jsonl）+ API 新增 → 合并回写。"""
    existing = []
    if QA_MASTER.exists():
        with open(QA_MASTER, encoding="utf-8") as f:
            existing = [json.loads(l) for l in f if l.strip()]

    api_qa = load_existing_api_qa()
    seen_questions = {q.get("question") for q in existing}
    merged = list(existing)
    for q in api_qa:
        if q.get("question") in seen_questions:
            continue
        seen_questions.add(q.get("question"))
        merged.append(q)

    # 统一重编 id：qa-api-<task>-<nnn>
    counters: dict[str, int] = {}
    for q in merged:
        if q.get("id", "").startswith("qa-api-"):
            task = q.get("task", "T0").lower()
            counters[task] = counters.get(task, 0) + 1
            q["id"] = f"qa-api-{task}-{counters[task]:03d}"

    QA_MASTER.parent.mkdir(parents=True, exist_ok=True)
    with open(QA_MASTER, "w", encoding="utf-8") as f:
        for q in merged:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"\nMerged QA library written to {QA_MASTER} ({len(merged)} pairs)")


def print_qa_stats(path: Path):
    qa = []
    with open(path, encoding="utf-8") as f:
        qa = [json.loads(l) for l in f if l.strip()]
    from collections import Counter

    by_task = Counter(q.get("task", "?") for q in qa)
    by_src = Counter(
        (q.get("grounding") or [{}])[0].get("source", "?") if q.get("grounding") else "?"
        for q in qa
    )
    grounded = sum(1 for q in qa if q.get("grounding"))
    with_section_id = sum(1 for q in qa if q.get("section_id"))
    with_evidence = sum(1 for q in qa if q.get("evidence_level") and q.get("evidence_level") != "N/A")
    print(f"\n=== QA 库统计 ({path.name}) ===")
    print(f"Total: {len(qa)} | by task: {dict(by_task)} | by source: {dict(by_src)}")
    print(f"grounded: {grounded}/{len(qa)} | section_id: {with_section_id} | evidence_level: {with_evidence}")


def main():
    parser = argparse.ArgumentParser(description="Extract clinical decision QA from guidelines")
    parser.add_argument("--api", action="store_true", help="Generate QA via DeepSeek-V4-Flash API")
    parser.add_argument("--target", type=int, default=API_TARGET, help="Number of new QA pairs to generate")
    args = parser.parse_args()

    if args.api:
        run_api_mode(args.target)
        return

    records = load_records()
    print(f"Loaded {len(records)} normalized guideline records")

    all_qa = []
    all_qa.extend(build_t1_qa(records))
    all_qa.extend(build_t2_qa(records))
    all_qa.extend(build_t3_qa(records))

    print(f"\nTotal QA pairs: {len(all_qa)}")
    print(f"  T1 (biopsy): {sum(1 for q in all_qa if q['task']=='T1')}")
    print(f"  T2 (treatment): {sum(1 for q in all_qa if q['task']=='T2')}")
    print(f"  T3 (BCR): {sum(1 for q in all_qa if q['task']=='T3')}")

    # 检查 grounding 命中率
    grounded = sum(1 for q in all_qa if q["grounding"])
    print(f"\nQA with grounding: {grounded}/{len(all_qa)}")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        for q in all_qa:
            f.write(json.dumps(q, ensure_ascii=False) + "\n")
    print(f"\nWrote: {OUT}")

    # 打印样本
    print("\n=== 样本 QA ===")
    for q in all_qa[:3]:
        print(f"\n[{q['id']}] {q['task']} | {q['decision']} | conf={q['confidence']}")
        print(f"  Q: {q['question'][:100]}")
        print(f"  A: {q['answer'][:120]}")
        print(f"  Grounding: {len(q['grounding'])} sections")
        for g in q["grounding"]:
            print(f"    - {g['source']} {g['section_id']} p{g['page']} | {g['section_title'][:50]}")


if __name__ == "__main__":
    main()
