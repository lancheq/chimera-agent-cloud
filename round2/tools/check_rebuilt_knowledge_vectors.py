"""Independently compare every written vector and unchanged guideline record."""
import argparse
import json
from pathlib import Path
import shutil
import tempfile
import numpy as np
from chimera_agent_baseline.knowledge_access import load_manifest, sha

p=argparse.ArgumentParser(description=__doc__)
p.add_argument('--parent-manifest',type=Path,required=True)
p.add_argument('--rebuild',type=Path,required=True)
a=p.parse_args()
parent=load_manifest(a.parent_manifest)
import chromadb
with tempfile.TemporaryDirectory(prefix='chimera-vector-reference-') as tmp:
    shutil.copytree(Path(parent['corpus']['path'])/'guidelines_db',Path(tmp)/'db')
    old=chromadb.PersistentClient(path=str(Path(tmp)/'db')).get_collection('guidelines').get(include=['documents','metadatas'])
    new=chromadb.PersistentClient(path=str(a.rebuild/'resources/guidelines_db')).get_collection('guidelines').get(include=['documents','metadatas','embeddings'])
    matrix=np.load(a.rebuild/'encoded_vectors.npy',allow_pickle=False)
    expected={cid:(doc,meta,matrix[i]) for i,(cid,doc,meta) in enumerate(zip(old['ids'],old['documents'],old['metadatas']))}
    assert set(expected)==set(new['ids'])
    for cid,doc,meta,vec in zip(new['ids'],new['documents'],new['metadatas'],new['embeddings']):
        before=expected[cid]
        assert before[0]==doc and before[1]==meta
        assert np.allclose(before[2],vec,rtol=0,atol=1e-7)
    qa=np.load(a.rebuild/'resources/guideline_qa.embeddings.npy',allow_pickle=False)
    assert np.array_equal(qa,matrix[len(old['ids']):])
    result=dict(stage='all_rebuilt_vector_write_verification',passed=True,chunks=len(old['ids']),qa=len(qa),
        matrix_sha256=sha(a.rebuild/'encoded_vectors.npy'),parent_manifest_sha256=sha(a.parent_manifest),
        all_records_and_metadata_unchanged=True,all_vectors_match=True,actual_diagnostic_llm_calls=0)
    (a.rebuild/'full_vector_write_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result),flush=True)
load_manifest(a.parent_manifest)
