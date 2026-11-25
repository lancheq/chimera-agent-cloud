"""对 35B 四组输出做结构化打标（关键词 + 简单规则），输出对照表。"""

import json
import re
import sys

path = sys.argv[1] if len(sys.argv) > 1 else "/tmp/llm_abcd.json"
d = json.load(open(path))

PAT = {
    "pias_2": re.compile(r"PI-?RADS\s*(?:category|score|of|:)?\s*2|PI-?RADS\s*[≤<]=?\s*2|PI-?RADS\s*2\b", re.I),
    "pias_3": re.compile(r"PI-?RADS\s*(?:category|score|of|:)?\s*3|PI-?RADS\s*[=:]?\s*3\b", re.I),
    "pis_4plus": re.compile(r"PI-?RADS\s*[≥>]=?\s*4", re.I),
    "biopsy_yes": re.compile(r"\b(biopsy is (?:indicated|recommended|advised)|should (?:undergo|have|proceed with) (?:a )?biopsy|perform (?:a )?biopsy|proceed with biopsy)", re.I),
    "biopsy_no": re.compile(r"\b(omit biopsy|biopsy (?:is )?not (?:indicated|recommended)|defer biopsy|no biopsy|avoid biopsy|withhold biopsy)", re.I),
    "cites_eau": re.compile(r"\bEAU\b", re.I),
    "cites_section": re.compile(r"5\.5\.7|5\.7\.1|section\s*5\."),
    "cond_psad": re.compile(r"PSA[- ]density", re.I),
    "cond_dre": re.compile(r"\bDRE\b|digital rectal", re.I),
    "cond_family": re.compile(r"family history", re.I),
    "unknown_ack": re.compile(r"\b(unknown|not (?:provided|available|documented)|cannot (?:be )?(?:determined|assessed)|insufficient information|would not assume)", re.I),
    "strength_mention": re.compile(r"\b(strong|weak) recommendation\b", re.I),
}

rows = []
for r in d["runs"]:
    t = r["content"]
    row = {"group": r["group"], "rep": r["rep"], "chars": len(t), "seconds": r["seconds"]}
    for k, p in PAT.items():
        row[k] = len(p.findall(t))
    rows.append(row)

cols = ["group", "rep", "chars", "pias_2", "pias_3", "pis_4plus", "biopsy_yes", "biopsy_no",
        "cites_eau", "cites_section", "cond_psad", "cond_dre", "cond_family", "unknown_ack"]
print(" | ".join(cols))
for r in rows:
    print(" | ".join(str(r[c]) for c in cols))

print("\n--- 输出首 400 字（每组 rep0）")
for r in d["runs"]:
    if r["rep"] == 0:
        print(f'\n[{r["group"]}]')
        print(r["content"][:400].replace("\n", " "))

json.dump(rows, open("/tmp/llm_marks.json", "w"), ensure_ascii=False, indent=1)
