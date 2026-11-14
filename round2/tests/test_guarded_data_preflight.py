"""Raw provenance hashes must not be confused with identity-stripped fingerprints."""
import hashlib
import json
from pathlib import Path
import sys
import pytest
from tools import preflight_guarded_data as preflight
from tools.validate_guarded_runner import fixture_config
from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK


def registered(tmp_path):
    cfg, source = fixture_config(tmp_path)
    records=[];assets=[]
    for index,cid in enumerate(('A','B')):
        records.append(dict(source='fixture',task=1,case_id=cid,record_index=index,
            directory=str(source/cid),input_hashes={'prompt':'semantic_fingerprint_is_not_a_byte_hash','clinical':'semantic'}))
        for name in ('prompt.json',CASE_DATA_FILENAMES_BY_TASK[1]):
            path=source/cid/name
            assets.append(dict(path=str(path),role='input',sha256=hashlib.sha256(path.read_bytes()).hexdigest()))
        (source/cid/'prostate-biopsy-decision.json').write_text('MUST_NOT_OPEN_REFERENCE')
    pool=tmp_path/'pool.json';pool.write_text(json.dumps(dict(cases=records,assets=assets)))
    return pool,source


def invoke(monkeypatch,pool,out):
    monkeypatch.setattr(sys,'argv',['preflight_guarded_data','--pool',str(pool),'--source','fixture','--output',str(out)])
    preflight.main()
    return json.loads((out/'summary.json').read_text())


def test_preflight_uses_registered_raw_bytes_without_opening_reference(monkeypatch,tmp_path):
    pool,source=registered(tmp_path)
    summary=invoke(monkeypatch,pool,tmp_path/'audit')
    assert summary['passed']==summary['total']==2 and not summary['failures']
    assert summary['targets_opened']==summary['actual_diagnostic_llm_calls']==0
    assert not summary['read_boundary']['forbidden_attempts']


def test_semantically_equal_but_changed_bytes_do_not_reuse_provenance(monkeypatch,tmp_path):
    pool,source=registered(tmp_path)
    path=source/'A/prompt.json';path.write_text(path.read_text()+'\n')
    out=tmp_path/'audit'
    with pytest.raises(SystemExit):invoke(monkeypatch,pool,out)
    summary=json.loads((out/'summary.json').read_text())
    assert summary['passed']==1 and len(summary['failures'])==1
    assert summary['failures'][0]['case_id']=='A'
    assert 'bytes differ' in summary['failures'][0]['error']


def test_source_boundary_refuses_reference_and_sidecar_open(tmp_path):
    source=tmp_path/'source';source.mkdir()
    path=source/'prostate-biopsy-decision.json';path.write_text('MUST_NOT_OPEN_REFERENCE')
    with preflight.input_read_boundary([source]) as events:
        with pytest.raises(PermissionError):path.read_text()
        with pytest.raises(PermissionError):open(path)
    assert len(events['forbidden_attempts'])==2
