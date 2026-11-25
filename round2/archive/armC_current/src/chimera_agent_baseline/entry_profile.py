"""Trusted, code-owned entry approvals. There is no approved submission profile.

A caller cannot approve a profile by editing YAML or setting an environment
variable. Synthetic tests inject a registry explicitly; those approvals never
permit load_model(). D01-D05 and a real-generation budget remain external gates.
"""
from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping

from chimera_agent_baseline.runtime_audit import RuntimeFatal, content_digest


@dataclass(frozen=True)
class EntryProfile:
    name: str
    config_path: str
    config_sha256: str
    effective_digest: str
    sources: tuple[tuple[str, str], ...]
    environment: tuple[tuple[str, str], ...]
    runtime_flags: tuple[tuple[str, object], ...]
    audit_root: str
    max_attempts: int
    max_reserved_tokens: int
    purpose: str = 'synthetic_test_only'


# Deliberately empty until planning approves a complete submission profile.
TRUSTED_PROFILES: Mapping[str, EntryProfile] = {}


def effective_config(cfg):
    from omegaconf import OmegaConf
    effective = OmegaConf.to_container(cfg, resolve=True)
    # These two paths are allocated by the entry adapter, never supplied by
    # a profile. Stable markers permit verification across fresh temp dirs.
    effective['paths']['data_root'] = '<entry-owned-input>'
    effective['paths']['output_dir'] = '<entry-owned-output>'
    return effective


def effective_environment():
    # Bind all CHIMERA switches, including import-time clinical switches.
    return tuple(sorted((k, v) for k, v in os.environ.items()
                        if k.startswith('CHIMERA_') and k != 'CHIMERA_OUTPUT_DIR'))


def source_identity():
    root = Path(__file__).resolve().parents[2]
    paths = [root / 'inference.py', root / 'reasoning_align.py',
             *sorted((root / 'src/chimera_agent_baseline').rglob('*.py'))]
    return tuple((str(p), hashlib.sha256(p.read_bytes()).hexdigest()) for p in paths)


def runtime_flags():
    from chimera_agent_baseline.agent import form_fill
    names = ('_T1_DL_MODE', '_T1_DECISION_LAYER', '_WEIGHT_TABLE_ENABLED', '_CONF_DOWNGRADE',
             '_FORCE_CONF_CLEAR', '_PREDICTOR_NOTE_ENABLED', '_T2_KEEP_WW', '_PRE_CALIB_DUMP',
             '_PROMOTE_NOT_USED', '_DEMOTE_UNGROUNDABLE', '_T1_WEIGHT_KEEP', '_REVEAL_DENY_T1',
             '_CONF_CALIB_ENABLED', '_CONF_CALIB_THRESHOLD')
    def freeze(value):
        return tuple(sorted(value)) if isinstance(value, (set, frozenset)) else value
    return tuple((name, freeze(getattr(form_fill, name))) for name in names)


def verify_profile(cfg, config_path: Path, *, registry=None):
    requested = os.environ.get('CHIMERA_ENTRY_PROFILE')
    if not requested:
        return None  # legacy default unchanged; not an approved submission
    profiles = TRUSTED_PROFILES if registry is None else registry
    profile = profiles.get(requested)
    if profile is None or profile.name != requested:
        raise RuntimeFatal('Unapproved entry profile')
    verify_identity(profile, cfg, config_path)
    return profile


def verify_identity(profile, cfg, config_path, *, host_smoke=False, case_pilot=False, benchmark=False):
    path = Path(config_path).resolve()
    if str(path) != profile.config_path or hashlib.sha256(path.read_bytes()).hexdigest() != profile.config_sha256:
        raise RuntimeFatal('Entry configuration path/SHA mismatch')
    if content_digest(effective_config(cfg)) != profile.effective_digest:
        raise RuntimeFatal('Effective entry configuration mismatch')
    if effective_environment() != profile.environment:
        raise RuntimeFatal('Effective runtime switches mismatch')
    if sum((bool(host_smoke), bool(case_pilot), bool(benchmark))) > 1:
        raise RuntimeFatal('Entry scopes are mutually exclusive')
    expected_purpose = ('adaptive_case_pilot' if case_pilot else
                        'adaptive_host_smoke' if host_smoke else
                        'adaptive_real_case_benchmark' if benchmark else 'synthetic_test_only')
    if profile.purpose != expected_purpose:
        raise RuntimeFatal('No real-generation/submission profile approved')
    if type(profile.max_attempts) is not int or profile.max_attempts < 0 or type(profile.max_reserved_tokens) is not int or profile.max_reserved_tokens < 0:
        raise RuntimeFatal('Invalid finite request budget')
    # Hashes bind workspace files and imported module locations. A shadowed
    # package must not pass by hashing unused files in the checkout.
    import sys
    bound_paths = {name for name, _ in profile.sources}
    for name, module in tuple(sys.modules.items()):
        if name.startswith('chimera_agent_baseline.') or name in {'inference', 'reasoning_align'}:
            actual = getattr(module, '__file__', None)
            if actual and str(Path(actual).resolve()) not in bound_paths:
                raise RuntimeFatal('Imported module path mismatch: ' + name)
    if profile.sources != source_identity():
        raise RuntimeFatal('Entry source SHA mismatch')
    if profile.runtime_flags != runtime_flags():
        raise RuntimeFatal('Loaded runtime switches mismatch')
    audit = Path(profile.audit_root)
    if not audit.is_absolute():
        raise RuntimeFatal('Private audit sink must be absolute')
