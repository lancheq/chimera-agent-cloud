"""Opt-in, hash-bound conditional guideline references; never a clinical decision.

Only a trusted InformationAccess instance and its bound upfront file can supply
facts. Model tool arguments cannot supply visibility, source authority or rules.
Unmapped hits are retained in the private audit, not promoted to recommendations.
"""
from copy import deepcopy
import fcntl
import hashlib
import json
from pathlib import Path
import re
import time
import urllib.request

from chimera_agent_baseline.information_access import InformationAccess, AccessDenied, canonical, digest

VERSION = 'source-applicability-v1'
FACTS = {'biopsy_context', 'pi_rads', 'psa_density', 'dre', 'family_history_prostate_cancer'}


class EvidencePackageError(ValueError):
    pass


def file_sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_overlay(path, expected):
    path = Path(path)
    if not expected or file_sha(path) != expected:
        raise EvidencePackageError('overlay_identity_mismatch')
    value = json.loads(path.read_text())
    if value.get('version') != VERSION or value.get('schema_version') != 1:
        raise EvidencePackageError('overlay_version_mismatch')
    if len(value.get('rules', [])) != 69 or len({r['rule_id'] for r in value['rules']}) != 69:
        raise EvidencePackageError('incomplete_rule_mapping')
    for item in value.get('summary_sources', {}).values():
        if file_sha(path.parent / item['path']) != item['sha256']:
            raise EvidencePackageError('summary_source_identity_mismatch')
    for key, source in value['sources'].items():
        if not source.get('version') or not source.get('publisher') or not source.get('url'):
            raise EvidencePackageError('unknown_source_metadata:' + key)
        for item in source['files']:
            item['path'] = str((path.parent / item['path']).resolve())
            if file_sha(item['path']) != item['sha256']:
                raise EvidencePackageError('source_identity_mismatch:' + key)
        source['verification_text'] = str((path.parent / source['verification_text']).resolve())
        if source['verification_text'] not in [f['path'] for f in source['files']]:
            raise EvidencePackageError('unbound_verification_text:' + key)
    seen = set()
    for hit in value['hits']:
        if hit['text_sha256'] in seen:
            raise EvidencePackageError('duplicate_overlay_source')
        seen.add(hit['text_sha256'])
        if hit['handling'] == 'conditional_reference':
            source = value['sources'].get(hit.get('document_id'))
            if not source or not hit.get('locator') or not hit.get('primary_text') or not hit.get('conditions'):
                raise EvidencePackageError('incomplete_primary_binding')
            original = Path(source['verification_text']).read_text()
            if source.get('layout_normalization') == 'eau_right_column_strength':
                # pdftotext -layout puts the separate recommendation-strength
                # column at the end of a wrapped sentence's first line.
                original = re.sub(r'[ \t]{2,}(?:Weak|Strong)[ \t]*$', '', original, flags=re.M)
            norm = lambda t: ' '.join(t.split())
            if norm(hit['primary_text']) not in norm(original):
                raise EvidencePackageError('primary_excerpt_not_in_source')
            if any(c.get('field') not in FACTS or c.get('op') not in ('eq', 'lt', 'le', 'in') for c in hit['conditions']):
                raise EvidencePackageError('unknown_condition_schema')
            if hit.get('strength') == 'unknown' or not hit.get('strength'):
                raise EvidencePackageError('unknown_recommendation_strength')
        elif hit['handling'] not in ('directory', 'unverified_task_adaptation'):
            raise EvidencePackageError('unknown_hit_handling')
    return value


def _valid_fact(key, value):
    if key == 'biopsy_context':
        return value in ('initial', 'prior_negative', 'known_cancer')
    if key == 'pi_rads':
        return type(value) is int and 1 <= value <= 5
    if key == 'psa_density':
        return type(value) in (int, float) and 0 <= value <= 10
    if key == 'dre':
        return value in ('negative', 'suspicious')
    return key == 'family_history_prostate_cancer' and type(value) is bool


def legal_context(access, data_dir):
    """No caller-provided fact dict; missing/unparseable/conflicting stays unknown.

    Text extraction accepts only a complete canonical three-line current-report
    representation. General report prose is unknown, even with numeric lines:
    nearby history/negation cannot safely be stripped by a line-prefix regex.
    """
    if type(access) is not InformationAccess:
        raise AccessDenied('trusted_information_access_required')
    if access.binding['task'] != 1:
        raise AccessDenied('evidence_package_task_not_supported')
    case = access.binding['case_id']
    if Path(case).name != case or case in ('.', '..'):
        raise AccessDenied('invalid_bound_case')
    folder = Path(data_dir) / case
    upfront = next((folder / n for n in ('prompt.json', 'structured-prompt.json') if (folder / n).is_file()), None)
    if upfront is None or file_sha(upfront) != access.binding['initial_source_sha256']:
        raise AccessDenied('upfront_source_identity_mismatch')
    raw = json.loads(upfront.read_text())
    if raw.get('case_id', case) != case:
        raise AccessDenied('cross_case')
    values = {k: [] for k in FACTS}
    def add(key, value, ref):
        if _valid_fact(key, value):
            values[key].append((value, ref))
    for key in FACTS:
        if key in raw:
            add(key, raw[key], {'source': 'bound_upfront', 'field': key, 'sha256': file_sha(upfront)})
    # These explicit legacy categorical values establish a history, not a
    # complete current management indication. Missing/None is never "initial".
    if raw.get('bx') in ('Positive', 'Negative'):
        add('biopsy_context', 'known_cancer' if raw['bx'] == 'Positive' else 'prior_negative',
            {'source': 'bound_upfront', 'field': 'bx', 'sha256': file_sha(upfront)})
    if raw.get('dre') in ('Normal', 'Negative', 'Nodus'):
        add('dre', 'suspicious' if raw['dre'] == 'Nodus' else 'negative',
            {'source': 'bound_upfront', 'field': 'dre', 'sha256': file_sha(upfront)})
    fields = access.snapshot()['visible_sections']
    visible = access.require_visible(case, fields)
    for field, payload in visible.items():
        ref = {'source': 'granted_cache', 'field': field, 'payload_digest': digest(payload)}
        if field == 'radiology_report':
            if isinstance(payload, dict) and set(payload) <= {'pi_rads', 'psa_density'}:
                for key in ('pi_rads', 'psa_density'):
                    if key in payload:
                        add(key, payload[key], ref)
            elif isinstance(payload, str):
                pattern = (r'IMPRESSION:\nPI-RADS ([1-5])\.\n'
                           r'PSA density: (\d+(?:\.\d+)?) ng/mL/(?:mL|cc)\.')
                match = re.fullmatch(pattern, payload.strip())
                if match:
                    add('pi_rads', int(match[1]), ref)
                    add('psa_density', float(match[2]), ref)
        # No unvalidated prose, pathology inference, sidecars or GT are read.
    state = {}
    for key, candidates in values.items():
        unique = {canonical(v) for v, _ in candidates}
        state[key] = {'status': 'known' if len(unique) == 1 else ('conflict' if unique else 'unknown'),
                      'value': candidates[0][0] if len(unique) == 1 else None,
                      'references': [r for _, r in candidates]}
    return {'case_id': case, 'snapshot_id': access.binding['snapshot_id'], 'facts': state,
            'visible_sections': fields, 'upfront_sha256': file_sha(upfront),
            'semantics': 'condition checks at retrieval time; not a diagnosis or automatic action'}


def check_condition(condition, facts):
    fact = facts[condition['field']]
    if fact['status'] != 'known':
        return {**condition, 'status': fact['status'], 'references': fact['references']}
    actual, expected, op = fact['value'], condition['value'], condition['op']
    result = {'eq': lambda: actual == expected, 'lt': lambda: actual < expected,
              'le': lambda: actual <= expected, 'in': lambda: actual in expected}[op]()
    return {**condition, 'status': 'matched' if result else 'unmet', 'references': fact['references']}


def append_audit(access, value):
    path = access.path.parent / 'evidence_package_audit.jsonl'
    with path.open('a') as handle:
        fcntl.flock(handle, fcntl.LOCK_EX)
        handle.write(canonical({'timestamp_unix': time.time(), **value}) + '\n')


class EvidencePackager:
    def __init__(self, config, access, data_dir):
        if type(access) is not InformationAccess:
            raise AccessDenied('trusted_information_access_required')
        required = {'overlay_path', 'overlay_sha256', 'max_consumer_bytes'}
        if not required <= set(config) or set(config) - required - {'tokenizer'}:
            raise EvidencePackageError('unknown_package_config')
        self.config = deepcopy(config)
        if type(config['max_consumer_bytes']) is not int or not 512 <= config['max_consumer_bytes'] <= 64000:
            raise EvidencePackageError('invalid_consumer_length_limit')
        if access.binding.get('evidence_package_id') != config['overlay_sha256']:
            raise EvidencePackageError('ledger_overlay_binding_mismatch')
        self.access, self.data_dir = access, Path(data_dir)
        self.overlay = load_overlay(config['overlay_path'], config['overlay_sha256'])
        if self.overlay['knowledge_snapshot_id'] != access.binding['knowledge_snapshot_id']:
            raise EvidencePackageError('knowledge_overlay_binding_mismatch')
        self.index = {x['text_sha256']: x for x in self.overlay['hits']}

    def validate_delivery(self, payload):
        """Apply length/identity checks to cached returns as well as fresh hits."""
        if payload.get('note', {}).get('overlay_id') != self.config['overlay_sha256']:
            raise EvidencePackageError('cached_package_identity_mismatch')
        if len(canonical(payload).encode()) > self.config['max_consumer_bytes']:
            raise EvidencePackageError('consumer_context_limit_exceeded')
        tokenizer = self.config.get('tokenizer')
        count = None
        if tokenizer is not None:
            if (set(tokenizer) != {'base_url', 'model_id', 'max_evidence_tokens'} or
                tokenizer['base_url'] not in ('http://127.0.0.1:8765', 'http://127.0.0.1:8766') or
                type(tokenizer['max_evidence_tokens']) is not int or tokenizer['max_evidence_tokens'] <= 0):
                raise EvidencePackageError('invalid_local_tokenizer_binding')
            try:
                # Only CPU tokenizer/metadata endpoints; never a model forward.
                with urllib.request.urlopen(tokenizer['base_url'] + '/props', timeout=15) as response:
                    props = json.load(response)
                if Path(props.get('model_path', '')).name != tokenizer['model_id']:
                    raise EvidencePackageError('tokenizer_model_identity_mismatch')
                request = urllib.request.Request(tokenizer['base_url'] + '/tokenize',
                    data=json.dumps({'content': canonical(payload), 'add_special': False, 'parse_special': True}).encode(),
                    headers={'Content-Type': 'application/json'})
                with urllib.request.urlopen(request, timeout=15) as response:
                    count = len(json.load(response)['tokens'])
            except EvidencePackageError:
                raise
            except Exception as error:
                raise EvidencePackageError('tokenizer_unavailable') from error
            if count > tokenizer['max_evidence_tokens']:
                raise EvidencePackageError('consumer_token_limit_exceeded')
        append_audit(self.access, {'event': 'delivery_ready', 'overlay_id': self.config['overlay_sha256'],
            'query_digest': digest(payload['query']), 'consumer_payload_digest': digest(payload),
            'consumer_bytes': len(canonical(payload).encode()), 'token_length': count,
            'token_measurement': 'loaded_local_model_cpu_tokenizer' if tokenizer is not None else 'unconfigured_unknown'})

    def package(self, query, hits):
        # Revalidate resources on every new backend result; no stale source pin.
        load_overlay(self.config['overlay_path'], self.config['overlay_sha256'])
        if not isinstance(query, str) or not query.strip() or not isinstance(hits, list):
            raise EvidencePackageError('malformed_retrieval')
        context = legal_context(self.access, self.data_dir)
        results, dispositions, seen = [], [], set()
        for rank, hit in enumerate(hits):
            if not isinstance(hit, dict) or not isinstance(hit.get('text'), str):
                raise EvidencePackageError('malformed_hit')
            key = hashlib.sha256(hit['text'].encode()).hexdigest()
            if key != hit.get('text_sha256') or hit.get('knowledge_snapshot_id') != self.access.binding['knowledge_snapshot_id']:
                raise EvidencePackageError('hit_identity_mismatch')
            rule = self.index.get(key)
            reason = 'unknown_source_binding' if rule is None else rule['handling']
            if key in seen:
                reason = 'duplicate_source'
            seen.add(key)
            disposition = {'rank': rank, 'text_sha256': key, 'handling': reason}
            if reason == 'conditional_reference':
                # Wrong known management population is excluded, not reclassified.
                stage = context['facts']['biopsy_context']
                if stage['status'] == 'known' and stage['value'] == 'known_cancer':
                    disposition['handling'] = 'scope_mismatch_known_cancer'
                else:
                    checks = [check_condition(c, context['facts']) for c in rule['conditions']]
                    status = ('unknown' if any(c['status'] in ('unknown', 'conflict') for c in checks)
                              else 'matched' if all(c['status'] == 'matched' for c in checks) else 'unmet')
                    source = self.overlay['sources'][rule['document_id']]
                    metadata = {'version': VERSION, 'overlay_id': self.config['overlay_sha256'],
                        'original_rank': rank, 'original_hit_sha256': key, 'document_id': rule['document_id'],
                        'source_version': source['version'], 'publisher': source['publisher'], 'source_url': source['url'],
                        'source_files': [{'sha256': f['sha256']} for f in source['files']],
                        'source_level': 'secondary_carrier_verified_primary_excerpt', 'content_type': 'conditional_recommendation',
                        'locator': rule['locator'], 'strength': rule['strength'], 'exceptions': rule['exceptions'],
                        'verification_status': 'primary_excerpt_whitespace_and_declared_layout_match',
                        'conditions': checks, 'condition_status_at_retrieval': status,
                        'context_digest_at_retrieval': digest(context), 'automatic_action_authorized': False,
                        'interpretation': 'Conditional source reference only. Unknown is not negative. Apply full conditions to current legally observed facts; no automatic patient action.'}
                    text = rule['primary_text']
                    results.append({'text': text, 'text_sha256': hashlib.sha256(text.encode()).hexdigest(),
                        'knowledge_snapshot_id': self.access.binding['knowledge_snapshot_id'],
                        'type': 'conditional_primary_excerpt', 'evidence_package': metadata})
                    disposition['condition_status'] = status
            dispositions.append(disposition)
        payload = {'query': query, 'results': results, 'note': {'evidence_package_version': VERSION,
            'overlay_id': self.config['overlay_sha256'], 'raw_hit_count': len(hits), 'returned_reference_count': len(results),
            'status': 'conditional_references' if results else 'no_verified_reference',
            'automatic_action_authorized': False}}
        size = len(canonical(payload).encode())
        audit = {'event': 'packaged', 'query_digest': digest(query), 'overlay_id': self.config['overlay_sha256'],
            'context': context, 'raw_hits': deepcopy(hits), 'dispositions': dispositions,
            'consumer_payload_digest': digest(payload), 'consumer_bytes': size, 'consumer_characters': len(canonical(payload)),
            'token_length': None, 'token_measurement': 'not measured here; model-specific context guard remains required'}
        if size > self.config['max_consumer_bytes']:
            append_audit(self.access, {**audit, 'event': 'context_limit_rejected'})
            raise EvidencePackageError('consumer_context_limit_exceeded')
        append_audit(self.access, audit)
        return payload


def verify_consumer(access, payload):
    """Called after grant/cache equality by the actual guarded model boundary."""
    identity = access.binding.get('evidence_package_id')
    note = payload.get('note')
    if not isinstance(note, dict) or note.get('overlay_id') != identity or note.get('evidence_package_version') != VERSION:
        raise AccessDenied('invalid_evidence_package_consumer_payload')
    append_audit(access, {'event': 'verified_consumer_payload', 'query_digest': digest(payload['query']),
        'overlay_id': identity, 'consumer_payload_digest': digest(payload),
        'consumer_bytes': len(canonical(payload).encode())})
