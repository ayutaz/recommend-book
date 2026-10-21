from __future__ import annotations
import json, re, sys, traceback
from datetime import datetime, timezone
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from collect_scale_content_window_v1 import collect, existing_content_qids as old_ledger, RawStore as BaseRawStore

BASE = Path(__file__).resolve().parents[1]
OLD = BASE / 'data/autonomous-next-v3/scale-30000-v1'
V19 = BASE / 'data/autonomous-next-v3/scale-100000-v1/content'
CATALOGUE = BASE / 'data/autonomous-next-v3/scale-v1/catalogue.sqlite'
SHARED = OLD / 'shared-content-fetch-v1'
STATE = V19 / 'worker-v1-state.json'

class ReuseRawStore(BaseRawStore):
    def __init__(self, _ignored):
        super().__init__(SHARED)

def qids_from_text(path: Path) -> set[str]:
    result=set()
    if not path.exists(): return result
    try:
        text=path.read_text(encoding='utf-8', errors='ignore')
    except OSError: return result
    result.update(re.findall(r'wd:(Q\d+)', text))
    result.update(re.findall(r'"qid"\s*:\s*"(Q\d+)"', text))
    result.update(re.findall(r'"source_id"\s*:\s*"(Q\d+)"', text))
    return result

def combined_exclusions(_root=None) -> set[str]:
    q=old_ledger(OLD)
    for p in OLD.glob('classification-sources/**/ordered-inputs.jsonl'):
        q |= qids_from_text(p)
    for p in V19.rglob('*'):
        if p.is_file() and (p.name in {'eligible-source-pins.jsonl','ordered-inputs.jsonl','native-records.jsonl','records.jsonl','outcomes.json','selections.json'} or p.name.endswith('manifest.json')):
            q |= qids_from_text(p)
    return q

def save_state(**extra):
    state={'schema_version':'scale-100000-content-worker-v1','updated_at_utc':datetime.now(timezone.utc).isoformat(),'catalogue':str(CATALOGUE),'shared_raw_store':str(SHARED),'old_outputs_modified':False,'model_calls':0,'web_requests':extra.pop('web_requests',0),**extra}
    STATE.write_text(json.dumps(state,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')


def completed_output(output: Path) -> bool:
    """Only a valid terminal collector manifest marks a window complete."""
    manifest = output / 'manifest.json'
    if not output.is_dir() or not manifest.is_file():
        return False
    try:
        value = json.loads(manifest.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return False
    counts = value.get('status_counts', {})
    return (
        value.get('version') == 'scale-content-acquisition-window-v1'
        and value.get('requested') == value.get('selected_authorities') == 100
        and value.get('accepted_records') == counts.get('accepted')
        and sum(counts.values()) == value.get('requested')
        and value.get('http_max_attempts') == 1
        and value.get('old_sources_modified') is False
    )

def main():
    # Patch only this new runner's imported module globals; old script remains
    # untouched, and importing this module cannot contaminate other tests.
    import collect_scale_content_window_v1 as worker
    worker.existing_content_qids = combined_exclusions
    worker.RawStore = ReuseRawStore

    V19.mkdir(parents=True,exist_ok=True)
    completed=[]
    for index in range(1,101):
        start=(index-1)*100+10001; end=start+99
        output=V19/f'content-window-{start:05d}-{end:05d}'
        # A partial directory is never a completion marker.  Retry is only
        # possible after the collector's own durable manifest is present.
        if completed_output(output):
            save_state(status='skipped_existing_output',completed_windows=len(completed),last_output=str(output)); continue
        save_state(status='running',current_window=index,target_windows=100,completed_windows=len(completed),last_output=None,exclusions=len(combined_exclusions()))
        try:
            manifest=collect(catalogue=CATALOGUE,output=output,limit=100)
            completed.append({'window':index,'output':str(output),'requested':manifest.get('requested'),'accepted':manifest.get('accepted_records'),'network_requests':manifest.get('network_requests'),'status_counts':manifest.get('status_counts')})
            save_state(status='running',current_window=index,target_windows=100,completed_windows=len(completed),last_output=str(output),last_manifest=manifest,web_requests=sum(x['network_requests'] for x in completed))
            print(json.dumps(completed[-1],ensure_ascii=False),flush=True)
        except Exception as exc:
            save_state(status='failed',current_window=index,target_windows=100,completed_windows=len(completed),last_output=str(output),error=repr(exc),traceback=traceback.format_exc(),web_requests=sum(x['network_requests'] for x in completed))
            print(traceback.format_exc(),flush=True)
            raise
    save_state(status='complete',target_windows=100,completed_windows=len(completed),completed=completed,web_requests=sum(x['network_requests'] for x in completed))


if __name__ == '__main__':
    main()
