"""Local aggregate-only autonomous recommendation comparison; no CLI/LLM launch."""
from __future__ import annotations
import argparse
from datetime import datetime, timezone
import hashlib
import html
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from recommend_book.recommendation.autonomous_evaluation import evaluate_nested, preregistration
from recommend_book.recommendation.narou import load_narou_reference


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_immutable(path,value):
    content=(json.dumps(value,ensure_ascii=False,indent=2,sort_keys=True,allow_nan=False)+'\n').encode('utf-8')
    if path.exists():
        if path.read_bytes()!=content:
            raise ValueError('Existing output differs; use a new output directory rather than overwrite')
        return
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as stream:stream.write(content)


def registration(contract_path,baseline_path,database,annotations,bridge):
    contract=json.loads(contract_path.read_text(encoding='utf-8'));expected=preregistration();rc=contract['recommendation']
    for field in ('outer_folds','inner_folds','outer_seed','inner_seed'):
        if rc[field]!=expected[field]:raise ValueError('Frozen recommendation contract mismatch')
    if rc['max_candidate_settings']<len(expected['settings']) or set(rc['method_families'])!={s['family'] for s in expected['settings']}:
        raise ValueError('Frozen setting family/maximum mismatch')
    if not rc['dataset_previously_observed'] or rc['fresh_holdout_claim']:
        raise ValueError('Previously observed input must not be described as fresh holdout')
    sources={'database':database,'annotations':annotations,'bridge':bridge}
    digests={key:sha(path) for key,path in sources.items()}
    baseline=json.loads(baseline_path.read_text(encoding='utf-8'))
    recorded={item['path']:item['sha256'] for item in baseline['artifacts']}
    for path in [contract_path,*sources.values()]:
        relative=path.resolve().relative_to(ROOT).as_posix()
        if recorded.get(relative)!=sha(path):raise ValueError('Source/contract differs from frozen baseline manifest')
    return {**expected,'contract_sha256':sha(contract_path),'baseline_manifest_sha256':sha(baseline_path),
        'source_sha256':digests,'feature_bridge_unchanged':True,'human_annotation_generated':False}


def render_report(report):
    records=[]
    for name,value in {**report['candidate_settings'], 'inner_selected_outer':report['methods']['selected'],
                        'random_baseline':report['methods']['random']}.items():
        macro=value['macro']
        records.append('<tr>'+''.join('<td>'+html.escape(str(item))+'</td>' for item in (
            name,round(macro['ndcg_at_k'],6),round(macro['precision_at_k'],6),
            round(value['supported_candidate_rate'],6),round(value['mean_boundary_tie_n'],2)))+'</tr>')
    selection=html.escape(json.dumps(report['selection']['recommended_setting'],ensure_ascii=False))
    return '<!doctype html><html lang="ja"><meta charset="utf-8"><title>Nested recommendation observation</title><style>body{font:16px sans-serif;max-width:1100px;margin:3em auto;padding:1em}td,th{border:1px solid #bbb;padding:.6em}table{border-collapse:collapse}</style><h1>既観測335評価の nested grouped CV</h1><p>ユーザー追加作業0。新しい独立holdout・将来評価・世界作品への移植品質を証明しません。人手分類gold0、校正未測定。全方式は同一eligible pool、unsupportedとnullを保持。</p><table><tr><th>Setting</th><th>nDCG@10</th><th>Precision@10</th><th>score coverage</th><th>rank10境界同点数</th></tr>'+''.join(records)+'</table><p>全候補outer値は記述用です。設定選定にはinner値だけを使用します。</p><p>full-source inner3CV deployment selection: <code>'+selection+'</code></p><p>5foldを独立試行と扱いません。JSONの条件付きgroup bootstrap区間は固定OOF順位の再標本化に限られ、再学習/選択・未知著者/シリーズ・過去feature・world移植の不確実性を含みません。</p><p><a href="report.json">Aggregate JSON / provenance</a> · <a href="selection.json">Selection API</a> · <a href="preregistration.json">Preregistered settings</a></p></html>'


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,default=ROOT/'data/reference-code/narou-personal-recommender/data/narou.sqlite')
    parser.add_argument('--annotations',type=Path,default=ROOT/'data/reference-code/narou-personal-recommender/data/processed/content-llm/annotations.json')
    parser.add_argument('--bridge',type=Path,default=ROOT/'config/recommendation-feature-bridge.json')
    parser.add_argument('--contract',type=Path,default=ROOT/'config/autonomous-evaluation-v1.json')
    parser.add_argument('--baseline',type=Path,default=ROOT/'data/autonomous-poc-v1/baseline-manifest.json')
    parser.add_argument('--output-dir',type=Path,default=ROOT/'data/autonomous-poc-v1/recommendation-evaluation')
    parser.add_argument('--register-only',action='store_true')
    args=parser.parse_args(argv)
    registered=registration(args.contract,args.baseline,args.database,args.annotations,args.bridge)
    path=args.output_dir/'preregistration.json'
    if args.register_only:
        write_immutable(path,registered)
        print(json.dumps({'status':'preregistered','settings_n':len(registered['settings']),'preregistration_sha256':sha(path)}))
        return 0
    if not path.is_file() or json.loads(path.read_text(encoding='utf-8'))!=registered:
        raise ValueError('Run --register-only in a separate completed invocation before evaluation')
    if any((args.output_dir/name).exists() for name in ('report.json','selection.json','report.html')):
        raise ValueError('Evaluation outputs already exist; use a new independently preregistered directory')
    reference=load_narou_reference(args.database,args.annotations,bridge=args.bridge)
    print(json.dumps({'status':'evaluating','rated_rows_n':len(reference['rows']),'settings_n':len(registered['settings'])}),flush=True)
    report=evaluate_nested(reference['rows'],settings=registered['settings'])
    if registered!=registration(args.contract,args.baseline,args.database,args.annotations,args.bridge):
        raise ValueError('Frozen inputs changed during evaluation')
    report['provenance']={'preregistration_sha256':sha(path),'contract_sha256':sha(args.contract),
        'baseline_manifest_sha256':sha(args.baseline),'source_sha256':registered['source_sha256'],
        'implementation_sha256':sha(ROOT/'src/recommend_book/recommendation/autonomous_evaluation.py'),
        'script_sha256':sha(Path(__file__)),'completed_at_utc':datetime.now(timezone.utc).isoformat(),
        'external_requests':0,'llm_invocations':0,'source_read_only':True}
    report['source_audit']=reference['audit']
    selection={**report['selection'],'preregistration_sha256':sha(path),'input_sha256':report['input_sha256'],
        'source_sha256':registered['source_sha256'],'fresh_holdout':False,'source_quality_verified':False}
    write_immutable(args.output_dir/'report.json',report)
    write_immutable(args.output_dir/'selection.json',selection)
    with (args.output_dir/'report.html').open('x',encoding='utf-8') as stream:stream.write(render_report(report))
    print(json.dumps({'status':'completed','rows_n':report['counts']['work_n'],
        'selected_setting':selection['recommended_setting']['id'],'methods':report['methods'],
        'report_sha256':sha(args.output_dir/'report.json')},ensure_ascii=False))
    return 0


if __name__=='__main__':
    try:
        raise SystemExit(main())
    except (ValueError,KeyError,TypeError,OSError):
        # Never expose exception messages containing private row identities.
        print('Autonomous evaluation rejected input/output/provenance; no private row details logged.',file=sys.stderr)
        raise SystemExit(2)
