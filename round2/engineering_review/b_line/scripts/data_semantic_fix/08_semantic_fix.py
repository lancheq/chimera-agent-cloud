#!/usr/bin/env python3
"""B-DATA-SEMANTIC-FIX · 18 槽审查脚本语义修复（单 CPU worker，零生成）。

三项限定改动：
  1. `psav` 与 PSA 水平分离：原值与字段出处单列，速度/水平语义未证时 UNKNOWN，排除出总 PSA 水平配对。
  2. `enc_date` 单列就诊元数据：不作为采样、观察、历史可得或临床截止。
  3. 同序列按去重后的两个不同观察引用组成无向对：每对一次，无自配，不以同值猜同源。
附带保持：限值符号（<、>、≤、≥）不作精确值参与精确比较。

先跑无病例合成正反例，全过才复跑原 18 槽。
"""
import hashlib
import itertools
import json
import os
import re
import resource
import sys
import time

ROOT = "/home/hedongze/CHIMERA-agent"
BASE = os.path.join(ROOT, "output/b_data_applicability_live_20261122")
OUT = os.path.join(ROOT, "output/b_data_semantic_fix_20261122")
DATA_ROOT = os.path.join(
    ROOT,
    "data/round2_materials_20261121/train_release_transfer/20261121_104011_78d1168e/train_release_chimera_phase2",
)

UNIT_DENSITY = {"ng/ml/ml", "ng/ml^2", "ng/ml2", "ng/ml²"}
NUM_UNIT = re.compile(r"(\d+(?:\.\d+)?)\s*(ng/mL/mL|ng/mL\^2|ng/mL2|ng/mL\s*/\s*mL|ng/mL|ng/dl)", re.I)
UNIT_ONLY = re.compile(r"(ng/mL/mL|ng/mL\^2|ng/mL2|ng/mL|ng/dl)", re.I)
QUAL = re.compile(r"^\s*([<>≤≥=~]+)\s*")
MONTHS = {m: i + 1 for i, m in enumerate(
    ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"])}
P_ISO = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
P_DMY = re.compile(r"\b(\d{1,2})[\s\-/]([A-Za-z]{3,9})\.?[\s\-/](\d{4})\b")
P_MY = re.compile(r"\b([A-Za-z]{3,9})\.?[\s\-/](\d{4})\b")
P_NUM = re.compile(r"\b(\d{1,2})/(\d{4})\b")


class Denied(Exception):
    pass


class Reader:
    """与 05_audit_read_v5.py 同一套白名单与拒绝规则；本类不做语义判断。"""

    def __init__(self, allowlist):
        self.content = {e["abs_path"]: e for e in allowlist["content_read"]}
        self.existence = {e["abs_path"]: e for e in allowlist["existence_only"]}
        self.denied_suffixes = tuple(allowlist["policy"]["denied_suffixes"])
        self.audit = []
        self.bytes_read = 0
        self.opens = 0
        self._seq = 0

    def _log(self, path, action, allowed, reason, nbytes=0):
        self._seq += 1
        self.audit.append({"seq": self._seq, "path": path, "action": action,
                           "allowed": allowed, "reason": reason, "bytes_read": nbytes})

    def _check(self, path):
        base = os.path.basename(path)
        if base.endswith(self.denied_suffixes):
            raise Denied("denied_suffix:%s" % base)
        if ".." in path.split(os.sep):
            raise Denied("traversal_segment")
        real = os.path.realpath(path)
        if real != path:
            raise Denied("non_canonical_path")
        if not real.startswith(os.path.realpath(DATA_ROOT) + os.sep):
            raise Denied("outside_data_root")
        if os.path.islink(path):
            raise Denied("symlink")
        if path not in self.content:
            raise Denied("not_in_allowlist")
        return self.content[path]

    def read_bytes_once(self, path):
        """一次打开同时用于校验与解析。"""
        try:
            entry = self._check(path)
        except Denied as exc:
            self._log(path, "read_bytes", False, str(exc))
            raise
        with open(path, "rb") as fh:
            raw = fh.read()
        self.opens += 1
        self.bytes_read += len(raw)
        got = hashlib.sha256(raw).hexdigest()
        if got != entry["expected_sha256"]:
            self._log(path, "read_bytes", False, "sha256_mismatch:%s" % got)
            raise Denied("sha256_mismatch")
        self._log(path, "read_bytes", True, "allowlist_content_sha_verified", len(raw))
        return raw, entry

    def stat_only(self, path):
        if path not in self.existence:
            self._log(path, "stat", False, "not_in_existence_allowlist")
            raise Denied("not_in_existence_allowlist")
        exists = os.path.isfile(path) and not os.path.islink(path)
        self._log(path, "stat", True, "existence_only_exists=%s" % exists)
        return exists


# ---------------- 语义层 ----------------

def parse_value(raw):
    """返回 (value, qualifier, exact)。限值符号保留，不当作精确值。"""
    if isinstance(raw, (int, float)):
        return float(raw), None, True
    if not isinstance(raw, str):
        return None, None, False
    s = raw.strip().replace(",", "")
    m = QUAL.match(s)
    q = None
    if m:
        q = m.group(1)
        s = s[m.end():]
    m2 = re.match(r"^(\d+(?:\.\d+)?)", s)
    if not m2:
        return None, q, False
    return float(m2.group(1)), q, (q is None)


def date_parts(label):
    if not isinstance(label, str) or not label.strip():
        return {"precision": "none"}
    s = label.strip()
    m = P_ISO.search(s)
    if m:
        return {"y": int(m.group(1)), "m": int(m.group(2)), "d": int(m.group(3)), "precision": "day"}
    m = P_DMY.search(s)
    if m:
        mon = MONTHS.get(m.group(2)[:3].lower())
        if mon:
            return {"y": int(m.group(3)), "m": mon, "d": int(m.group(1)), "precision": "day"}
    m = P_MY.search(s)
    if m:
        mon = MONTHS.get(m.group(1)[:3].lower())
        if mon:
            return {"y": int(m.group(2)), "m": mon, "d": None, "precision": "month"}
    m = P_NUM.search(s)
    if m:
        return {"y": int(m.group(2)), "m": int(m.group(1)), "d": None, "precision": "month"}
    m = re.search(r"\b(19|20)\d{2}\b", s)
    if m:
        return {"y": int(m.group(0)), "m": None, "d": None, "precision": "year"}
    return {"precision": "unknown"}


def split_clauses(text):
    if not isinstance(text, str):
        return []
    return [c for c in re.split(r"[.;\n\r]+|\s{2,}", text) if c.strip()]


def report_serum_obs(text, path_prefix="radiology_report"):
    """只取正文血清 PSA 数值；密度从句与含 free 的从句排除。带限值符号时 exact=False。"""
    obs = []
    frag = {"clauses": 0, "density_clauses": 0, "free_clauses": 0, "unitless_values": 0}
    for i, cl in enumerate(split_clauses(text)):
        frag["clauses"] += 1
        low = cl.lower()
        if "densit" in low:
            frag["density_clauses"] += 1
            continue
        if "psa" not in low:
            continue
        if "free" in low and "total" not in low:
            frag["free_clauses"] += 1
            continue
        hits = 0
        for m in NUM_UNIT.finditer(cl):
            unit = re.sub(r"\s+", "", m.group(2)).lower()
            if unit in UNIT_DENSITY:
                continue
            obs.append({"seq": "report_serum", "ref": "report:%d" % len(obs), "val": float(m.group(1)),
                        "qualifier": None, "exact": True,
                        "date": {"precision": "none"}, "date_role": "narrative_mention",
                        "pointer": "%s[clause:%d]" % (path_prefix, i)})
            hits += 1
        if not hits:
            for m in re.finditer(r"PSA[^0-9\n]{0,25}(\d+(?:\.\d+)?)", cl, re.I):
                obs.append({"seq": "report_serum", "ref": "report:%d" % len(obs), "val": float(m.group(1)),
                            "qualifier": None, "exact": True,
                            "date": {"precision": "none"}, "date_role": "narrative_mention",
                            "pointer": "%s[clause:%d]" % (path_prefix, i)})
                frag["unitless_values"] += 1
    return obs, frag


def build_case(prompt, clinical):
    """把一份病例拆成：序列、psav 单列块、enc_date 单列块、片段统计。"""
    lab = clinical.get("laboratory_results") or []
    trend = clinical.get("psa_trend") or []
    lab_obs, lab_types = [], {"total_psa": 0, "free_psa": 0, "free_psa_pct": 0, "density": 0, "other": 0}
    lab_qual = 0
    for idx, item in enumerate(lab if isinstance(lab, list) else []):
        if not isinstance(item, dict):
            continue
        nm = str(item.get("name", ""))
        low = nm.lower()
        if not re.search(r"psa|prostate specific", low):
            continue
        val, qual, exact = parse_value(item.get("val"))
        if re.search(r"densit", low) or "psad" in low:
            lab_types["density"] += 1
            continue
        if "free" in low and "%" in nm:
            lab_types["free_psa_pct"] += 1
            continue
        if "free" in low:
            lab_types["free_psa"] += 1
            continue
        if re.search(r"ratio|complex|%", low):
            lab_types["other"] += 1
            continue
        lab_types["total_psa"] += 1
        if val is None:
            continue
        if qual:
            lab_qual += 1
        lab_obs.append({"seq": "lab_total", "ref": "lab:%d" % idx, "val": val,
                        "qualifier": qual, "exact": exact,
                        "date": date_parts(item.get("date")), "date_role": "lab_record_date",
                        "pointer": "laboratory_results[%d]" % idx})

    trend_obs = []
    for idx, item in enumerate(trend if isinstance(trend, list) else []):
        if not isinstance(item, dict):
            continue
        val, qual, exact = parse_value(item.get("val"))
        if val is None:
            continue
        trend_obs.append({"seq": "trend", "ref": "trend:%d" % idx, "val": val,
                          "qualifier": qual, "exact": exact,
                          "date": date_parts(item.get("date")), "date_role": "trend_month_label",
                          "pointer": "psa_trend[%d]" % idx})

    rep_obs, rep_frag = report_serum_obs(clinical.get("radiology_report"))

    psav_raw = prompt.get("psav") if isinstance(prompt, dict) else None
    psav_val, psav_qual, psav_exact = parse_value(psav_raw)
    psav_block = {
        "value": psav_val,
        "qualifier": psav_qual,
        "exact": psav_exact,
        "field": "structured-prompt.json:psav" if psav_val is not None else None,
        "months_field_present": isinstance(prompt, dict) and "months" in prompt,
        "semantic": "velocity_or_level_unknown",
        "paired": False,
        "reason": "psav 的填充语义（速度/水平/口径）未绑定；按卡排除出总 PSA 水平配对，状态 UNKNOWN",
    }
    enc_block = {
        "value_present": isinstance(prompt, dict) and bool(prompt.get("enc_date")),
        "field": "structured-prompt.json:enc_date",
        "precision": date_parts(prompt.get("enc_date")) if isinstance(prompt, dict) else {"precision": "none"},
        "role": "encounter_metadata",
        "used_as_observation_date": False,
        "reason": "就诊元数据；不冒充采样、观察、历史可得或临床截止",
    }
    return {
        "sequences": {
            "lab_total": lab_obs,
            "trend": trend_obs,
            "report_serum": rep_obs,
        },
        "lab_types": lab_types,
        "lab_qualified_values": lab_qual,
        "psav": psav_block,
        "enc_date": enc_block,
        "fragments": {"radiology_clauses": rep_frag["clauses"],
                      "density_clauses": rep_frag["density_clauses"],
                      "free_clauses": rep_frag["free_clauses"],
                      "unitless_report_values": rep_frag["unitless_values"]},
    }


def undirected_pairs(case):
    """同序列按两个不同观察引用组成无向对，每对一次；跨序列同样一对象。"""
    seqs = case["sequences"]
    pairs = []
    for name, obs in seqs.items():
        for a, b in itertools.combinations(obs, 2):
            pairs.append({"kind": "within_%s" % name, "a": a, "b": b, "key": tuple(sorted((a["ref"], b["ref"])))})
    names = list(seqs)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            for a in seqs[names[i]]:
                for b in seqs[names[j]]:
                    pairs.append({"kind": "cross_%s_%s" % (names[i], names[j]), "a": a, "b": b,
                                  "key": tuple(sorted((a["ref"], b["ref"])))})
    # 一对象只出现一次（跨序列与同序列键不会重合，仍显式去重）
    seen, uniq = set(), []
    for p in pairs:
        k = (p["kind"], p["key"])
        if k in seen:
            continue
        seen.add(k)
        uniq.append(p)
    return uniq


def date_relation(a, b):
    da, db = a["date"], b["date"]
    pa, pb = da.get("precision"), db.get("precision")
    if pa in ("day", "month") and pb in ("day", "month"):
        if (da.get("y"), da.get("m")) != (db.get("y"), db.get("m")):
            return "separated_month"
        if pa == "day" and pb == "day" and da.get("d") != db.get("d"):
            return "separated_day_same_month"
        if pa == "day" and pb == "day":
            return "same_day_label"
        return "same_month_label"
    return "undated"


def classify_pair(p):
    out = {"kind": p["kind"], "key": list(p["key"]),
           "refs": [{"ref": p["a"]["ref"], "pointer": p["a"]["pointer"], "date_role": p["a"]["date_role"],
                     "qualifier": p["a"]["qualifier"]},
                    {"ref": p["b"]["ref"], "pointer": p["b"]["pointer"], "date_role": p["b"]["date_role"],
                     "qualifier": p["b"]["qualifier"]}]}
    if not (p["a"]["exact"] and p["b"]["exact"]):
        out["exclusion"] = "qualified_value_not_exact"
        out["analyzable"] = False
        return out
    out["exclusion"] = None
    out["analyzable"] = True
    out["value_relation"] = "equal" if abs(p["a"]["val"] - p["b"]["val"]) < 1e-9 else "different"
    out["date_relation"] = date_relation(p["a"], p["b"])
    return out


def legacy_pair_count(case):
    """复刻 05 版启发式：psav 当水平并赋 enc_date，同序列双向成对。用于对齐旧基线。"""
    seq = case["sequences"]
    lab, trend, rep = seq["lab_total"], seq["trend"], seq["report_serum"]
    psav = []
    if case["psav"]["value"] is not None:
        psav = [{"val": case["psav"]["value"], "date": case["enc_date"]["precision"]}]
    def cross(x, y):
        return sum(1 for a in x for b in y if a is not b)
    def within(x):
        return sum(1 for a in x for b in x if a is not b)
    return {
        "psav_vs_lab": cross(psav, lab),
        "psav_vs_trend": cross(psav, trend),
        "lab_vs_trend": cross(lab, trend),
        "lab_internal": within(lab),
        "trend_internal": within(trend),
        "report_vs_lab": cross(rep, lab),
    }


def analyse_case(prompt, clinical):
    case = build_case(prompt, clinical)
    pairs = undirected_pairs(case)
    classified = [classify_pair(p) for p in pairs]
    analyzable = [c for c in classified if c["analyzable"]]
    excluded = [c for c in classified if not c["analyzable"]]
    diff = [c for c in analyzable if c["value_relation"] == "different"]
    equal = [c for c in analyzable if c["value_relation"] == "equal"]
    date_rel = {}
    for c in analyzable:
        date_rel[c["date_relation"]] = date_rel.get(c["date_relation"], 0) + 1

    trend = case["sequences"]["trend"]
    distinct_months = {(o["date"].get("y"), o["date"].get("m")) for o in trend
                       if o["date"].get("precision") in ("day", "month")}
    legacy = legacy_pair_count(case)

    return {
        "case": case,
        "pairs_total": len(classified),
        "pairs_analyzable": len(analyzable),
        "pairs_excluded": len(excluded),
        "pairs_excluded_by_reason": _tally(c["exclusion"] for c in excluded),
        "pairs_differing": len(diff),
        "pairs_equal": len(equal),
        "date_relation_tally": date_rel,
        "same_day_label_pairs": date_rel.get("same_day_label", 0),
        "distinct_orderable_trend_months": len(distinct_months),
        "legacy": legacy,
        "legacy_total": sum(legacy.values()),
        "classified": classified,
    }


def _tally(items):
    out = {}
    for i in items:
        out[i] = out.get(i, 0) + 1
    return out


# ---------------- 合成正反例 ----------------

def synthetic_checks():
    """无病例最小正反例：三项修复 + 限值符号 + 合法正例保留。"""
    checks = []

    def mk(prompt_extra=None, lab=None, trend=None, radiology=None):
        prompt = {"psav": None, "enc_date": None, "months": None}
        prompt.update(prompt_extra or {})
        clinical = {"laboratory_results": lab or [], "psa_trend": trend or [], "radiology_report": radiology or ""}
        return prompt, clinical

    def run(name, prompt, clinical, expect):
        res = analyse_case(prompt, clinical)
        actual, ok = {}, True
        for path, want in expect.items():
            cur = res
            for part in path.split("."):
                cur = cur[int(part)] if part.isdigit() and isinstance(cur, list) else (cur.get(part) if isinstance(cur, dict) else None)
            actual[path] = cur
            if cur != want:
                ok = False
        checks.append({"case": name, "passed": ok, "expected": expect, "actual": actual})

    lab_total = lambda v, d="01 Jan 2025", q=None: {"name": "PSA", "val": (q or "") + str(v), "date": d, "flag": ""}

    # 1 速度/水平：psav + months 存在，不与总 PSA 水平配对
    p, c = mk({"psav": 0.42, "months": 12}, lab=[lab_total(5.2)])
    run("psav_velocity_not_paired_with_level", p, c,
        {"pairs_total": 0, "case.psav.paired": False, "case.psav.semantic": "velocity_or_level_unknown"})

    # 2 enc_date 不作观察日期：psav 有 enc_date，但序列里不出现该日期
    p, c = mk({"psav": 0.42, "enc_date": "15 Feb 2025"}, lab=[lab_total(5.2, "01 Jan 2025")])
    res = analyse_case(p, c)
    enc_used = any(o_ref["date_role"] == "encounter_metadata"
                   for cl in res["classified"] for o_ref in cl["refs"])
    checks.append({"case": "enc_date_not_observation_date", "passed": (enc_used is False and res["pairs_total"] == 0),
                   "expected": {"enc_date_in_pairs": False, "pairs_total": 0},
                   "actual": {"enc_date_in_pairs": enc_used, "pairs_total": res["pairs_total"]}})

    # 3 输入反序只计一次
    p, c = mk(lab=[lab_total(5.2, "01 Jan 2025"), lab_total(6.1, "01 Mar 2025")])
    run("reversed_order_single_pair", p, c, {"pairs_total": 1})

    # 4 自配禁止
    p, c = mk(lab=[lab_total(5.2)])
    run("self_pair_forbidden", p, c, {"pairs_total": 0})

    # 5 同值不推断同源：两个同值不同月份仍是两条观察、一对
    p, c = mk(lab=[lab_total(5.2, "01 Jan 2025"), lab_total(5.2, "01 Mar 2025")])
    run("equal_values_not_merged", p, c, {"pairs_total": 1, "pairs_equal": 1, "pairs_differing": 0})

    # 6 同日标签但观察身份未证：只记同日标签，不升级为冲突（两侧均日精度）
    p, c = mk(lab=[lab_total(5.2, "15 Feb 2025"), lab_total(6.0, "15 Feb 2025")])
    run("same_day_label_uncertified", p, c,
        {"same_day_label_pairs": 1, "pairs_differing": 1})
    # 6b 一侧只有月精度：记为同月标签，不冒充同日
    p, c = mk(lab=[lab_total(5.2, "15 Feb 2025")], trend=[{"date": "Feb 2025", "val": 6.0}])
    run("month_precision_not_same_day", p, c,
        {"date_relation_tally.same_month_label": 1, "same_day_label_pairs": 0})

    # 7 合法已知日期要保留为可分析对
    p, c = mk(lab=[lab_total(5.2, "15 Feb 2025")], trend=[{"date": "Jan 2025", "val": 6.0}])
    run("legitimate_known_dates_retained", p, c,
        {"pairs_analyzable": 1, "pairs_differing": 1})

    # 8 限值符号保留：< 不作精确值
    p, c = mk(lab=[lab_total(0.1, "01 Jan 2025", q="<")], trend=[{"date": "Jan 2025", "val": 0.1}])
    res = analyse_case(p, c)
    qual_kept = res["case"]["sequences"]["lab_total"][0]["qualifier"] == "<"
    checks.append({"case": "limit_symbol_not_exact", "passed": (qual_kept and res["pairs_excluded"] == 1),
                   "expected": {"qualifier_kept": True, "pairs_excluded": 1},
                   "actual": {"qualifier_kept": qual_kept, "pairs_excluded": res["pairs_excluded"]}})

    # 9 free PSA 不进水平序列
    p, c = mk(lab=[lab_total(5.2), {"name": "Free PSA", "val": "0.9", "date": "01 Jan 2025", "flag": ""}])
    run("free_psa_excluded_from_level", p, c,
        {"case.lab_types.total_psa": 1, "case.lab_types.free_psa": 1, "pairs_total": 0})

    # 10 缺截止：不产生任何获证时间判定
    p, c = mk(lab=[lab_total(5.2, "15 Feb 2025")])
    res = analyse_case(p, c)
    checks.append({"case": "no_cutoff_stays_unknown",
                   "passed": res["case"]["enc_date"]["used_as_observation_date"] is False,
                   "expected": {"used_as_observation_date": False},
                   "actual": {"used_as_observation_date": res["case"]["enc_date"]["used_as_observation_date"]}})

    return checks


# ---------------- 主流程 ----------------

def main():
    t0 = time.perf_counter()
    os.makedirs(os.path.join(OUT, "scripts"), exist_ok=True)
    frame = json.load(open(os.path.join(BASE, "risk_group_frame.json"), encoding="utf-8"))
    allowlist = json.load(open(os.path.join(BASE, "read_allowlist.json"), encoding="utf-8"))
    sealed = json.load(open(os.path.join(BASE, "aggregate_counts.json"), encoding="utf-8"))

    checks = synthetic_checks()
    ok = all(c["passed"] for c in checks)
    with open(os.path.join(OUT, "synthetic_checks.json"), "w", encoding="utf-8") as fh:
        json.dump({"all_passed": ok, "count": len(checks), "checks": checks}, fh, ensure_ascii=False, indent=1)
    if not ok:
        print("SYNTHETIC_FAILED")
        for c in checks:
            if not c["passed"]:
                print("  FAIL", c["case"], c["expected"], c["actual"])
        return 2
    print("synthetic", len(checks), "passed")

    reader = Reader(allowlist)
    deltas = []
    per_task = {1: [], 2: [], 3: []}

    for slot in frame["slots"]:
        sid, task = slot["slot_id"], slot["task"]
        p_path = next(e["abs_path"] for e in allowlist["content_read"] if e["slot_id"] == sid and e["role"] == "initial_panel")
        c_path = next(e["abs_path"] for e in allowlist["content_read"] if e["slot_id"] == sid and e["role"] == "clinical_input")
        notes = []
        try:
            raw, _ = reader.read_bytes_once(p_path)
            prompt = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            prompt, notes = {}, ["initial_panel_failed:%s" % type(exc).__name__]
        try:
            raw, _ = reader.read_bytes_once(c_path)
            clinical = json.loads(raw)
        except Exception as exc:  # noqa: BLE001
            clinical, notes = {}, ["clinical_input_failed:%s" % type(exc).__name__]
        ex_path = next(e["abs_path"] for e in allowlist["existence_only"] if e["slot_id"] == sid)
        try:
            rep_present = reader.stat_only(ex_path)
        except Denied:
            rep_present = False

        res = analyse_case(prompt, clinical)
        old = sealed["tasks"]["task%d" % task]
        old_diff = old["comparison_ledger"].get("same_day_diff", 0) + old["comparison_ledger"].get("same_month_diff", 0) \
            + old["comparison_ledger"].get("separated_diff", 0) + old["comparison_ledger"].get("undated_diff", 0)
        delta = {
            "slot_id": sid,
            "task": task,
            "risk_group_id": slot["risk_group_id"],
            "representation_present": rep_present,
            "legacy_pair_counts": res["legacy"],
            "legacy_pair_total": res["legacy_total"],
            "new_pairs_total": res["pairs_total"],
            "new_pairs_analyzable": res["pairs_analyzable"],
            "new_pairs_excluded": res["pairs_excluded"],
            "new_pairs_excluded_by_reason": res["pairs_excluded_by_reason"],
            "new_pairs_differing": res["pairs_differing"],
            "new_pairs_equal": res["pairs_equal"],
            "date_relation_tally": res["date_relation_tally"],
            "same_day_label_pairs": res["same_day_label_pairs"],
            "distinct_orderable_trend_months": res["distinct_orderable_trend_months"],
            "psav": res["case"]["psav"],
            "enc_date": res["case"]["enc_date"],
            "lab_types": res["case"]["lab_types"],
            "fragments": res["case"]["fragments"],
            "first_mismatch_stage": {
                "cross_source_or_time_value_difference": "observation_typing:psav_as_level_and_date_role",
                "certified_distinct_eligible_observation": "date_role:enc_date_as_observation",
                "certified_same_encounter_unresolved_conflict": "date_role:enc_date_as_observation",
                "misread_unit_duplicate_restatement": "comparison:limit_symbol_stripped",
                "confirmed_late_and_model_reachable": "date_role:no_certified_cutoff",
                "eligible_comparable_longitudinal": "comparison:counted_date_rows_not_distinct_observations",
            },
            "legacy_same_day_from_sealed_total": old_diff,
            "categories_after": {
                "cross_source_or_time_value_difference": "CONFIRMED" if res["pairs_differing"] else
                ("EXCLUDED" if res["pairs_analyzable"] and not res["pairs_differing"] else "UNKNOWN"),
                "certified_distinct_eligible_observation": "UNKNOWN",
                "certified_same_encounter_unresolved_conflict": "UNKNOWN",
                "misread_unit_duplicate_restatement": "CONFIRMED" if (res["pairs_excluded_by_reason"].get("qualified_value_not_exact")
                                                                      or res["case"]["lab_types"]["free_psa"]
                                                                      or res["case"]["fragments"]["unitless_report_values"]) else "UNKNOWN",
                "confirmed_late_and_model_reachable": "UNKNOWN",
                "eligible_comparable_longitudinal": "UNKNOWN",
            },
            "notes": notes,
        }
        deltas.append(delta)
        per_task[task].append(delta)

    with open(os.path.join(OUT, "semantic_delta.jsonl"), "w", encoding="utf-8") as fh:
        for d in deltas:
            fh.write(json.dumps(d, ensure_ascii=False) + "\n")
    with open(os.path.join(OUT, "file_access_audit.jsonl"), "w", encoding="utf-8") as fh:
        for row in reader.audit:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")

    # 前后对照
    before_after = {"batch_id": frame["batch_id"], "seed": frame["seed"],
                    "baseline_label": "旧启发式运算回执，仅作被审查基线，不是有效可比率",
                    "before_source": {"aggregate_counts.json": sealed["provenance"]["case_findings_sha256"]},
                    "tasks": {}, "totals": {}}
    for task in (1, 2, 3):
        rows = per_task[task]
        old = sealed["tasks"]["task%d" % task]
        old_ledger = old["comparison_ledger"]
        old_cmp = sum(old_ledger.values())
        old_diff = (old_ledger.get("same_day_diff", 0) + old_ledger.get("same_month_diff", 0)
                    + old_ledger.get("separated_diff", 0) + old_ledger.get("undated_diff", 0))
        new_total = sum(r["new_pairs_total"] for r in rows)
        new_an = sum(r["new_pairs_analyzable"] for r in rows)
        new_diff = sum(r["new_pairs_differing"] for r in rows)
        exc = {}
        for r in rows:
            for k, v in r["new_pairs_excluded_by_reason"].items():
                exc[k] = exc.get(k, 0) + v
        before_after["tasks"]["task%d" % task] = {
            "records": len(rows),
            "before": {
                "directional_pair_operations": old_cmp,
                "differing_operations": old_diff,
                "sealed_ratio": [old_diff, old_cmp],
                "same_day_differing": old_ledger.get("same_day_diff", 0),
                "included_psav_pairs": True,
                "directional_duplicates": True,
                "limit_symbol_handling": "stripped_and_treated_as_exact",
            },
            "after": {
                "undirected_unique_pairs": new_total,
                "analyzable_pairs": new_an,
                "excluded_pairs": sum(r["new_pairs_excluded"] for r in rows),
                "excluded_by_reason": exc,
                "differing_pairs": new_diff,
                "equal_pairs": sum(r["new_pairs_equal"] for r in rows),
                "ratio": [new_diff, new_an] if new_an else None,
                "same_day_label_pairs": sum(r["same_day_label_pairs"] for r in rows),
                "psav_pairs": 0,
                "directional_duplicates": 0,
            },
            "delta": {
                "pair_universe": new_total - old_cmp,
                "differing": new_diff - old_diff,
                "direction": "decrease" if new_diff < old_diff else ("increase" if new_diff > old_diff else "unchanged"),
            },
        }
    before_after["totals"] = {
        "before_directional_operations": sum(t["before"]["directional_pair_operations"] for t in before_after["tasks"].values()),
        "before_differing_operations": sum(t["before"]["differing_operations"] for t in before_after["tasks"].values()),
        "after_undirected_pairs": sum(t["after"]["undirected_unique_pairs"] for t in before_after["tasks"].values()),
        "after_analyzable": sum(t["after"]["analyzable_pairs"] for t in before_after["tasks"].values()),
        "after_differing": sum(t["after"]["differing_pairs"] for t in before_after["tasks"].values()),
    }
    before_after["resources"] = {
        "wall_seconds": round(time.perf_counter() - t0, 3),
        "cpu_seconds": round(sum(resource.getrusage(resource.RUSAGE_SELF)[:2]), 3),
        "peak_rss_mib": round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024, 1),
        "file_opens": reader.opens,
        "bytes_read": reader.bytes_read,
        "model_requests": 0, "gpu_seconds": 0, "clinical_tool_calls": 0,
    }
    before_after["access"] = {
        "audit_entries": len(reader.audit),
        "denied": sum(1 for r in reader.audit if not r["allowed"]),
        "content_reads": sum(1 for r in reader.audit if r["action"] == "read_bytes" and r["allowed"]),
        "existence_checks": sum(1 for r in reader.audit if r["action"] == "stat" and r["allowed"]),
        "gt_reads": 0, "reference_reasoning_reads": 0, "outcome_reads": 0,
    }
    before_after["provenance"] = {
        "frame_sha256": frame["frame_sha256"],
        "allowlist_sha256": hashlib.sha256(open(os.path.join(BASE, "read_allowlist.json"), "rb").read()).hexdigest(),
        "sealed_aggregate_sha256": hashlib.sha256(open(os.path.join(BASE, "aggregate_counts.json"), "rb").read()).hexdigest(),
        "fix_script_sha256": hashlib.sha256(open(os.path.abspath(__file__), "rb").read()).hexdigest(),
        "semantic_delta_sha256": hashlib.sha256(open(os.path.join(OUT, "semantic_delta.jsonl"), "rb").read()).hexdigest(),
        "synthetic_checks_sha256": hashlib.sha256(open(os.path.join(OUT, "synthetic_checks.json"), "rb").read()).hexdigest(),
    }
    with open(os.path.join(OUT, "before_after_counts.json"), "w", encoding="utf-8") as fh:
        json.dump(before_after, fh, ensure_ascii=False, indent=1)

    print("wall", before_after["resources"]["wall_seconds"], "rss_mib", before_after["resources"]["peak_rss_mib"])
    for t, v in before_after["tasks"].items():
        print(t, "before", v["before"]["differing_operations"], "/", v["before"]["directional_pair_operations"],
              "-> after", v["after"]["differing_pairs"], "/", v["after"]["analyzable_pairs"],
              "excluded", v["after"]["excluded_pairs"], "same_day", v["after"]["same_day_label_pairs"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
