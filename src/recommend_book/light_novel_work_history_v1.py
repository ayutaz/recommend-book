"""Read-only independent world/LN history checkpoints for fresh work calls."""
import hashlib
import json
from pathlib import Path
import re
import sqlite3

VERSION = 'light-novel-work-history-audit-v1'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _identity(value):
    if not isinstance(value, str):return None
    # Historical series/work type changes cause a HOLD only; never feature reuse.
    match = re.fullmatch(r'(?:ln[sw]:wd:|wd:)?(Q[1-9][0-9]*)', value)
    return 'lnw:wd:' + match[1] if match else None


def _ids(value):
    result = set()
    if isinstance(value, list):
        for row in value:result.update(_ids(row))
    elif isinstance(value, dict):
        for key, row in value.items():
            if key in ('work_id', 'entry_id', 'series_id'):
                identity = _identity(row)
                if identity:result.add(identity)
            elif key in ('work_ids', 'entry_ids', 'series_ids') and isinstance(row, list):
                result.update(identity for v in row if (identity := _identity(v)))
            else:result.update(_ids(row))
    return result


def observe(selected_ids, *, world_database, ln_database, run_roots, reservation_roots,
            exclude_reservations=(), exclude_runs=()):
    selected = set(selected_ids)
    if not 1 <= len(selected) <= 100 or any(_identity(i) != i for i in selected):
        raise ValueError('History requires finite canonical work identities')
    if not run_roots or not reservation_roots:
        raise ValueError('World/LN run and reservation roots must be explicitly covered')
    tables, blocked = {}, set()
    for name, database, query, args in (
        ('world', world_database, 'SELECT qid,manifest_path,manifest_sha256 FROM native_classifications WHERE qid IN (' + ','.join('?' for _ in selected)+')', [i.split(':')[-1] for i in sorted(selected)]),
        ('ln', ln_database, 'SELECT entry_id,manifest_path,manifest_sha256 FROM features WHERE entry_id IN (' + ','.join('?' for _ in range(len(selected)*2))+')', sorted(selected)+[i.replace('lnw:','lns:',1) for i in sorted(selected)]),
    ):
        db = sqlite3.connect(Path(database).resolve().as_uri()+'?mode=ro',uri=True)
        db.execute('PRAGMA query_only=ON')
        rows = [list(r) for r in db.execute(query,args)];db.close()
        tables[name] = {'database':str(Path(database).resolve()),'selected_rows':rows}
        blocked.update('lnw:wd:'+r[0] if name=='world' else _identity(r[0]) for r in rows)
    paths = set()
    for raw in run_roots:
        root = Path(raw).resolve(strict=True)
        if not root.is_dir():raise ValueError('History run root must be a directory')
        for pattern in ('manifest.json','inputs.json','inputs.jsonl'):
            paths.update(root.rglob(pattern))
    excluded = {str(Path(p).resolve()) for p in exclude_reservations}
    excluded_runs = [Path(p).resolve() for p in exclude_runs]
    for raw in reservation_roots:
        root = Path(raw).resolve(strict=True)
        if not root.is_dir():raise ValueError('History reservation root must be a directory')
        paths.update(root.rglob('*.json'))
    if len(paths)>50000:raise ValueError('History scan exceeds finite file bound')
    artifacts, hits = {}, []
    for path in sorted(paths):
        if str(path) in excluded:continue
        if any(path.is_relative_to(root) for root in excluded_runs):continue
        raw = path.read_bytes()
        if len(raw)>64*1024*1024:raise ValueError('History document exceeds finite byte bound')
        values = [json.loads(line) for line in raw.splitlines() if line.strip()] if path.suffix=='.jsonl' else [json.loads(raw)]
        matches = set().union(*(_ids(v) for v in values)) & selected
        artifacts[str(path)] = hashlib.sha256(raw).hexdigest()
        if matches:hits.append({'path':str(path),'work_ids':sorted(matches)});blocked.update(matches)
    return {'version':VERSION,'selected_work_ids':sorted(selected),'tables':tables,
        'run_roots':[str(Path(p).resolve()) for p in run_roots],
        'reservation_roots':[str(Path(p).resolve()) for p in reservation_roots],
        'artifact_sha256':artifacts,'history_hits':hits,'blocked_work_ids':sorted(blocked)}


def verify(path, pin, selected_ids):
    if sha(path)!=pin:raise ValueError('Work history audit SHA differs')
    audit=json.loads(Path(path).read_bytes())
    if audit.get('version')!=VERSION or audit.get('selected_work_ids')!=sorted(selected_ids):
        raise ValueError('Work history audit coverage differs')
    if set(audit.get('tables',{}))!={'world','ln'} or not audit.get('run_roots') or not audit.get('reservation_roots'):
        raise ValueError('Independent world/LN history coverage absent')
    blocked=set()
    for name,spec in audit['tables'].items():
        for row in spec['selected_rows']:
            blocked.add('lnw:wd:'+row[0] if name=='world' else _identity(row[0]))
    hits=[]
    for raw,expected in audit['artifact_sha256'].items():
        if sha(raw)!=expected:raise ValueError('Prior work history artifact changed')
        p=Path(raw);data=p.read_bytes()
        values=[json.loads(l) for l in data.splitlines() if l.strip()] if p.suffix=='.jsonl' else [json.loads(data)]
        match=set().union(*(_ids(v) for v in values)) & set(selected_ids)
        if match:hits.append({'path':raw,'work_ids':sorted(match)});blocked.update(match)
    if (sorted(hits,key=lambda r:r['path'])!=sorted(audit['history_hits'],key=lambda r:r['path'])
            or sorted(blocked)!=audit['blocked_work_ids']):
        raise ValueError('Work history blocked identity projection differs')
    if blocked:raise ValueError('Previously classified/reserved work requires verified reuse, never fresh calls')
    return audit


def recheck_current(audit, *, exclude_reservations=(), exclude_run_roots=()):
    current=observe(audit['selected_work_ids'],world_database=audit['tables']['world']['database'],
        ln_database=audit['tables']['ln']['database'],
        run_roots=audit['run_roots'],reservation_roots=audit['reservation_roots'],
        exclude_reservations=exclude_reservations,exclude_runs=exclude_run_roots)
    if current['blocked_work_ids']:
        raise ValueError('Current world/LN work history prevents new model calls')
