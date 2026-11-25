#!/usr/bin/env python3
"""集B 步骤8 前置：构建 QLoRA SFT 数据集 data/sft/train.jsonl。

输入：
  - GT:   data/task{1,2,3}/agent_input/<case>/prostate-*-decision*.json（91+72+73=236）
  - Pseudo: data/pseudo_labels_v2.json（--include-pseudo 时加入）

输出（llamafactory sharegpt 格式，每行一条）：
  {"messages": [{"role": "system", "content": ...},
                {"role": "user", "content": 病例上下文},
                {"role": "assistant", "content": 目标结构化 JSON}]}

assistant 目标含决策映射所需的结构化字段（decision/confidence/
variable_weights/reveal_sequence/event/months）加 free_text（推理链），
为微调备完整推理链 SFT 数据。
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = PROJECT_ROOT / "data"

GT_DECISION_FILES = {
    1: "prostate-biopsy-decision.json",
    2: "prostate-treatment-decision.json",
    3: "prostate-time-to-recurrence-or-last-follow-up.json",
}
GT_REASONING_FILES = {
    1: "prostate-biopsy-decision-reasoning.json",
    2: "prostate-treatment-decision-reasoning.json",
    3: None,
}

SYSTEM_PROMPTS = {
    1: "你是泌尿科医生助手。根据前列腺癌临床数据判断是否建议前列腺穿刺活检，"
       "输出 JSON：biopsy_decision(yes/no)、confidence(clear/borderline/uncertain)、"
       "variable_weights(各变量 decisive/important/noted/not_used)、reveal_sequence(证据揭示顺序)。",
    2: "你是泌尿科医生助手。根据前列腺癌临床数据推荐治疗方案，"
       "输出 JSON：treatment_recommendation.primary(active_surveillance/continued_surveillance/active_treatment)、"
       "confidence、variable_weights、reveal_sequence。",
    3: "你是泌尿科医生助手。根据前列腺癌术后临床数据预测生化复发，"
       "输出 JSON：event(0/1)、months_to_recurrence(月数, event=0 时为随访时长)、reveal_sequence。",
}


def load_json(path: Path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def case_context(case_dir: Path, task: int) -> str:
    """拼接病例上下文：structured-prompt 概要 + clinical-data 关键报告。"""
    parts: list[str] = []
    sp_path = case_dir / "structured-prompt.json"
    if sp_path.exists():
        sp = load_json(sp_path)
        keep = {k: v for k, v in sp.items() if k not in ("pmhx", "allergies", "vitals")}
        parts.append("[结构化指标] " + json.dumps(keep, ensure_ascii=False))
    cd_path = case_dir / "prostate-biopsy-decision-clinical-data.json"
    if not cd_path.exists():
        cd_path = case_dir / "prostate-treatment-decision-clinical-data.json"
    if not cd_path.exists():
        cd_path = case_dir / "prostate-time-to-recurrence-or-last-follow-up-clinical-data.json"
    if cd_path.exists():
        cd = load_json(cd_path)
        for k in ("radiology_report", "previous_notes", "laboratory_results", "psa_trend", "family_history"):
            v = cd.get(k)
            if v:
                parts.append(f"[{k}] " + json.dumps(v, ensure_ascii=False)[:1500])
    return "\n".join(parts) if parts else case_dir.name


def target_output(case_dir: Path, task: int, pseudo: dict | None) -> dict | None:
    """构造 assistant 目标 JSON；无 GT/pseudo 数据时返回 None。

    输出含 free_text（推理链），为微调备完整推理链 SFT 数据。
    """
    if pseudo is not None:
        dec = pseudo.get("decision")
        if task == 1:
            return {
                "biopsy_decision": dec,
                "confidence": pseudo.get("confidence"),
                "variable_weights": pseudo.get("variable_weights", {}),
                "free_text": pseudo.get("free_text", ""),
            }
        if task == 2:
            return {
                "treatment_recommendation": {"primary": dec},
                "confidence": pseudo.get("confidence"),
                "variable_weights": pseudo.get("variable_weights", {}),
                "free_text": pseudo.get("free_text", ""),
            }
        return {
            "event": dec.get("event"),
            "months_to_recurrence": dec.get("months_to_recurrence"),
            "free_text": pseudo.get("free_text", ""),
        }

    dec_path = case_dir / GT_DECISION_FILES[task]
    if not dec_path.exists():
        return None
    dec = load_json(dec_path)
    rsn = None
    rsn_rel = GT_REASONING_FILES[task]
    if rsn_rel:
        rsn_path = case_dir / rsn_rel
        rsn = load_json(rsn_path) if rsn_path.exists() else None

    if task == 1:
        out = {"biopsy_decision": dec}
    elif task == 2:
        out = {"treatment_recommendation": {"primary": dec}}
    else:
        out = {
            "event": int(dec.get("event", 0)),
            "months_to_recurrence": dec.get("months_to_recurrence"),
        }
    if rsn:
        out["confidence"] = rsn.get("confidence")
        out["variable_weights"] = rsn.get("variable_weights", {})
        if rsn.get("reveal_sequence"):
            out["reveal_sequence"] = rsn["reveal_sequence"]
        out["free_text"] = rsn.get("free_text", "")
    elif task == 3:
        # T3 无 reasoning 文件：从决策数据生成 free_text
        event = int(dec.get("event", 0))
        months = dec.get("months_to_recurrence")
        out["free_text"] = (
            f"Biochemical recurrence {'occurred' if event else 'not observed'} "
            f"at {months} months follow-up."
        )
    else:
        out.setdefault("free_text", "")
    return out


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out", default=str(DATA_DIR / "sft" / "train.jsonl"))
    ap.add_argument("--include-pseudo", action="store_true", default=True,
                    help="加入 data/pseudo_labels_v2.json（默认开启）")
    ap.add_argument("--no-pseudo", dest="include_pseudo", action="store_false")
    args = ap.parse_args()

    pseudo = {}
    if args.include_pseudo:
        pl_path = DATA_DIR / "pseudo_labels_v2.json"
        if pl_path.exists():
            pseudo = load_json(pl_path)
            print(f"[pseudo] task1={len(pseudo.get('task1', []))} "
                  f"task2={len(pseudo.get('task2', []))} task3={len(pseudo.get('task3', []))}")
        else:
            print("[pseudo] pseudo_labels.json 不存在，仅用 GT")

    out_path = Path(args.out)
    out_path.parent.mkdir(parents=True, exist_ok=True)

    n_written = 0
    stats = {1: 0, 2: 0, 3: 0}
    seen = set()
    with open(out_path, "w", encoding="utf-8") as fout:
        for task in (1, 2, 3):
            root = DATA_DIR / f"task{task}" / "agent_input"
            if not root.is_dir():
                continue
            # 先 GT，再 pseudo（GT 优先，pseudo 同 case 跳过）
            gt_cases = sorted(c for c in os.listdir(root)
                              if (root / c / GT_DECISION_FILES[task]).exists())
            for case_id in gt_cases:
                tgt = target_output(root / case_id, task, pseudo=None)
                if tgt is None:
                    continue
                rec = {"messages": [
                    {"role": "system", "content": SYSTEM_PROMPTS[task]},
                    {"role": "user", "content": f"Task {task} | Case {case_id}\n"
                                                + case_context(root / case_id, task)},
                    {"role": "assistant", "content": json.dumps(tgt, ensure_ascii=False)},
                ]}
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_written += 1
                stats[task] += 1
                seen.add((task, case_id))
            for pc in pseudo.get(f"task{task}", []):
                cid = pc.get("case_id")
                if not cid or (task, cid) in seen:
                    continue
                cdir = root / cid
                if not cdir.is_dir():
                    continue
                tgt = target_output(cdir, task, pseudo=pc)
                if tgt is None:
                    continue
                rec = {"messages": [
                    {"role": "system", "content": SYSTEM_PROMPTS[task]},
                    {"role": "user", "content": f"Task {task} | Case {cid}\n"
                                                + case_context(cdir, task)},
                    {"role": "assistant", "content": json.dumps(tgt, ensure_ascii=False)},
                ]}
                fout.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n_written += 1
                stats[task] += 1
                seen.add((task, cid))

    print(f"[done] wrote {n_written} samples -> {out_path}")
    print(f"[stats] T1={stats[1]} T2={stats[2]} T3={stats[3]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
