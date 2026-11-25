"""Verify reproducibility of the recorded judgments, including any HOLD."""
from hashlib import sha256
import json
from pathlib import Path
import sys

from preregister import OUT, ROOT, pin


def read(p):
    return json.loads(p.read_bytes())


def rows(path):
    loaded = []
    for line in path.read_bytes().splitlines():
        record = json.loads(line)
        claimed = record.pop('log_sha256')
        wire = json.dumps(record,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()
        assert sha256(wire).hexdigest()==claimed
        loaded.append(record)
    return loaded


def stable(record):
    keys = ('rid','case','probe','inputs','expected','actual','status','first_mismatch')
    return {key:record[key] for key in keys}


def verify_saved():
    h = read(OUT/'handoff.json')
    assert h['frozen_before']==h['frozen_after']
    for name, fingerprint in (h['frozen_after']|h['deliverables']).items():
        assert pin(ROOT/name)==fingerprint,name
    data = rows(OUT/'expected_actual.jsonl')
    planned = read(OUT/'expected.json')
    assert [{key:item[key] for key in ('rid','case','probe','inputs','expected')} for item in data]==planned
    return h,data


if __name__=='__main__':
    h,original = verify_saved()
    if len(sys.argv)>1:
        scratch = Path(sys.argv[1])
        observed_rc = [int(x) for x in sys.argv[2:]]
        assert observed_rc == h['execution_exit_codes']
        reproduced = rows(scratch/'matrix/expected_actual.jsonl')
        assert [stable(r) for r in reproduced] == [stable(r) for r in original]
        frozen = read(OUT/'baseline_delta.json')
        rerun = read(scratch/'baseline_delta.json')
        new_runtime_paths = ('baseline_fixture_result_location','candidate_fixture_result_location')
        assert {k:v for k,v in rerun.items() if k not in new_runtime_paths} == {
                k:v for k,v in frozen.items() if k not in new_runtime_paths}
    print(h['status'],len(original),'recorded probes checked')
