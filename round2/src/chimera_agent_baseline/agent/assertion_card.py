"""派生断言卡（AssertionCard）—— 把决策门控事实与阈值判定在程序侧算完。

动机（实测）：`Type: Re-evaluation - prior PCa diagnosis` 与 `Prior biopsy: Positive`
本来就在初始消息里，LLM 仍然 27/27 判错 ⇒ 光把字段放进去没用，必须把**推导**也做掉。

四条硬纪律：
  1. 全程不调用任何 LLM（否则偏见换形式回流）；
  2. 顺序固定、输出确定（同输入同输出，可 diff、可复现）；
  3. 每条断言都带 `src:` 出处（可审计、可回溯到原始字段）；
  4. 末尾必须声明「原始记录优先」并提示模型主动报告冲突 ——
     防止断言卡把错误的推断变成不可质疑的前提。

本模块是纯函数，无副作用、不读盘（调用方传 dict）。
"""

from __future__ import annotations

from typing import Any

PSA_STRONG = 10.0      # EAU：PSA ≥ 10 属强指征区
PSAD_STRONG = 0.15     # PSAD ≥ 0.15 提示临床显著癌风险升高
PIRADS_STRONG = 4      # PI-RADS ≥ 4 为高风险

_TRUTHY = ("yes", "true", "1", "positive")


def _num(v: Any) -> float | None:
    """宽容转 float；不可转或非有限返回 None。"""
    if v is None:
        return None
    s = str(v).strip()
    if s == "" or s.lower() in ("none", "na", "n/a", "nan", "null"):
        return None
    try:
        f = float(s)
    except (TypeError, ValueError):
        return None
    if f != f or f in (float("inf"), float("-inf")):
        return None
    return f


def _is_truthy(v: Any) -> bool:
    return str(v or "").strip().lower() in _TRUTHY


def _flag(value: bool, src: str) -> dict[str, str]:
    return {"value": "TRUE" if value else "FALSE", "src": src}


def build_assertions(sp: dict, cd: dict, task: int) -> dict[str, Any]:
    """返回 {'text': str, 'items': [...], 'codes': [...]}。

    sp = structured-prompt.json 的内容；cd = *-clinical-data.json 的内容。
    """
    enc_type = str(sp.get("enc_type") or "").strip()
    bx = str(sp.get("bx") or "").strip()
    psa = _num(sp.get("psa"))
    psad = _num(sp.get("psad"))
    pirads = _num(sp.get("pirads"))
    bx_isup = _num(sp.get("bx_isup"))
    bx_gp, bx_gs = _num(sp.get("bx_gl_prim")), _num(sp.get("bx_gl_sec"))
    at_flag = sp.get("active_treatment_flag")
    isup_path = _num(sp.get("path_isup"))

    prior_dx = bx.lower() == "positive"
    prior_bx_negative = bx.lower() == "negative"
    re_eval = "re-evaluation" in enc_type.lower()
    graded = bx_isup is not None or bx_gp is not None

    hit_psa = psa is not None and psa >= PSA_STRONG
    hit_psad = psad is not None and psad >= PSAD_STRONG
    hit_pirads = pirads is not None and pirads >= PIRADS_STRONG

    items: list[dict[str, str]] = []
    codes: list[str] = []

    def add(group: str, code: str, line: str, src: str) -> None:
        items.append({"group": group, "code": code, "line": line, "src": src})
        codes.append(code)

    # ---- ENCOUNTER ----
    add("ENCOUNTER", "encounter_type",
        f"encounter type = {enc_type or 'unknown'}",
        "structured-prompt.enc_type")

    # ---- PRIOR DX ----
    if prior_dx or prior_bx_negative or graded:
        if prior_dx:
            add("PRIOR DX", "prior_prostate_cancer_diagnosis",
                "prior prostate-cancer diagnosis = TRUE (a prior biopsy already returned POSITIVE)",
                "structured-prompt.bx=Positive")
        else:
            add("PRIOR DX", "prior_prostate_cancer_diagnosis",
                "prior prostate-cancer diagnosis = FALSE (prior biopsy was NEGATIVE or absent)",
                f"structured-prompt.bx={bx or 'absent'}")
        if graded:
            gl = ""
            if bx_gp is not None and bx_gs is not None:
                gl = f" · Gleason {int(bx_gp)}+{int(bx_gs)}"
            lvl = f" · ISUP grade group {int(bx_isup)}" if bx_isup is not None else ""
            add("PRIOR DX", "prior_biopsy_graded",
                f"prior biopsy is graded{lvl}{gl}",
                "structured-prompt.bx_isup / bx_gl_prim / bx_gl_sec")
        if isup_path is not None:
            add("PRIOR DX", "pathology_grade_group",
                f"pathology ISUP grade group = {int(isup_path)}",
                "structured-prompt.path_isup")

    # ---- THRESHOLDS ----
    thr: list[str] = []
    if hit_psa:
        thr.append(f"PSA {psa:g} ≥ {PSA_STRONG:g}")
    if hit_pirads:
        thr.append(f"PI-RADS {int(pirads)} ≥ {PIRADS_STRONG}")
    if hit_psad:
        thr.append(f"PSAD {psad:.3g} ≥ {PSAD_STRONG}")
    if thr:
        add("THRESHOLDS", "thresholds_hit",
            "guideline thresholds met: " + " ; ".join(thr)
            + "  (these justify the diagnostic step ONLY IF it has not already been performed)",
            "structured-prompt.psa / pirads / psad")
    else:
        add("THRESHOLDS", "thresholds_hit",
            "no strong guideline threshold met on PSA / PI-RADS / PSAD",
            "structured-prompt.psa / pirads / psad")

    # ---- CONSEQUENCE（仅 task1：活检决策）----
    if task == 1:
        if prior_dx:
            add("CONSEQUENCE", "biopsy_indicated",
                "consequence: the patient ALREADY has a prostate-cancer diagnosis ⇒ a further "
                "biopsy is NOT the indicated next step; the pathway continues at staging / "
                "treatment planning",
                "derived from prior_prostate_cancer_diagnosis")
        else:
            add("CONSEQUENCE", "biopsy_indicated",
                "consequence: no prior positive diagnosis on record ⇒ the biopsy question is "
                "genuinely open and the thresholds above apply",
                "derived from prior_prostate_cancer_diagnosis")
        if prior_dx and thr:
            add("CONSEQUENCE", "biopsy_indicated_contrast",
                "note: the elevated markers above and this consequence are NOT contradictory — "
                "the marker level is high, but the diagnostic step those markers would justify "
                "has already been carried out",
                "derived")

    # ---- STAGING / TREATMENT ----
    if prior_dx:
        if at_flag is None or str(at_flag).strip() == "":
            add("NEXT STEP", "treatment_status_unknown",
                "treatment status is NOT recorded in the record (active_treatment_flag absent) "
                "⇒ staging / treatment planning cannot be confirmed from structured fields alone",
                "structured-prompt.active_treatment_flag")
        else:
            add("NEXT STEP", "active_treatment_flag",
                f"active_treatment_flag = {int(_num(at_flag) or 0)}",
                "structured-prompt.active_treatment_flag")

    # ---- UNKNOWN ----
    for name, val in (("bx_isup", bx_isup), ("active_treatment_flag", at_flag),
                      ("cores_positive", sp.get("cores_positive"))):
        if val is None or str(val).strip() == "":
            add("UNKNOWN", f"missing_{name}", f"{name} is not populated in the record",
                f"structured-prompt.{name}")

    # ---- 渲染 ----
    order = ["ENCOUNTER", "PRIOR DX", "THRESHOLDS", "CONSEQUENCE", "NEXT STEP", "UNKNOWN"]
    lines = ["ASSERTION CARD (mechanically derived from the record — deterministic, no LLM)"]
    for grp in order:
        rows = [it for it in items if it["group"] == grp]
        if not rows:
            continue
        lines.append("")
        for it in rows:
            lines.append(f"[{grp}] {it['line']}")
            lines.append(f"          src: {it['src']}")
    lines.append("")
    lines.append("[NOTE] The assertions above are mechanically derived. If any of them conflicts")
    lines.append("       with the raw record or the evidence you retrieve, THE RAW RECORD WINS —")
    lines.append("       state the conflict explicitly in your answer instead of following the card.")
    return {"text": "\n".join(lines), "items": items, "codes": codes}


def implication(codes: list[str]) -> str:
    """把断言码归约成对 T1 决策的含义，便于离线统计精度/覆盖率。"""
    if "prior_prostate_cancer_diagnosis" in codes:
        # 仅 bx=Positive 时才判定"不再活检"
        return "no_biopsy"
    if "thresholds_hit" in codes:
        return "biopsy"
    return "unclear"
