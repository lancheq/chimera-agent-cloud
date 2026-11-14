#!/usr/bin/env python3
"""S1 provenance and conservative leakage groups; never decode target files."""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from datetime import datetime
import hashlib
import json
from pathlib import Path, PurePosixPath
import zipfile
from zoneinfo import ZoneInfo
from tools.audit_cohorts import audit, TASK_SLUGS

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT/'data/round2_materials_20261111/mac_transfer/20261111_101222_bn72g8xr/payload'


def read(p): return json.loads(p.read_text())
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def write(p,x): p.write_text(json.dumps(x,ensure_ascii=False,indent=2)+'\n')


def input_kind(name):
    if name in ('prompt.json','structured-prompt.json'): return 'prompt'
    if name in ('clinical.json', *[s+'-clinical-data.json' for s in TASK_SLUGS.values()]): return 'clinical'
    if name in ('features.json','prostate-modality-level-neural-representations.json'): return 'embeddings'
    return None


def risk_groups(cases, overlaps):
    """Union evidence candidates for split safety, NEVER assert clinical identity."""
    parent=list(range(len(cases)))
    def find(i):
        while parent[i]!=i:parent[i]=parent[parent[i]];i=parent[i]
        return i
    evidence=[]
    for edge in overlaps:
        members=edge['record_indices']
        if edge['basis'] not in ('case_id','patient_id','clinical','embeddings'):continue
        for m in members[1:]:parent[find(m)]=find(members[0])
        evidence.append({'basis':edge['basis'],'key_hash':edge['key_hash'],'record_indices':members})
    grouped=defaultdict(list)
    for i in range(len(cases)):grouped[find(i)].append(i)
    mappings=[];groups=[]
    for members in grouped.values():
        keys=sorted(f"{cases[i]['source']}|{cases[i]['task']}|{cases[i]['case_id']}" for i in members)
        gid='risk-'+hashlib.sha256('\n'.join(keys).encode()).hexdigest()[:16]
        groups.append({'risk_group_id':gid,'record_indices':members,'n_records':len(members),
            'status':'candidate_link_not_confirmed_patient' if len(members)>1 else 'unlinked_identity_unresolved',
            'cross_tasks':len({cases[i]['task'] for i in members})>1})
        for i in members:
            r=cases[i]
            mappings.append({'record_index':i,'source':r['source'],'task':r['task'],'case_id':r['case_id'],
                'risk_group_id':gid,'patient_identity_confirmed':False,'input_fingerprints':r['input_hashes'],
                'split_rule':'keep_candidate_group_together_if_internal_partitioning; unresolved mapping prevents certified independence'})
    return {'groups':groups,'mappings':sorted(mappings,key=lambda x:x['record_index']),'link_evidence':evidence,
        'independent_patient_count':None,'split_assigned':False,
        'limitation':'Conservative transitive risk clusters may overmerge; no-match does not establish independence.'}


def version_pairs(cases,left='local_train_release_v1',right='phase2_train_release_20261104'):
    indexes={s:{(r['task'],r['case_id']):r for r in cases if r['source']==s} for s in (left,right)}
    rows=[]
    for key in sorted(set(indexes[left]) & set(indexes[right])):
        a=indexes[left][key];b=indexes[right][key];matches={}
        for kind in ('prompt','clinical','embeddings'):
            x=a['input_hashes'].get(kind);y=b['input_hashes'].get(kind)
            matches[kind]={'both_nonempty':bool(x and y),'fingerprints_equal':bool(x and y and x==y)}
        rows.append({'task':key[0],'case_id':key[1],'left_source':left,'right_source':right,
            'evidence':matches,'status':'same_key_version_candidate_not_confirmed_patient',
            'label_alignment_assessed':False,'merge_input_values':False})
    return rows


def inspect_archive(path, extracted_root):
    """Only inflate whitelisted clinical inputs, never labels/reasoning members."""
    entries=[];mismatch=[];checked=0;skipped=Counter()
    with zipfile.ZipFile(path) as z:
        for m in z.infolist():
            p=PurePosixPath(m.filename)
            if p.is_absolute() or '..' in p.parts:raise ValueError('Unsafe archive member path')
            if m.is_dir():continue
            kind=input_kind(p.name)
            # Require exact release/task/case/file layout, not merely a filename suffix.
            is_input=bool(kind and len(p.parts)==4 and p.parts[1] in ('task1','task2','task3'))
            entry={'member':m.filename,'bytes':m.file_size,'crc32':m.CRC,'content_opened':is_input}
            if is_input:
                target=extracted_root.joinpath(*p.parts[1:]);data=z.read(m)
                digest=hashlib.sha256(data).hexdigest();entry.update(kind=kind,sha256=digest,extracted_path=str(target))
                checked+=1
                if not target.is_file() or sha(target)!=digest:mismatch.append(m.filename)
            else:skipped['non_input_members']+=1
            entries.append(entry)
    return {'path':str(path),'sha256':sha(path),'members':entries,'input_members_checked':checked,
        'input_mismatches':mismatch,'non_input_member_contents_opened':0,'skipped':skipped,
        'meaning':'Archive-to-directory identity of input files only; not new patients or permission to use labels.'}


def build(config_path, out):
    if out.exists():raise FileExistsError('Use a new output directory; preserve prior audits')
    config=read(config_path);inv=audit(config['sources'],config_path.resolve().parent)
    if inv['errors']:raise ValueError(f"Input inventory errors: {inv['errors'][:3]}")
    source_manifest=read(PAYLOAD/'source_manifest.json')
    origins={str((PAYLOAD/x['staged']).resolve()):x for x in source_manifest['source_files']}
    transferred={str((PAYLOAD/x['path']).resolve()):x for x in read(PAYLOAD/'transfer_manifest.json')['files']}
    files=[];snapshots={};errors=[];records=[]
    def register(path,role,source,record_index=None):
        if not path.is_file():return None
        key=str(path.resolve());decl=transferred.get(key,{});origin=origins.get(key,{})
        item={'path':key,'role':role,'source':source,'record_index':record_index,'bytes':path.stat().st_size,
            'original_path':origin.get('source'),'declared_transfer_sha256':decl.get('sha256'),
            'content_opened':role=='input','hash_verification':'transfer_declaration_only_content_not_opened'}
        if role=='input':
            h=sha(path);snapshots[key]=h;item.update(sha256=h,hash_verification='current_input_bytes')
            if decl and decl['sha256']!=h:errors.append({'error':'transfer_input_mismatch','path':key})
        if decl and decl['bytes']!=item['bytes']:errors.append({'error':'declared_size_mismatch','path':key})
        files.append(item);return len(files)-1
    for i,r in enumerate(inv['cases']):
        d=Path(r['directory']);slug=TASK_SLUGS[r['task']];inputs=[]
        for f in sorted(d.iterdir()):
            if f.is_file() and input_kind(f.name):inputs.append(register(f,'input',r['source'],i))
        decision=register(d/(slug+'.json'),'reference_decision',r['source'],i)
        reasoning=register(d/(slug+'-reasoning.json'),'reference_reasoning',r['source'],i)
        fixtures=('-mock-' in r['case_id'] or r['case_id'].startswith(('BX_','T2_')))
        records.append({**r,'record_index':i,'input_asset_indices':inputs,'decision_asset_index':decision,
            'reasoning_asset_index':reasoning,'replay_status':'input_bundle_present_runtime_adapter_not_certified',
            'usage':'engineering_fixture' if fixtures else 'internal_development_exposed',
            'fresh_holdout':False,'label_content_opened':False})
    # Platform returns may contain references: metadata declarations only.
    for path in sorted((PAYLOAD/'project/reports').rglob('*.json')):
        if 'metrics' in path.name.lower():register(path,'platform_return_not_clinical_input','transferred_platform')
    official=[]
    for t in (1,2,3):
        d=ROOT/f'data/task{t}/agent_input_official'
        if not d.is_dir():continue
        for c in sorted(p for p in d.iterdir() if p.is_dir()):
            matches=[]
            for f in c.iterdir():
                if f.is_file() and input_kind(f.name):
                    register(f,'input','official_sample_copy')
                    other=ROOT/f'data/task{t}/agent_input'/c.name/f.name
                    matches.append(other.is_file() and sha(other)==sha(f))
            official.append({'task':t,'case_id':c.name,'directory':str(c),'all_present_inputs_equal_legacy':bool(matches) and all(matches),
                'usage':'engineering_only_not_independent_clinical_expansion'})
    sample_keys={(x['task'],x['case_id']) for x in official if x['all_present_inputs_equal_legacy']}
    for r in records:
        if r['source']=='server_legacy' and (r['task'],r['case_id']) in sample_keys:
            r['usage']='engineering_fixture'
    archive_path=ROOT/'data/round2_materials_20261111/transfer_only/20261111_104236_a423fab5/payload/project/_phase2_train_20261104/train_release_chimera_phase2.zip'
    phase=next(s for s in config['sources'] if s['name']=='phase2_train_release_20261104')
    archive=inspect_archive(archive_path,(config_path.resolve().parent/phase['root']).resolve())
    if archive['input_mismatches']:errors.append({'error':'archive_input_mismatch','members':archive['input_mismatches']})
    relation=read(PAYLOAD/'platform_copy_relationship.json')
    metrics_relation={'declared_relation':relation,'verification':'previous_transfer_verified_declaration; no reference-bearing contents opened this run',
        'observed_files':[{'path':str(PAYLOAD/p),'present':(PAYLOAD/p).is_file(),'bytes':(PAYLOAD/p).stat().st_size if (PAYLOAD/p).exists() else None} for p in relation['files']]}
    grouping=risk_groups(inv['cases'],inv['overlap_candidates'])
    pairs=version_pairs(inv['cases'])
    counts=Counter(f['role'] for f in files)
    missing=[k for k,h in snapshots.items() if not Path(k).is_file() or sha(Path(k))!=h]
    assert not missing and not errors, (missing,errors)
    out.mkdir(parents=True)
    manifest={'generated_at':datetime.now(ZoneInfo('Asia/Shanghai')).isoformat(timespec='seconds'),
        'sources':inv['sources'],'cases':records,'assets':files,'official_sample_copies':official,
        'platform_copy_relationship':metrics_relation,'archive_reference':'archive_inputs.json',
        'independent_dev_test_input_package_verified':False,
        'scope':'Configured three server sources, received transfer declarations and the single transferred ZIP; no assertion about unmounted Mac or unsearched hosts.',
        'use_restrictions':['No fresh holdout claimed.','Reference assets registered by metadata only; no labels imported into training.',
            'No automatic new partition, cleaned dataset, or label-based conflict resolution.']}
    summary={'stage':'S1_P1_P2_source_and_risk_group_registration','core_records':len(records),'asset_roles':counts,
        'sources':inv['summary']['by_source'],'risk_groups':len(grouping['groups']),
        'multi_record_risk_groups':sum(g['n_records']>1 for g in grouping['groups']),
        'cross_task_risk_groups':sum(g['cross_tasks'] for g in grouping['groups']),
        'largest_risk_group':max(g['n_records'] for g in grouping['groups']),
        'release_same_key_pairs':len(pairs),
        'release_pair_nonempty_fingerprint_matches':{kind:sum(x['evidence'][kind]['fingerprints_equal'] for x in pairs) for kind in ('prompt','clinical','embeddings')},
        'engineering_fixture_records':sum(r['usage']=='engineering_fixture' for r in records),
        'confirmed_independent_patient_count':None,'official_sample_copies':len(official),
        'sample_copies_equal_legacy':sum(x['all_present_inputs_equal_legacy'] for x in official),
        'archive_input_members_checked':archive['input_members_checked'],'archive_input_mismatches':0,
        'inputs_hash_checked':len(snapshots),'inputs_unchanged':True,'gt_file_contents_opened':0,
        'platform_return_contents_opened':0,'new_model_calls':0,'training_started':False,'production_changed':False,
        'next_step':'P3 source/date/unit conflict review; definitive patient mapping and independent dev/test inputs remain unresolved.'}
    write(out/'data_pool_manifest.json',manifest);write(out/'patient_version_map.json',grouping)
    write(out/'version_pairs.json',pairs)
    write(out/'archive_inputs.json',archive);write(out/'input_hashes.json',snapshots);write(out/'summary.json',summary)
    write(out/'source_config.json',config)
    (out/'REPORT.md').write_text('# S1 数据来源与保守分组登记\n\n'+json.dumps(summary,ensure_ascii=False,indent=2)+
        '\n\n风险组仅用于避免疑似同源记录跨分区，不是已确认的患者身份或独立患者数；未自动划分训练／验证集。未匹配也不证明独立。\n\n参考标签、reasoning和平台返回只登记文件元数据；声明hash来自既有传输清单，不冒充本轮内容复核。ZIP只读取白名单输入成员。\n\n官方样例副本不扩充临床队列；训练ZIP与解压目录输入一致也不新增患者。未核实dev/test原包的范围限制见data_pool_manifest。\n')
    return summary


if __name__=='__main__':
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--sources',type=Path,required=True);p.add_argument('--output-dir',type=Path,required=True)
    a=p.parse_args();print(json.dumps(build(a.sources,a.output_dir),ensure_ascii=False,indent=2))
