"""C02 real stdio MCP + existing CPU retrieval + scripted final consumer.

All clinical records are synthetic. No patient/GT reads or generation API calls.
"""
import argparse
import asyncio
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import time
import urllib.request

from langchain_core.messages import AIMessage, HumanMessage
from omegaconf import OmegaConf
from chimera_agent_baseline import run as runner
from chimera_agent_baseline.evidence_package import file_sha
from chimera_agent_baseline.information_access import canonical, digest
from chimera_agent_baseline.agent.evidence_features import decode_payload
from tools.validate_guarded_runner import fixture_config, ScriptedModel

ROOT=Path(__file__).resolve().parents[1]
QUERY='MRI negative PI-RADS 2 PSA density <0.20 negative DRE no family history omit biopsy PSA monitoring'


class PackageModel(ScriptedModel):
    def invoke(self,messages):
        human=next(m.content for m in messages if isinstance(m,HumanMessage))
        if str(messages[0].content).startswith('You are filling out') or 'Produce a 3-5 step' in human:
            return super().invoke(messages)
        self.inputs.append(deepcopy(messages))
        turn=self.turns.get('A',0);self.turns['A']=turn+1
        actions=[('get_mri_report',{'case_id':'A'}),('search_guidelines',{'query':QUERY}),('search_guidelines',{'query':QUERY})]
        if turn<len(actions):
            name,args=actions[turn]
            return AIMessage(content='',tool_calls=[dict(id='c02-'+str(turn),name=name,args=args,type='tool_call')])
        return AIMessage(content='Synthetic software fixture completed. Conditional primary-source references were received; no patient action is inferred from this engineering test.')


async def validate(root,overlay,enabled=True):
    cfg,data=fixture_config(root,'react');cfg.agent.pids=['A']
    cfg.agent.information_access.knowledge_manifest=str(ROOT/'output/s2_knowledge_snapshot_20261113_v4/manifest.json')
    if enabled:
        cfg.agent.information_access.evidence_package={'overlay_path':str(overlay.resolve()),
            'overlay_sha256':file_sha(overlay),'max_consumer_bytes':24000,
            'tokenizer':{'base_url':'http://127.0.0.1:8765','model_id':'Qwen3.6-35B-A3B-UD-Q4_K_M.gguf','max_evidence_tokens':8000}}
    prompt=data/'A/prompt.json';p=json.loads(prompt.read_text());p.update(biopsy_context='initial',dre='negative',family_history_prostate_cancer=False);prompt.write_text(json.dumps(p))
    clinical=data/'A/prostate-biopsy-decision-clinical-data.json';p=json.loads(clinical.read_text());p['radiology_report']='IMPRESSION:\nPI-RADS 2.\nPSA density: 0.08 ng/mL/mL.';clinical.write_text(json.dumps(p))
    os.environ.update(CHIMERA_OUTPUT_DIR=cfg.paths.output_dir,CHIMERA_STRICT_RUNTIME='1',HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',CUDA_VISIBLE_DEVICES='')
    model=PackageModel();start=time.monotonic();count=await runner._run_task(cfg,1,data,model,'C02 synthetic engineering')
    assert count==1,'Actual runner did not complete synthetic case'
    case=Path(cfg.paths.output_dir)/'task1/A';trace=json.loads((case/'trace.json').read_text())
    tool_messages=[x for x in trace['messages'] if x.get('name')=='search_guidelines'];assert len(tool_messages)==2
    payloads=[decode_payload(x['content']) for x in tool_messages];assert payloads[0]==payloads[1]
    value=payloads[0];assert value['results'],'A source-positive case must not be all blocked'
    snapshot=json.loads((case/'information_access_snapshot.json').read_text())
    assert snapshot['spent_units']==1 and snapshot['admitted_attempts']==3
    assert not any('HIDDEN_LAB_' in str(m.content) or 'HIDDEN_AUDIT_' in str(m.content) for calls in model.inputs for m in calls)
    delivered=[m for calls in model.inputs for m in calls if getattr(m,'name',None)=='search_guidelines']
    assert delivered and any(decode_payload(m.content)==value for m in delivered)
    result={'enabled':enabled,'completed':True,'stage':'S6 C02 engineering, not clinical efficacy','new_clinical_calls':0,
        'scripted_consumer_calls':len(model.inputs),'gpu_computation':False,'cpu_retrieval_backend':True,
        'retrieval_callbacks':1,'cached_returns':1,'patient_units':snapshot['spent_units'],
        'admitted_attempts':snapshot['admitted_attempts'],'result_count':len(value['results']),
        'consumer_payload_digest':digest(value),'consumer_bytes':len(canonical(value).encode()),
        'elapsed_seconds':time.monotonic()-start}
    if enabled:
        assert all(z['type']=='conditional_primary_excerpt' for z in value['results'])
        assert any(z['evidence_package']['condition_status_at_retrieval']=='matched' for z in value['results'])
        events=[json.loads(x) for x in (case/'evidence_package_audit.jsonl').read_text().splitlines()]
        packaged=[x for x in events if x['event']=='packaged'];assert len(packaged)==1
        assert any(x['event']=='verified_consumer_payload' and x['consumer_payload_digest']==digest(value) for x in events)
        delivery=[x for x in events if x['event']=='delivery_ready']
        assert len(delivery)==2 and all(type(x['token_length']) is int and x['token_length']>0 for x in delivery)
        result['raw_hit_hashes']=[x['text_sha256'] for x in packaged[0]['raw_hits']]
        result['dispositions']=packaged[0]['dispositions']
    else:
        assert all('evidence_package' not in z for z in value['results'])
        assert not (case/'evidence_package_audit.jsonl').exists()
        result['raw_hit_hashes']=[x['text_sha256'] for x in value['results']]
    # Tokenizer endpoint only: no /completion, /chat or model forward operation.
    # Its loaded GGUF tokenizer runs on CPU; this is not a clinical LLM call.
    body=json.dumps({'content':canonical(value),'add_special':False,'parse_special':True}).encode()
    req=urllib.request.Request('http://127.0.0.1:8765/tokenize',data=body,headers={'Content-Type':'application/json'})
    with urllib.request.urlopen(req,timeout=15) as response:tokens=json.load(response)['tokens']
    result.update(consumer_token_length=len(tokens),tokenizer='existing llama.cpp GGUF tokenizer only; CPU; no generation',tokenizer_requests=1)
    (root/'summary.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    return result


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--overlay',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--project-root',type=Path,default=ROOT)
    args=parser.parse_args()
    ROOT=args.project_root.resolve()
    if args.output.exists():raise SystemExit('New integration directory required')
    os.environ['CHIMERA_TRACE_CONTENT_LIMIT']='0'
    disabled=asyncio.run(validate(args.output/'default',args.overlay,False));print(json.dumps(disabled),flush=True)
    enabled=asyncio.run(validate(args.output/'enabled',args.overlay,True));print(json.dumps(enabled),flush=True)
    assert disabled['raw_hit_hashes']==enabled['raw_hit_hashes'],'Ranking/query/backend changed between arms'
    (args.output/'summary.json').write_text(json.dumps({'passed':True,'default':disabled,'enabled':enabled,'same_raw_hits_and_order':True,'new_clinical_calls':0,'training':[]},ensure_ascii=False,indent=2)+'\n')
