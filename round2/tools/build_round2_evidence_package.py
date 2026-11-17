"""Build bounded C02 overlay from public primary documents and frozen KB bytes.

No patients, GT, quality labels, embeddings or model calls. Never rebuilds KB.
"""
import argparse
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3

from chimera_agent_baseline.evidence_package import VERSION, file_sha, load_overlay

ROOT = Path(__file__).resolve().parents[1]
AUDIT = ROOT / 'output/evidence_applicability_audit_20261117'
KB = ROOT / 'output/s2_knowledge_snapshot_20261113_v4'


def build(destination):
    destination = Path(destination).resolve()
    if destination.exists():
        raise ValueError('Use new overlay directory; do not overwrite a resource version')
    destination.mkdir(parents=True)
    source_dir = destination / 'sources'; source_dir.mkdir()
    specs = {
        'eau_2025': ('EAU', '2025-03', 'https://d56bochluxqnz.cloudfront.net/documents/full-guideline/EAU-EANM-ESTRO-ESUR-ISUP-SIOG-Guidelines-on-Prostate-Cancer-2025_2025-03-24-120144_rinw.pdf', 'eau_2025_official', 'pdf'),
        'pirads_21': ('ACR/ESUR/AdMeTech', '2019-v2.1', 'https://www.acr.org/Clinical-Resources/Clinical-Tools-and-Reference/Reporting-and-Data-Systems/PI-RADS', 'pirads_21_current_link', 'pdf'),
        'aua_2023_I': ('AUA/SUO', '2023', 'https://pmc.ncbi.nlm.nih.gov/articles/PMC11060750/', 'aua_part1_pmc', 'html'),
        'aua_2023_II': ('AUA/SUO', '2023', 'https://pmc.ncbi.nlm.nih.gov/articles/PMC11321723/', 'aua_part2_pmc', 'html')}
    sources = {}
    for key, (publisher, version, url, basename, suffix) in specs.items():
        files = []
        for ext in (suffix, 'txt'):
            src = AUDIT / 'sources' / (basename + '.' + ext)
            dst = source_dir / src.name; shutil.copy2(src, dst)
            files.append({'path': str(dst.relative_to(destination)), 'sha256': file_sha(dst)})
        sources[key] = {'publisher': publisher, 'version': version, 'url': url, 'files': files,
                        'verification_text': 'sources/' + basename + '.txt'}
        if key == 'eau_2025':
            sources[key]['layout_normalization'] = 'eau_right_column_strength'
    ledger = json.loads((AUDIT / 'rule_ledger.json').read_text())
    rules = deepcopy(ledger['rules'])
    summaries = {}
    for filename in ('aua_suo_2023_early_detection.md', 'pirads_v2_1_rules.md'):
        src = ROOT / 'guideline_dataset/raw' / filename
        dst = source_dir / filename; shutil.copy2(src, dst)
        summaries[filename] = {'path': str(dst.relative_to(destination)), 'sha256': file_sha(dst)}
    for rule in rules:
        if rule['rule_id'] == 'P60':
            rule['audit_note'] = 'Withdraw lenticular/crescentic objection; retain non-circumscribed versus non-encapsulated distinction. Original glossary printed p32.'
        if rule['rule_id'] == 'P61':
            rule['audit_note'] = 'Size/extension criteria consistent; inherited margin distinction from corrected P60 remains incomplete.'
        rule['runtime_handling'] = 'audit_mapping_not_official_action_authority'
        rule['necessary_conditions'] = ledger['conditions'][rule['scope_condition_key']]
        rule['source_level'] = 'project_secondary_summary'
        rule['verification_status'] = rule['status']
        rule['required_runtime_fact_values'] = 'unknown_unless_bound_at_runtime'
        rule['summary_source'] = summaries[Path(rule['file']).name]
        rule['summary_version'] = 'project-adaptation-2026-08-20'
        rule['content_type'] = 'secondary_rule_row'
        rule['exceptions'] = 'Full primary exceptions not runtime-certified for this summary row; retain unknown.'
        rule['primary_source_binding_status'] = 'manual_audit_locator_only_not_runtime_authorized'
    # Read SQLite in true read-only mode; no Chroma client/migration side effects.
    db = sqlite3.connect('file:' + str(KB / 'corpus/guidelines_db/chroma.sqlite3') + '?mode=ro', uri=True)
    docs = db.execute("SELECT string_value FROM embedding_metadata WHERE key='chroma:document'").fetchall()
    task_hashes = {'6ce99a149579869b8acbb44cf017c09e2ff126ea3124e23cab87dd61f20b8b80',
                   '725107b78f41fb26ba002dab958a7e38a6b9b1692132bc39ac8e9b226d9dd013'}
    toc_hashes = {'56e6d63e63319197d9c5765570c14a5e16f76f0ebe040014cdd06f19c4c2bb7d',
                  '055365bd77cca72ea1a2c00c4c5966c5178e54da32cc93b565826e2c225c90f7'}
    hits = []
    for (text,) in docs:
        key = hashlib.sha256(text.encode()).hexdigest()
        if key in task_hashes | toc_hashes:
            hits.append({'text_sha256': key, 'handling': 'directory' if key in toc_hashes else 'unverified_task_adaptation',
                         'source_version': '2026-explicit-in-text' if key in toc_hashes else 'project-summary-2026-08-20',
                         'source_level': 'directory' if key in toc_hashes else 'project_task_adaptation'})
    db.close()
    # The exact full conditional quotations in two already indexed QA carriers
    # are verified against the primary 2025 document. No ranking change/backfill.
    qa = [json.loads(line) for line in (KB / 'corpus/guideline_qa.jsonl').read_text().splitlines()]
    for identifier, maximum in [('qa-T1-s16-048', 2), ('qa-T1-s16-049', 3)]:
        q = next(x for x in qa if x['id'] == identifier)
        g = q['grounding'][0]
        head = f"[{g['source']} {g['section_id']}] {g['section_title']}"
        text = head + '\n' + q['support_quote']
        hits.append({'text_sha256': hashlib.sha256(text.encode()).hexdigest(), 'handling': 'conditional_reference',
            'document_id': 'eau_2025', 'locator': 'section 5.5.7, printed p41, ' + identifier,
            'primary_text': q['support_quote'], 'strength': 'Weak',
            'exceptions': 'Other risk/suspicion invokes the explicitly retained otherwise branch; not a known-cancer surveillance recommendation.',
            'conditions': [{'field': 'biopsy_context', 'op': 'in', 'value': ['initial', 'prior_negative']},
                {'field': 'pi_rads', 'op': 'le' if maximum == 2 else 'eq', 'value': maximum},
                {'field': 'psa_density', 'op': 'lt', 'value': 0.20 if maximum == 2 else 0.10},
                {'field': 'dre', 'op': 'eq', 'value': 'negative'},
                {'field': 'family_history_prostate_cancer', 'op': 'eq', 'value': False}]})
    if len(hits) != 6:
        raise ValueError('Expected two identified task summaries, two directories and two verified quotations')
    overlay = {'schema_version': 1, 'version': VERSION, 'knowledge_snapshot_id': file_sha(KB / 'manifest.json'),
        'scope': '69 secondary rule mappings; two verified primary conditional excerpts only; other KB hits unknown',
        'a01_ledger_sha256': file_sha(AUDIT / 'rule_ledger.json'), 'rules': rules, 'sources': sources, 'hits': hits,
        'summary_sources': summaries,
        'default_handling': 'unknown_source_binding', 'clinical_adjudication': False}
    path = destination / 'overlay.json';path.write_text(json.dumps(overlay, ensure_ascii=False, indent=2)+'\n')
    load_overlay(path, file_sha(path))
    return path


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__); parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(); result = build(args.output)
    print(json.dumps({'overlay_path': str(result), 'overlay_sha256': file_sha(result)}))
