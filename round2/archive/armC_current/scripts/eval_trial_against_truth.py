#!/usr/bin/env python3
import argparse, importlib.util, json
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HELPER = ROOT / "scripts" / "eval_against_truth.py"
spec = importlib.util.spec_from_file_location("eat", HELPER)
eat = importlib.util.module_from_spec(spec)
spec.loader.exec_module(eat)


def load_preds_trial(out_root: Path, task: int) -> dict[str, dict]:
    task_dir = out_root / f"task{task}"
    results = {}
    if not task_dir.exists():
        return results
    for case_dir in sorted(task_dir.iterdir()):
        if not case_dir.is_dir():
            continue
        pf = case_dir / "prediction.json"
        if pf.exists():
            results[case_dir.name] = json.loads(pf.read_text())
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--task", type=int, choices=[1, 2, 3])
    parser.add_argument("--out-root", default="test/output.trial_20260822")
    args = parser.parse_args()
    out_root = ROOT / args.out_root
    tasks = [args.task] if args.task else [1, 2, 3]
    for task in tasks:
        gt = eat.load_ground_truth(task)
        pred = load_preds_trial(out_root, task)
        if task == 1:
            eat.eval_task1(gt, pred)
        elif task == 2:
            eat.eval_task2(gt, pred)
        else:
            eat.eval_task3(gt, pred)


if __name__ == "__main__":
    main()
