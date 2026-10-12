"""Offline review editor and strict human-annotation import."""
from __future__ import annotations
import csv
import hashlib
import json
from pathlib import Path
from .metrics import records, validate_gold


def create_review_pack(inputs, results, output_dir):
    output=Path(output_dir)
    output.mkdir(parents=True,exist_ok=True)
    works=records(inputs)
    ids=[r['work_id'] for r in works]
    if len(ids)!=len(set(ids)):
        raise ValueError('duplicate work in review pack')
    predictions=records(results)
    if any(r['work_id'] not in ids for r in predictions):
        raise ValueError('prediction refers to unknown review work')
    labels=sorted({l['label_id'] for r in predictions for l in r.get('labels',[])})
    taxonomy_path=Path('config/taxonomy.json')
    if taxonomy_path.exists():
        taxonomy=json.loads(taxonomy_path.read_text('utf-8'))
        labels=sorted(set(labels)|{l['label_id'] for l in taxonomy['labels']})
    partitions={}
    group_ids={}
    manifest_path=output/'split_manifest.json'
    if manifest_path.exists():
        manifest=json.loads(manifest_path.read_text('utf-8'))
        partitions={w:p for p,values in manifest['partitions'].items() for w in values}
        group_ids=manifest['group_ids']
    payload={'works':works,'predictions':predictions,'label_ids':labels,'partitions':partitions,'group_ids':group_ids}
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True,separators=(',',':'))
    payload['pack_sha256']=hashlib.sha256(encoded.encode()).hexdigest()
    existing=output/'review_pack.json'
    if existing.exists() and json.loads(existing.read_text('utf-8'))['pack_sha256']!=payload['pack_sha256']:
        raise ValueError('immutable review pack differs; use a new directory')
    existing.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
    # This is an unfilled task list, never gold or fabricated human labels.
    with (output/'review_tasks.csv').open('w',encoding='utf-8',newline='') as handle:
        writer=csv.writer(handle)
        writer.writerow(['work_id','title','partition','label_count','annotation_status'])
        for work in works:
            writer.writerow([work['work_id'],work.get('title',''),partitions.get(work['work_id'],'holdout'),len(labels),'pending'])
    data=json.dumps(payload,ensure_ascii=False).replace('<','\\u003c').replace('>','\\u003e').replace('&','\\u0026').replace('\u2028','\\u2028').replace('\u2029','\\u2029')
    html=_HTML.replace('__PAYLOAD__',data)
    (output/'review.html').write_text(html,encoding='utf-8')
    return {'html':str(output/'review.html'),'pack':str(existing),'tasks':str(output/'review_tasks.csv'),'works':len(works),'labels':len(labels),'annotation_status':'human_review_pending','pack_sha256':payload['pack_sha256']}


def import_annotations(path, review_pack_dir):
    """Validate imported JSON against frozen work/label/partition snapshot.

    This verifies declarations and integrity, not that someone really read a
    work. Independent review/adjudication remains a human procedure.
    """
    pack=json.loads((Path(review_pack_dir)/'review_pack.json').read_text('utf-8'))
    path=Path(path)
    content=path.read_text('utf-8-sig')
    if path.suffix=='.jsonl':
        annotations=[]
        for line_number,line in enumerate(content.splitlines(),1):
            if not line.strip():
                continue
            try:
                annotations.append(json.loads(line))
            except json.JSONDecodeError as error:
                raise ValueError(f'invalid annotation JSONL line {line_number}: {error.msg}') from error
    else:
        annotations=json.loads(content)
    if isinstance(annotations,dict):
        if annotations.get('pack_sha256')!=pack['pack_sha256']:
            raise ValueError('review pack checksum mismatch')
        annotations=annotations['annotations']
    by_work={w['work_id']:w for w in pack['works']}
    seen=set()
    for record in annotations:
        if record.get('provenance')!='human':
            raise ValueError('review import only accepts explicit human annotations')
        if record.get('work_id') not in by_work:
            raise ValueError('unknown work in annotation')
        validate_gold(record,set(pack['label_ids']))
        key=(record['work_id'],record['reviewer_id'],record['truth_level'])
        if key in seen:
            raise ValueError('duplicate reviewer/work/truth annotation')
        seen.add(key)
        partition=pack['partitions'].get(record['work_id'],'holdout')
        if record.get('partition',partition)!=partition:
            raise ValueError('partition mismatch; frozen split must not be reassigned')
        record['partition']=partition
        work=by_work[record['work_id']]
        for field in ('authors','series','original_language','first_publication_year','region'):
            record[field]=work.get(field)
        record['group_id']=pack['group_ids'].get(record['work_id'],record['work_id'])
        visible_evidence=[e for e in work.get('evidence',[]) if record['truth_level']=='work' or e.get('llm_input_allowed') is True]
        evidence_ids={e['evidence_id'] for e in visible_evidence}
        record['evidence_kind']='+'.join(sorted({e['kind'] for e in visible_evidence})) or 'unknown'
        for label in record.get('labels',[]):
            refs=label.get('evidence_ids',[])
            if not set(refs)<=evidence_ids:
                raise ValueError('unknown evidence reference')
        record['pack_sha256']=pack['pack_sha256']
    return annotations


_HTML='''<!doctype html><html lang="ja"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>小説分類の人手レビュー</title>
<style>body{font-family:system-ui;max-width:1100px;margin:auto;padding:20px;background:#f6f6f7}input,select,button,textarea{font:inherit;padding:6px}button{cursor:pointer}article,section{background:white;padding:18px;margin:12px 0;border-radius:8px}pre{white-space:pre-wrap}table{width:100%;border-collapse:collapse}td,th{padding:7px;border-bottom:1px solid #ddd}small{color:#555}.labels{overflow:auto}textarea{width:95%}</style>
<h1>人手レビュー</h1><p>入力資料への忠実性と作品内容の正しさを区別してください。未判定は空欄、判断できない項目は unknown を選択します。初期画面ではモデル判定を隠し、独立レビューを支援します。作業状態はこのブラウザに保存されます。</p>
<section><label>レビュー担当ID <input id="reviewer"></label> <label>評価対象 <select id="truth"><option value="evidence">入力資料への忠実性</option><option value="work">作品内容の正しさ</option></select></label><br><label><input type="checkbox" id="read">この作品を読んで理解している</label> <label><input type="checkbox" id="documented">十分な作品資料を確認した</label><br><label>作品内容の確認資料（URL等、改行区切り）<textarea id="sources" rows="2"></textarea></label><label><input type="checkbox" id="reveal">モデル判定を表示する（独立判定ではなくなります）</label></section>
<section><button id="prev">前の作品</button> <select id="work"></select> <button id="next">次の作品</button> <button id="export">入力済み判定をJSON保存</button> <span id="progress"></span></section>
<article><h2 id="title"></h2><pre id="bibliography"></pre><div id="evidence"></div></article><section class="labels"><table><thead><tr><th>ラベル</th><th>人手判定</th><th>関連度 0〜1</th><th>根拠ID（複数はカンマ）</th><th>モデル（任意）</th></tr></thead><tbody id="labels"></tbody></table></section><p id="status"></p>
<script type="application/json" id="payload">__PAYLOAD__</script><script>
const pack=JSON.parse(document.getElementById('payload').textContent), $=id=>document.getElementById(id), key='novel-review-'+pack.pack_sha256;let state={};try{state=JSON.parse(localStorage.getItem(key)||'{}')}catch(e){}let index=0;const started=Date.now();const pred=new Map(pack.predictions.map(r=>[r.work_id,r]));
for(let i=0;i<pack.works.length;i++){let o=document.createElement('option');o.value=i;o.textContent=(i+1)+'. '+pack.works[i].title;$('work').append(o)}
function current(){return pack.works[index]}function draft(){let w=current(),slot=w.work_id+'|'+$('truth').value;return state[slot]||(state[slot]={labels:{},read:false,documented:false,sources:'',exposed:false})}function save(){localStorage.setItem(key,JSON.stringify(state));$('progress').textContent=Object.values(state).filter(s=>Object.values(s.labels||{}).some(l=>l.decision)).length+' 件の作品・評価対象に判定あり'}
function render(){if(!pack.works.length)return;$('work').value=index;let w=current(),d=draft();$('title').textContent=w.title;$('bibliography').textContent=JSON.stringify({work_id:w.work_id,authors:w.authors,original_language:w.original_language,year:w.first_publication_year,region:w.region,partition:pack.partitions[w.work_id]||'holdout'},null,2);$('evidence').replaceChildren();for(let e of (w.evidence||[]).filter(e=>$('truth').value==='work'||e.llm_input_allowed===true)){let s=document.createElement('section'),h=document.createElement('strong'),p=document.createElement('pre');h.textContent=e.evidence_id+' / '+e.kind+' / '+e.source_url;p.textContent=e.text;s.append(h,p);$('evidence').append(s)}$('read').checked=d.read;$('documented').checked=d.documented;$('sources').value=d.sources;$('reveal').checked=d.exposed;$('labels').replaceChildren();let modeled=new Map(((pred.get(w.work_id)||{}).labels||[]).map(l=>[l.label_id,l]));for(let id of pack.label_ids){let row=document.createElement('tr'),name=document.createElement('td');name.textContent=id;row.append(name);let val=d.labels[id]||{decision:'',relevance:null,evidence_ids:[]};d.labels[id]=val;let td=document.createElement('td'),select=document.createElement('select');for(let choice of ['', 'present','absent','unknown']){let opt=document.createElement('option');opt.value=choice;opt.textContent=choice||'未判定';select.append(opt)}select.value=val.decision;select.onchange=()=>{val.decision=select.value;save()};td.append(select);row.append(td);td=document.createElement('td');let rel=document.createElement('input');rel.type='number';rel.min=0;rel.max=1;rel.step=.25;rel.style.width='75px';rel.value=val.relevance??'';rel.oninput=()=>{val.relevance=rel.value===''?null:Number(rel.value);save()};td.append(rel);row.append(td);td=document.createElement('td');let refs=document.createElement('input');refs.value=(val.evidence_ids||[]).join(',');refs.oninput=()=>{val.evidence_ids=refs.value.split(',').map(x=>x.trim()).filter(Boolean);save()};td.append(refs);row.append(td);td=document.createElement('td');td.textContent=d.exposed?JSON.stringify(modeled.get(id)||{}):'非表示';row.append(td);$('labels').append(row)}save()}
$('truth').onchange=render;$('work').onchange=()=>{index=Number($('work').value);render()};$('prev').onclick=()=>{index=Math.max(0,index-1);render()};$('next').onclick=()=>{index=Math.min(pack.works.length-1,index+1);render()};for(let id of ['read','documented','sources','reveal']){$(id).onchange=()=>{let d=draft();d.read=$('read').checked;d.documented=$('documented').checked;d.sources=$('sources').value;if($('reveal').checked)d.exposed=true;save();if(id==='reveal')render()}}
$('export').onclick=()=>{let reviewer=$('reviewer').value.trim(),truth=$('truth').value;if(!reviewer){$('status').textContent='担当IDを入力してください';return}let annotations=[];for(let w of pack.works){let d=state[w.work_id+'|'+truth];if(!d)continue;let labels=Object.entries(d.labels).filter(([id,l])=>l.decision).map(([id,l])=>({label_id:id,...l}));if(!labels.length)continue;let sources=d.sources.split(/\\n/).map(x=>x.trim()).filter(Boolean);if(truth==='work'&&!d.read&&!(d.documented&&sources.length)){$('status').textContent=w.title+' : 作品内容の確認根拠が必要です';return}if(labels.some(l=>l.relevance!==null&&(!Number.isFinite(l.relevance)||l.relevance<0||l.relevance>1))){$('status').textContent='関連度は0〜1です';return}annotations.push({work_id:w.work_id,provenance:'human',reviewer_id:reviewer,truth_level:truth,partition:pack.partitions[w.work_id]||'holdout',labels,has_read_work:d.read,sufficient_documentation:d.documented,work_truth_sources:sources,independent_review:!d.exposed,elapsed_session_seconds:(Date.now()-started)/1000,annotated_at:new Date().toISOString()})}let blob=new Blob([JSON.stringify({pack_sha256:pack.pack_sha256,annotations},null,2)],{type:'application/json'}),a=document.createElement('a');a.href=URL.createObjectURL(blob);a.download='human-annotations.json';a.click();URL.revokeObjectURL(a.href);$('status').textContent=annotations.length+'作品を保存。インポート検証後に評価へ使用してください'};render();
</script></html>'''
