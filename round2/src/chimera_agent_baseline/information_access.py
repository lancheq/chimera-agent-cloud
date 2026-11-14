"""Opt-in case-bound acquisition guard with cross-process transactional state.

Research proxy units count newly returned clinical sections, not medical prices.
The caller must bind snapshot_id to verified input bytes before constructing.
"""
from contextlib import contextmanager
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import time
import uuid

from chimera_agent_baseline.tools.definitions import TASK1_TOOLS,TASK2_TOOLS,TASK3_TOOLS


class AccessDenied(RuntimeError): pass


class BoundCaseDataStore:
    """Guarded fetch reads only the bound case, rechecking immutable bytes."""
    def __init__(self,data_dir,access):
        from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK
        self.access=access
        self.path=Path(data_dir)/access.binding['case_id']/CASE_DATA_FILENAMES_BY_TASK[access.binding['task']]

    def extract(self,case_id,fields):
        if case_id!=self.access.binding['case_id']:raise AccessDenied('cross_case')
        raw=self.path.read_bytes()
        if hashlib.sha256(raw).hexdigest()!=self.access.binding['snapshot_id']:raise ValueError('Source snapshot changed')
        record=json.loads(raw)
        if record.get('case_id',case_id)!=case_id:raise ValueError('Embedded case identity differs')
        return {'case_id':case_id,**{f:record[f] for f in fields if f in record}}


def canonical(x):return json.dumps(x,ensure_ascii=False,sort_keys=True,allow_nan=False)
def digest(x):return hashlib.sha256(canonical(x).encode()).hexdigest()


def tool_contract(task):
    tools={1:TASK1_TOOLS,2:TASK2_TOOLS,3:TASK3_TOOLS}[task]
    return {'schema_version':1,'task':task,'cost_unit':'research_new_section_unit_not_currency',
        'tools':{t.name:{'fields':list(t.fields),'new_section_units':1,'kind':'clinical_section'} for t in tools},
        'search_guidelines':{'kind':'knowledge_search','new_section_units':0,'admitted_attempt_units':1},
        'get_image_predictor':'blocked_until_modality_provenance_and_authorization_verified'}


class InformationAccess:
    def __init__(self,*,ledger_path,session_id,case_id,task,snapshot_id,budget_units,max_attempts,
                 initial_sections=None,initial_source_sha256=None,knowledge_snapshot_id=None):
        for name,value in [('budget_units',budget_units),('max_attempts',max_attempts)]:
            if type(value) is not int or value<0:raise ValueError('Nonnegative integer '+name+' required')
        if type(task) is not int or task not in (1,2,3):raise ValueError('Invalid task')
        if not all(isinstance(v,str) and v for v in (session_id,case_id,snapshot_id)):raise ValueError('Session, case and snapshot binding required')
        self.contract=tool_contract(task);self.path=Path(ledger_path);self.path.parent.mkdir(parents=True,exist_ok=True)
        initial_sections=deepcopy(initial_sections or {})
        allowed={f for t in self.contract['tools'].values() for f in t['fields']}
        if not set(initial_sections)<=allowed:raise ValueError('Unknown initial section')
        if initial_sections and not initial_source_sha256:raise ValueError('Initial sections require trusted upfront-source binding')
        self.binding={'session_id':session_id,'case_id':case_id,'task':task,'snapshot_id':snapshot_id,
            'budget_units':budget_units,'max_attempts':max_attempts,'contract':self.contract,
            'knowledge_snapshot_id':knowledge_snapshot_id,
            'initial_sections_digest':digest(initial_sections),'initial_source_sha256':initial_source_sha256}
        with self._tx() as db:
            db.execute('CREATE TABLE IF NOT EXISTS meta (binding TEXT NOT NULL, spent INTEGER NOT NULL, attempts INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS cache (key TEXT PRIMARY KEY, payload TEXT NOT NULL, source TEXT NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS pending (token TEXT PRIMARY KEY, keys TEXT NOT NULL, units INTEGER NOT NULL)')
            db.execute('CREATE TABLE IF NOT EXISTS events (seq INTEGER PRIMARY KEY AUTOINCREMENT, event TEXT NOT NULL)')
            row=db.execute('SELECT binding FROM meta').fetchone()
            if row and row[0]!=canonical(self.binding):raise ValueError('Ledger binding differs; do not reuse state across cases, resources or budget profiles')
            if not row:
                db.execute('INSERT INTO meta VALUES (?,0,0)',(canonical(self.binding),))
                for field,value in initial_sections.items():
                    db.execute('INSERT INTO cache VALUES (?,?,?)',('clinical:'+field,canonical(value),'initial_upfront'))
                self._event(db,{'status':'session_initialized','initial_fields':sorted(initial_sections)})

    @classmethod
    def from_config(cls,data_dir,config):
        from chimera_agent_baseline.tools.base import CASE_DATA_FILENAMES_BY_TASK,infer_task_from_data_dir
        options=deepcopy(config);case=options['case_id'];root=Path(data_dir).resolve()
        knowledge_manifest=options.pop('knowledge_manifest', None)
        if not Path(options['ledger_path']).is_absolute():raise ValueError('MCP ledger_path must be absolute across subprocesses')
        if not isinstance(case,str) or case in ('.','..') or Path(case).name!=case or '/' in case or '\\' in case:
            raise ValueError('Invalid bound case identifier')
        if options['task']!=infer_task_from_data_dir(root):raise ValueError('Wrong task binding')
        path=root/case/CASE_DATA_FILENAMES_BY_TASK[options['task']]
        if hashlib.sha256(path.read_bytes()).hexdigest()!=options['snapshot_id']:raise ValueError('Clinical input snapshot differs')
        if options.get('knowledge_snapshot_id'):
            if not knowledge_manifest:
                raise ValueError('Guarded MCP knowledge resource manifest validation requires a pinned manifest')
            from chimera_agent_baseline.knowledge_access import load_manifest
            load_manifest(knowledge_manifest, options['knowledge_snapshot_id'], require_alignment=True)
        elif knowledge_manifest:
            raise ValueError('Knowledge manifest requires an immutable snapshot id')
        initial=options.get('initial_sections') or {}
        if initial:
            prompt=next((root/case/n for n in ('prompt.json','structured-prompt.json') if (root/case/n).is_file()),None)
            if prompt is None or hashlib.sha256(prompt.read_bytes()).hexdigest()!=options.get('initial_source_sha256'):
                raise ValueError('Initial source binding differs')
            raw=json.loads(prompt.read_text())
            if any(k not in raw or raw[k]!=v for k,v in initial.items()):raise ValueError('Initial section not actually upfront')
        return cls(**options)

    @contextmanager
    def _tx(self):
        db=sqlite3.connect(self.path,timeout=10,isolation_level=None)
        try:
            db.execute('BEGIN IMMEDIATE');yield db;db.execute('COMMIT')
        except BaseException:
            if db.in_transaction:db.execute('ROLLBACK')
            raise
        finally:db.close()

    def _state(self,db):
        spent,attempts=db.execute('SELECT spent,attempts FROM meta').fetchone()
        return {'spent_units':spent,'reserved_units':db.execute('SELECT COALESCE(SUM(units),0) FROM pending').fetchone()[0],
            'admitted_attempts':attempts,'budget_units':self.binding['budget_units'],'max_attempts':self.binding['max_attempts']}

    def _event(self,db,x):
        value={'session_id':self.binding['session_id'],'case_id':self.binding['case_id'],
            'timestamp_unix':time.time(),'cost_unit':self.contract['cost_unit'],**x}
        db.execute('INSERT INTO events(event) VALUES (?)',(canonical(value),))

    def snapshot(self):
        with self._tx() as db:
            return {**self._state(db),'visible_sections':[r[0][len('clinical:'):] for r in db.execute("SELECT key FROM cache WHERE key LIKE 'clinical:%' ORDER BY key")],
                'pending_requests':db.execute('SELECT COUNT(*) FROM pending').fetchone()[0],
                'events':[json.loads(r[0]) for r in db.execute('SELECT event FROM events ORDER BY seq')]}

    def require_visible(self,case_id,fields):
        if case_id!=self.binding['case_id']:raise AccessDenied('cross_case')
        with self._tx() as db:
            values={r[0][len('clinical:'):]:json.loads(r[1]) for r in db.execute("SELECT key,payload FROM cache WHERE key LIKE 'clinical:%'")}
        if not set(fields)<=set(values):raise AccessDenied('unrevealed_fields')
        return {f:deepcopy(values[f]) for f in fields}

    def require_knowledge(self, query):
        key='knowledge:'+digest({'query':query,'snapshot':self.binding['knowledge_snapshot_id']})
        with self._tx() as db:
            row=db.execute('SELECT payload FROM cache WHERE key=?',(key,)).fetchone()
        if row is None:raise AccessDenied('unobserved_knowledge')
        return json.loads(row[0])

    def execute(self,tool,case_id,fetch,*,query=None):
        started=time.monotonic();token=uuid.uuid4().hex
        knowledge=tool=='search_guidelines'
        spec=self.contract['tools'].get(tool)
        fields=spec['fields'] if spec else []
        keys=['knowledge:'+digest({'query':query,'snapshot':self.binding['knowledge_snapshot_id']})] if knowledge else ['clinical:'+f for f in fields]
        denial=None;cached=None
        with self._tx() as db:
            before=self._state(db)
            if case_id!=self.binding['case_id']:denial='cross_case'
            elif not knowledge and not spec:denial='unknown_or_disabled_tool'
            elif knowledge and not self.binding['knowledge_snapshot_id']:denial='knowledge_snapshot_unverified'
            elif knowledge and (not isinstance(query,str) or not query.strip() or len(query)>10000):denial='invalid_query'
            elif before['admitted_attempts']>=self.binding['max_attempts']:denial='attempt_budget_exhausted'
            else:
                available={k:db.execute('SELECT payload FROM cache WHERE key=?',(k,)).fetchone() for k in keys}
                new=[k for k,v in available.items() if v is None]
                pending={k for row in db.execute('SELECT keys FROM pending') for k in json.loads(row[0])}
                units=0 if knowledge else len(new)
                if pending.intersection(keys):denial='request_pending'
                elif before['spent_units']+before['reserved_units']+units>self.binding['budget_units']:denial='information_budget_exhausted'
                else:
                    db.execute('UPDATE meta SET attempts=attempts+1')
                    if not new:
                        cached=json.loads(available[keys[0]][0]) if knowledge else {'case_id':case_id,**{f:json.loads(available['clinical:'+f][0]) for f in fields}}
                        self._event(db,{'request_id':token,'tool':tool,'status':'cache_returned','allowed':True,'success':True,'before':before,'after':self._state(db),'charged_units':0,'fields':fields,'elapsed_seconds':time.monotonic()-started})
                    else:
                        db.execute('INSERT INTO pending VALUES (?,?,?)',(token,canonical(keys),units))
                        self._event(db,{'request_id':token,'tool':tool,'status':'reserved','allowed':True,'success':None,'before':before,'after':self._state(db),'fields':fields,'query_digest':digest(query) if knowledge else None})
            if denial:self._event(db,{'request_id':token,'tool':tool,'status':denial,'allowed':False,'success':False,'before':before,'after':self._state(db),'fields':fields})
        if denial:raise AccessDenied(denial)
        if cached is not None:return cached
        try:
            result=fetch()
            if not isinstance(result,dict):raise ValueError('Payload must be a dict')
            if knowledge:
                if set(result)-{'query','results','note'} or result.get('query')!=query or not isinstance(result.get('results'),list):raise ValueError('Invalid knowledge payload')
                canonical(result)
            else:
                if result.get('case_id')!=case_id or set(result)-{'case_id','note',*fields}:raise ValueError('Wrong case or undeclared fields')
                canonical(result)
        except BaseException as exc:
            with self._tx() as db:
                before=self._state(db);db.execute('DELETE FROM pending WHERE token=?',(token,))
                self._event(db,{'request_id':token,'tool':tool,'status':'fetch_failed','allowed':True,'success':False,'failure_type':type(exc).__name__,
                    'before':before,'after':self._state(db),'charged_units':0,'elapsed_seconds':time.monotonic()-started})
            if not isinstance(exc,Exception):raise
            raise AccessDenied('fetch_failed') from None
        with self._tx() as db:
            before=self._state(db)
            if not db.execute('SELECT token FROM pending WHERE token=?',(token,)).fetchone():raise AccessDenied('missing_reservation')
            new_fields=[]
            if knowledge:
                db.execute('INSERT OR REPLACE INTO cache VALUES (?,?,?)',(keys[0],canonical(result),'knowledge_search'))
            else:
                for f in fields:
                    if f in result and not db.execute('SELECT key FROM cache WHERE key=?',('clinical:'+f,)).fetchone():
                        new_fields.append(f)
                        db.execute('INSERT INTO cache VALUES (?,?,?)',('clinical:'+f,canonical(result[f]),'successful_tool_return'))
                db.execute('UPDATE meta SET spent=spent+?',(len(new_fields),))
            db.execute('DELETE FROM pending WHERE token=?',(token,))
            self._event(db,{'request_id':token,'tool':tool,'status':'returned' if knowledge or new_fields else 'no_section_returned',
                'allowed':True,'success':True,'before':before,'after':self._state(db),'charged_units':len(new_fields),
                'fields':new_fields,'response_digest':digest(result),'response_bytes':len(canonical(result).encode()),'elapsed_seconds':time.monotonic()-started})
        return deepcopy(result)
