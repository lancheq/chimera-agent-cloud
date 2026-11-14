import json
import zipfile
import pytest
from tools.build_round2_data_pool import input_kind, inspect_archive, risk_groups, version_pairs


def record(source,cid,task=1):
    return {'source':source,'case_id':cid,'task':task,'input_hashes':{}}


def test_transitive_cross_task_candidates_stay_together_without_identity_claim():
    cases=[record('old','A'),record('new','A'),record('new','B',2),record('other','C')]
    edges=[{'basis':'case_id','key_hash':'id','record_indices':[0,1]},
           {'basis':'embeddings','key_hash':'vector','record_indices':[1,2]}]
    x=risk_groups(cases,edges);m=x['mappings']
    assert m[0]['risk_group_id']==m[1]['risk_group_id']==m[2]['risk_group_id']
    assert m[3]['risk_group_id']!=m[0]['risk_group_id']
    assert x['independent_patient_count'] is None and not x['split_assigned']
    assert all(not a['patient_identity_confirmed'] for a in m)
    assert sum(g['cross_tasks'] for g in x['groups'])==1


def test_prompt_only_match_cannot_merge_cases():
    cases=[record('old','A'),record('new','B')]
    x=risk_groups(cases,[{'basis':'prompt','key_hash':'same','record_indices':[0,1]}])
    assert len(x['groups'])==2


def test_target_and_reasoning_names_are_not_input_whitelist():
    assert input_kind('prostate-biopsy-decision.json') is None
    assert input_kind('prostate-biopsy-decision-reasoning.json') is None
    assert input_kind('metrics.json') is None


def test_version_key_match_is_not_identity_or_missing_embedding_equality():
    a=record('local_train_release_v1','A');b=record('phase2_train_release_20261104','A')
    a['input_hashes']={'clinical':'old'};b['input_hashes']={'clinical':'new'}
    x=version_pairs([a,b])[0]
    assert not x['evidence']['embeddings']['fingerprints_equal']
    assert not x['evidence']['clinical']['fingerprints_equal']
    assert not x['label_alignment_assessed'] and not x['merge_input_values']


def test_zip_targets_never_inflated_and_input_identity_is_checked(tmp_path,monkeypatch):
    archive=tmp_path/'release.zip';root=tmp_path/'extracted';case=root/'task1'/'A';case.mkdir(parents=True)
    (case/'structured-prompt.json').write_text('{"age":65}')
    with zipfile.ZipFile(archive,'w') as z:
        z.writestr('release/task1/A/structured-prompt.json','{"age":65}')
        z.writestr('release/task1/A/prostate-biopsy-decision.json','DO NOT DECODE TARGET')
        z.writestr('release/task1/A/prostate-biopsy-decision-reasoning.json','DO NOT DECODE REASONING')
    original=zipfile.ZipFile.read
    def guard(self,name,*args,**kwargs):
        member=name.filename if isinstance(name,zipfile.ZipInfo) else name
        assert member.endswith('structured-prompt.json')
        return original(self,name,*args,**kwargs)
    monkeypatch.setattr(zipfile.ZipFile,'read',guard)
    result=inspect_archive(archive,root)
    assert result['input_members_checked']==1 and not result['input_mismatches']
    assert result['non_input_member_contents_opened']==0
    (case/'structured-prompt.json').write_text('{"age":66}')
    assert len(inspect_archive(archive,root)['input_mismatches'])==1


def test_archive_path_escape_is_rejected_without_extraction(tmp_path):
    archive=tmp_path/'bad.zip'
    with zipfile.ZipFile(archive,'w') as z:z.writestr('../structured-prompt.json','{}')
    with pytest.raises(ValueError,match='Unsafe'):inspect_archive(archive,tmp_path)
