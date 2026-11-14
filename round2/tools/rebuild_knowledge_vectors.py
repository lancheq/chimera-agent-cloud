"""Repair inconsistent embeddings in a NEW resource copy, never the original DB.

No diagnostic targets or patient cases are opened. CPU document embeddings only.
Run freeze_knowledge_access afterwards in a separate process, after Chroma exits.
"""
import argparse
import json
from pathlib import Path
import shutil
import time
import numpy as np
from chimera_agent_baseline.knowledge_access import load_manifest, sha, inventory

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--manifest',type=Path,required=True)
p.add_argument('--output',type=Path,required=True)
p.add_argument('--batch-size',type=int,default=8)
a=p.parse_args()
if a.output.exists() or a.batch_size < 1:
    raise SystemExit('Use new output and positive batch size')
m=load_manifest(a.manifest)
a.output.mkdir(parents=True)
resources=a.output/'resources'
shutil.copytree(m['corpus']['path'],resources)
started=time.monotonic()
from sentence_transformers import SentenceTransformer
import chromadb
model=SentenceTransformer(m['encoder']['path'],device='cpu',local_files_only=True)
client=chromadb.PersistentClient(path=str(resources/'guidelines_db'))
collection=client.get_collection('guidelines')
raw=collection.get(include=['documents','metadatas'])
qa=[json.loads(line) for line in (resources/'guideline_qa.jsonl').read_text().splitlines() if line.strip()]
texts=raw['documents']+[r['question'] for r in qa]
vectors=[]
last_report=started
for index in range(0,len(texts),a.batch_size):
    values=model.encode_document(texts[index:index+a.batch_size],batch_size=a.batch_size,show_progress_bar=False).astype('float32')
    if not np.isfinite(values).all():
        raise ValueError('Invalid vector')
    vectors.extend(values)
    now=time.monotonic()
    if index==0 or now-last_report>=30 or index+a.batch_size>=len(texts):
        elapsed=now-started
        completed=len(vectors)
        progress=dict(stage='embedding_repair_not_diagnostic_generation',completed=completed,total=len(texts),
            elapsed_seconds=elapsed,estimated_remaining_seconds=(len(texts)-completed)*elapsed/completed,
            actual_diagnostic_llm_calls=0)
        (a.output/'progress.json').write_text(json.dumps(progress,indent=2))
        print(json.dumps(progress),flush=True)
        last_report=now
matrix=np.array(vectors,dtype='float32')
np.save(a.output/'encoded_vectors.npy',matrix,allow_pickle=False)
for index in range(0,len(raw['ids']),128):
    collection.update(ids=raw['ids'][index:index+128],embeddings=matrix[index:min(index+128,len(raw['ids']))].tolist())
np.save(resources/'guideline_qa.embeddings.npy',matrix[len(raw['ids']):],allow_pickle=False)
result=dict(stage='consistent_document_vector_rebuild',parent_manifest_sha256=sha(a.manifest),
    chunks=len(raw['ids']),qa=len(qa),shape=list(matrix.shape),document_api='SentenceTransformer.encode_document',
    elapsed_seconds=time.monotonic()-started,original_resource_inventory_unchanged=True,
    actual_diagnostic_llm_calls=0,note='Run freeze in separate process; this working copy is not yet an authorized manifest')
load_manifest(a.manifest)
(a.output/'rebuild_summary.json').write_text(json.dumps(result,indent=2)+'\n')
print(json.dumps(result),flush=True)
