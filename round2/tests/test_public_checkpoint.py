"""Publication boundaries and index preservation, without any remote write."""
import json
from pathlib import Path
import subprocess
import pytest
from tools import public_checkpoint as pub


def test_allowlist_and_report_redaction(tmp_path):
    for d in ('src/pkg','reports','data','output','src/pkg.bak_old'):(tmp_path/d).mkdir(parents=True)
    (tmp_path/'src/pkg/a.py').write_text('x = 1\n')
    (tmp_path/'src/pkg.bak_old/a.py').write_text('old = 1\n')
    (tmp_path/'data/patient.json').write_text('{"private":true}')
    (tmp_path/'output/trace.json').write_text('{"private":true}')
    (tmp_path/'reports/ROUND2_test.md').write_text('60/91\n'+ 'PT-'+'pseudo_'+'abcdef012345 patient values\n')
    files,manifest=pub.collect(tmp_path)
    assert set(files)=={'round2/src/pkg/a.py','round2/reports/ROUND2_test.md'}
    assert 'patient values' not in files['round2/reports/ROUND2_test.md']
    assert '60/91' in files['round2/reports/ROUND2_test.md']
    assert manifest['reports/ROUND2_test.md']['public_derivative']


def test_credentials_block_publication(tmp_path):
    (tmp_path/'src').mkdir();(tmp_path/'src/a.py').write_text('token="'+'ghp_'+'x'*30+'"')
    with pytest.raises(ValueError,match='Credential'):pub.collect(tmp_path)


def test_external_symlink_rejected(tmp_path):
    root=tmp_path/'root';(root/'src').mkdir(parents=True)
    other=tmp_path/'outside.py';other.write_text('private=1')
    (root/'src/link.py').symlink_to(other)
    with pytest.raises(ValueError,match='escapes'):pub.collect(root)


def test_prepare_preserves_source_index_and_staging(tmp_path):
    subprocess.run(['git','init',str(tmp_path)],check=True,capture_output=True)
    def git(*args):return subprocess.check_output(['git',*args],cwd=tmp_path)
    git('config','user.name','fixture');git('config','user.email','fixture@local')
    (tmp_path/'baseline').write_text('base');git('add','baseline');git('commit','-m','base')
    (tmp_path/'staged').write_text('keep staged');git('add','staged')
    (tmp_path/'src').mkdir();(tmp_path/'src/a.py').write_text('x=2\n')
    before=git('status','--porcelain=v1');head=git('rev-parse','HEAD')
    result=pub.prepare('test','engineering fixture',tmp_path)
    after=git('status','--porcelain=v1')
    assert git('diff','--cached','--name-only')==b'staged\n'
    assert git('rev-parse','HEAD')==head
    assert b'?? src/' in before and b'?? src/' in after
    bundle=json.loads(Path(result['bundle_path']).read_text())
    assert bundle['status']=='prepared_not_uploaded'
    assert bundle['files']['round2/src/a.py']=='x=2\n'
    assert result['local_archive_commit']
