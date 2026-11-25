import json, re, sys, collections
sys.path.insert(0, "/home/hedongze/CHIMERA-agent/src")
import chromadb

RES = "/home/hedongze/CHIMERA-agent/resources"
cl = chromadb.PersistentClient(path=RES + "/guidelines_db")
col = cl.get_collection("guidelines")
all_data = col.get(include=["documents", "metadatas"])
print("total", len(all_data["ids"]), collections.Counter(re.sub(r"\d+", "#", i) for i in all_data["ids"]).most_common(5))

want = {"5.4.2.2", "6.6.8", "6.4.8", "5.1.3", "5.5.7", "7.3.1"}
out = []
for cid, doc, meta in zip(all_data["ids"], all_data["documents"], all_data["metadatas"]):
    sec = (meta.get("section") or "").strip()
    m = re.match(r"^(\d+(?:\.\d+)*)", sec)
    if not m or m.group(1) not in want:
        continue
    out.append({
        "id": cid,
        "sec": m.group(1),
        "section": sec,
        "page": meta.get("page"),
        "len": len(doc),
        "hdr2026": "MARCH 2026" in doc,
        "hdr2025": "MARCH 2025" in doc,
        "text": doc[:700],
    })
json.dump(out, open("/tmp/sec_chunks.json", "w"), ensure_ascii=False, indent=1)
print("picked", len(out), collections.Counter(o["sec"] for o in out).most_common())
