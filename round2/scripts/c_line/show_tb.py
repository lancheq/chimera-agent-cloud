import json

d = json.load(open("/tmp/title_boost.json"))
for q in ("Q01", "Q03", "Q05", "Q06"):
    print("===", q)
    for name in ("base", "boost", "boost_text"):
        v = d[q].get(name)
        if not v:
            print("  ", name, "MISSING")
            continue
        print("  %-11s" % name, v["top5_sections"])
        for w, x in v["watch"].items():
            print("      %-12s dense=%s bm25=%s rrf=%s rerank=%s"
                  % (w, x["dense"], x["bm25"], x["rrf"], x["rerank"]))
