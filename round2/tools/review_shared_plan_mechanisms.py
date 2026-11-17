"""Offline evidence/stop audit of a finished study. Never calls a model or edits cases."""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path

from langchain_core.messages import messages_from_dict
from chimera_agent_baseline.agent.evidence_features import decode_payload
from chimera_agent_baseline.information_access import tool_contract, canonical


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def classify_pending(action, visible, budget, requests, query_cache=()):
    """Mechanical affordability only; never asserts clinical necessity."""
    remaining_slots = 6 - requests
    attempts_left = budget['max_attempts'] - budget['admitted_attempts']
    common = dict(tool=action['tool'], purpose=action['purpose'], query=action.get('query'),
                  action_slots_left=remaining_slots, admitted_attempts_left=attempts_left)
    if remaining_slots <= 0 or attempts_left <= 0:
        return common | dict(category='attempt_or_action_cap', individually_feasible=False)
    if action['tool'] == 'search_guidelines':
        category = 'knowledge_query_already_seen' if action.get('query') in query_cache else 'new_knowledge_query'
        return common | dict(category=category, individually_feasible=True, new_patient_units=0)
    fields = tool_contract(1)['tools'][action['tool']]['fields']
    unseen = set(fields) - set(visible)
    if not unseen:
        return common | dict(category='already_visible_clinical_section', individually_feasible=True, new_patient_units=0)
    required = len(unseen)
    affordable = budget['budget_units'] - budget['spent_units'] >= required
    return common | dict(category='affordable_unseen_clinical_section' if affordable else 'clinical_budget_blocked',
                         individually_feasible=affordable, new_patient_units=required, unseen_fields=sorted(unseen))


def history(events, initial):
    remaining = initial[1:]
    decisions = []
    final_remaining = []
    for e in events:
        if e['kind'] == 'policy_decision':
            d = e['decision']
            decisions.append(dict(turn=e['policy_turn'], before=remaining, decision=d))
            if d['action'] == 'stop':
                final_remaining = remaining
                remaining = []
            else:
                remaining = d['remaining_plan'][1:]
    return decisions, final_remaining


def arm_state(directory):
    checkpoint = json.loads((directory/'candidate_checkpoint.json').read_text())
    artifact = json.loads((directory/'initial_plan.json').read_text())
    events = [json.loads(l) for l in (directory/'candidate_events.jsonl').read_text().splitlines()]
    snapshot = json.loads((directory/'information_access_snapshot.json').read_text())
    prediction = json.loads((directory/'prediction.json').read_text())
    clinical = {}; knowledge = []; observations = []
    for m in messages_from_dict(checkpoint['observations']):
        payload = decode_payload(m.content)
        observations.append(dict(tool=m.name, status=m.status, payload=payload if payload is not None else m.content))
        if m.status != 'success' or not isinstance(payload, dict):
            continue
        if m.name == 'search_guidelines':
            knowledge.append(payload)
        else:
            for field in tool_contract(1)['tools'].get(m.name, {}).get('fields', []):
                if field in payload:
                    if field in clinical:
                        assert digest(clinical[field]) == digest(payload[field])
                    clinical[field] = payload[field]
    decisions, pending = history(events, artifact['actions'])
    requests = [e for e in events if e['kind']=='model_request']
    diagnosis = next(e for e in requests if e['stage']=='diagnosis')
    draft = checkpoint['diagnostic_draft'][0]['data']['content']
    cache = [e['args']['query'] for e in events if e['kind']=='tool_request' and e['tool']=='search_guidelines']
    pending_states = [classify_pending(a, snapshot['visible_sections'], snapshot, checkpoint['requests'], cache) for a in pending]
    return dict(plan_id=artifact['plan_id'], initial_plan=artifact['actions'], observations=observations,
        clinical=clinical, clinical_hashes={k:digest(v) for k,v in clinical.items()}, knowledge=knowledge,
        knowledge_payload_hashes=sorted(digest(v) for v in knowledge), diagnosis_request_digest=diagnosis['request_digest'],
        diagnosis_options=diagnosis['options'], diagnosis_messages=diagnosis['messages'], diagnostic_draft=draft,
        final_decision=prediction['biopsy_decision'], final_rationale=prediction['free_text'],
        visible_sections=snapshot['visible_sections'], information_units=snapshot['spent_units'],
        stop_reason=checkpoint['stop_reason'], requests=checkpoint['requests'], policy_turns=checkpoint['policy_turns'],
        policy_history=decisions, stopped_with_pending=bool(pending), pending_at_stop=pending_states)


def compare_states(fixed, adaptive):
    f=fixed['clinical_hashes'];a=adaptive['clinical_hashes']
    shared=set(f)&set(a)
    return dict(same_successful_clinical_payloads=f==a,
        fixed_only_sections=sorted(set(f)-set(a)),adaptive_only_sections=sorted(set(a)-set(f)),
        shared_sections_with_different_payloads=sorted(k for k in shared if f[k]!=a[k]),
        same_knowledge_payloads=fixed['knowledge_payload_hashes']==adaptive['knowledge_payload_hashes'],
        same_exact_diagnosis_request=fixed['diagnosis_request_digest']==adaptive['diagnosis_request_digest'],
        same_diagnosis_sampling_options=fixed['diagnosis_options']==adaptive['diagnosis_options'],
        information_saved=fixed['information_units']-adaptive['information_units'])


def analyze(protocol_path, output):
    assert not output.exists(), 'Preserve previous audit'
    p=json.loads(protocol_path.read_text());root=Path(p['results_root'])
    control=Path(json.loads((Path(__file__).resolve().parents[1]/'output/s4_v2_authorized_deployment_current.json').read_text())['control'])
    analysis=json.loads((control/'analysis/analysis.json').read_text());labels={r['case_id']:r for r in analysis['rows']}
    output.mkdir(parents=True);casebook=[];early=[];discordant=[]
    before={str(f):hashlib.sha256(f.read_bytes()).hexdigest() for f in root.rglob('*') if f.is_file()}
    for number,record in enumerate(p['evaluation_cases'],1):
        cid=record['case_id'];worker=root/f"worker{record['worker']}";row=labels[cid]
        f=arm_state(worker/'fixed/task1'/cid);a=arm_state(worker/'adaptive/task1'/cid)
        assert f['plan_id']==a['plan_id']
        comparison=compare_states(f,a)
        metadata=dict(case_id=cid,risk_group_id=record['risk_group_id'],fixed_correct=bool(row['fixed']['parsed_correct']),adaptive_correct=bool(row['adaptive']['parsed_correct']),**comparison)
        if a['stopped_with_pending']:
            states=a['pending_at_stop'];categories=Counter(x['category'] for x in states)
            early.append(metadata | dict(pending_categories=dict(categories),pending=states,
                has_affordable_unseen_clinical=any(x['category']=='affordable_unseen_clinical_section' for x in states),
                has_new_knowledge_query=any(x['category']=='new_knowledge_query' for x in states),
                only_unavailable_or_already_seen=all(x['category'] in ('clinical_budget_blocked','attempt_or_action_cap','already_visible_clinical_section','knowledge_query_already_seen') for x in states),
                adaptive_stop_reason=a['policy_history'][-1]['decision']['reason']))
        if f['final_decision']!=a['final_decision']:
            runtime=json.loads((protocol_path.parent/f"runtime_worker{record['worker']}.json").read_text())
            case=next(x for x in runtime['cases'] if x['case_id']==cid);upfront=Path(case['directory'])/case['upfront_filename']
            dossier=dict(**metadata,review_id='D'+str(len(discordant)+1),upfront=json.loads(upfront.read_text()),
                target=row['label'],reference_reasoning=json.loads(Path(record['references']['reasoning']['path']).read_text()),fixed=f,adaptive=a)
            discordant.append(dossier)
        casebook.append(metadata)
    assert len(casebook)==91 and len(discordant)==6 and len(early)==47
    for name,value in before.items():assert hashlib.sha256(Path(name).read_bytes()).hexdigest()==value
    categories=Counter(x['category'] for r in early for x in r['pending'])
    cohorts={}
    for key in ('has_affordable_unseen_clinical','has_new_knowledge_query','only_unavailable_or_already_seen'):
        selected=[r for r in early if r[key]]
        cohorts[key]=dict(n=len(selected),fixed_correct=sum(r['fixed_correct'] for r in selected),adaptive_correct=sum(r['adaptive_correct'] for r in selected),information_saved=sum(r['information_saved'] for r in selected))
    counts=Counter('saved' if r['information_saved']>0 else 'same' if r['information_saved']==0 else 'spent_more' for r in early)
    summary=dict(stage='offline_mechanism_review_not_causal_intervention',case_pairs=91,discordant_pairs=6,
        discordant_same_clinical_payloads=sum(x['same_successful_clinical_payloads'] for x in discordant),
        discordant_exact_same_diagnosis_request=sum(x['same_exact_diagnosis_request'] for x in discordant),
        early_stops=47,early_stop_action_categories=dict(categories),early_stop_action_count=sum(categories.values()),early_stop_cost_cases=dict(counts),
        early_stop_fixed_correct=sum(x['fixed_correct'] for x in early),early_stop_adaptive_correct=sum(x['adaptive_correct'] for x in early),
        early_stop_information_saved=sum(x['information_saved'] for x in early),early_stop_groups=cohorts,
        actual_model_calls=0,original_output_files_verified_unchanged=len(before),
        limits=['Affordability is not information value or clinical necessity','Available trace comparisons cannot identify counterfactual effect or sampling-only causation','Unseen source material and reference text are offline audit only, never new runtime input'])
    for name,data in [('summary.json',summary),('discordant_private.json',discordant),('early_stop_private.json',early),('pair_metadata_private.json',casebook)]:
        (output/name).write_text(json.dumps(data,ensure_ascii=False,indent=2)+'\n')
    return summary


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--protocol',type=Path,required=True);parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args();print(json.dumps(analyze(args.protocol.resolve(),args.output.resolve()),ensure_ascii=False,indent=2))
