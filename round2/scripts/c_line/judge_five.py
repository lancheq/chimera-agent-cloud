import json
import re

FILES = [
    ("Q01 562159dc", "/tmp/llm_abcd.json", "/tmp/llm_q01_E.json"),
    ("Q03 4f33b025", "/tmp/llm_4f33.json", None),
    ("Q05 c39e94a1", "/tmp/llm_c39e.json", None),
]

DEC = re.compile(
    r"[^.]{0,80}(?:biopsy (?:is|would be|should be) (?:indicated|not indicated|recommended|deferred|performed)"
    r"|(?:repeat|confirmatory|systematic|targeted) biopsy (?:is|should)[^.]{0,40}"
    r"|omit biopsy|defer biopsy|no biopsy|proceed with[^.]{0,30}biopsy"
    r"|surveillance|monitoring)[^.]{0,160}\.", re.I)
FAM = re.compile(r"[^.]{0,60}family history[^.]{0,160}\.", re.I)

for label, base, extra in FILES:
    runs = []
    d = json.load(open(base))
    runs += d["runs"]
    if extra:
        runs += json.load(open(extra))["runs"]
    print("\n" + "#" * 20, label)
    seen = {}
    for r in runs:
        t = re.sub(r"\s+", " ", r["content"])
        key = r["group"]
        dec = DEC.findall(t)
        fam = FAM.findall(t)
        print(f'\n-- {key} rep{r["rep"]} ({r["chars"]} chars)')
        # 只打印含 biopsy/surveillance 判断的句子，最多 2 条
        picks = [m.group(0).strip() for m in DEC.finditer(t)][:2]
        for p in picks:
            print("   DEC:", p[:210])
        if fam:
            print("   FAM:", fam[0].strip()[:190])
