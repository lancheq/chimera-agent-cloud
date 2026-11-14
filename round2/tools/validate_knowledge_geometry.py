"""Check stored document vectors against the pinned local encoder, not labels."""
import argparse
import json
from pathlib import Path
import time
import sys
import numpy as np
from chimera_agent_baseline.knowledge_access import create_search, sha, load_manifest, alignment_binding

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--manifest',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--authorize',action='store_true')
a=p.parse_args()
t=time.monotonic()
manifest=load_manifest(a.manifest)
s=create_search(a.manifest,sha(a.manifest),_engineering_audit=True)
raw=s._collection.get(include=['documents','embeddings','metadatas'])
indices=[0,len(raw['documents'])//2,len(raw['documents'])-1]
texts=[raw['documents'][i] for i in indices]
old=np.array([raw['embeddings'][i] for i in indices])
new=s._encoder.encode_document(texts,show_progress_bar=False)
cos=lambda x,y:(x*y).sum(axis=1)/(np.linalg.norm(x,axis=1)*np.linalg.norm(y,axis=1))
chunk_cos=cos(old,new).tolist()
qa_indices=[0,len(s._qa)//2,len(s._qa)-1]
old=s._qa_vecs[qa_indices]
new=s._encoder.encode_document([s._qa[i]['question'] for i in qa_indices],show_progress_bar=False)
qa_cos=cos(old,new).tolist()
result={'manifest_sha256_before_authorization':sha(a.manifest),'resource_binding':alignment_binding(manifest),'chunk_sample_indices':indices,'chunk_cosines':chunk_cos,
        'qa_sample_indices':qa_indices,'qa_cosines':qa_cos,'elapsed_seconds':time.monotonic()-t,
        'criterion':'each deterministic sampled cosine >=0.999; this is a compatibility screen, not proof for every vector',
        'passed':all(v>=0.999 for v in chunk_cos+qa_cos),'actual_llm_calls':0}
a.output.parent.mkdir(parents=True,exist_ok=True)
a.output.write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result))

if not result['passed']:
    sys.exit(1)
if a.authorize:
    manifest['alignment_evidence']={'resource_binding':alignment_binding(manifest),
        'report_path':str(a.output.resolve()),'report_sha256':sha(a.output)}
    a.manifest.write_text(json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n')
    load_manifest(a.manifest,require_alignment=True)
    print('authorized_manifest_sha256='+sha(a.manifest),flush=True)
