"""Read-only private Narou ratings adapter; features never become taxonomy gold."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import unicodedata

ROOT = Path(__file__).resolve().parents[3]
NATIVE_THEMES = {'power','growth','craft','protection','recognition','autonomy','belonging',
                 'parenting','romance','revenge','redo','exploration','comedy','cozy'}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def strict_json(raw):
    def pairs(items):
        result = {}
        for key,value in items:
            require(key not in result, 'Duplicate JSON property')
            result[key]=value
        return result
    def floating(value):
        number=float(value)
        require(math.isfinite(number), 'Non-finite JSON value')
        return number
    def constant(value):
        raise ValueError('Non-finite JSON value')
    return json.loads(raw,object_pairs_hook=pairs,parse_float=floating,parse_constant=constant)


def normalize_ncode(value):
    require(isinstance(value,str) and re.fullmatch(r'[Nn][0-9]{4}[A-Za-z]+',value), 'Invalid Narou identity')
    return value.lower()


def encoded_sources(sources):
    # Exact original content_analysis.encoded() serialization, including newline.
    return (json.dumps(sources,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode()


def validate_annotations(annotations, sources):
    require(isinstance(annotations,dict) and annotations.get('rating_blinded') is True, 'Rating-blinded annotation contract required')
    require(annotations.get('source_sha256')==sha(encoded_sources(sources)), 'Stale annotation source SHA')
    require(isinstance(annotations.get('prompt_version'),str) and annotations['prompt_version'], 'Annotation prompt provenance missing')
    novels=annotations.get('novels')
    require(isinstance(novels,list), 'Annotation novel array missing')
    expected={normalize_ncode(r['ncode']):r for r in sources}
    result={}
    fields={'ncode','summary','premise','goal','gender','tone','themes'}
    for row in novels:
        require(isinstance(row,dict) and set(row)==fields, 'Invalid annotation fields')
        code=normalize_ncode(row['ncode'])
        require(code in expected and code not in result, 'Annotation identity coverage/duplicate mismatch')
        require(row['gender'] in ('female','male','mixed','unknown') and row['tone'] in ('light','dark','mixed','unknown'), 'Invalid native gender/tone')
        require(all(isinstance(row[k],str) for k in ('summary','premise','goal')), 'Invalid native synopsis fields')
        themes=row['themes']
        require(isinstance(themes,list) and len(themes)<=5, 'Invalid native themes')
        seen=set()
        for theme in themes:
            require(isinstance(theme,dict) and set(theme)=={'id','prominence','source','evidence'}, 'Invalid native theme fields')
            tid=theme['id']
            require(tid in NATIVE_THEMES and tid not in seen and theme['prominence'] in ('central','secondary'), 'Invalid/duplicate native theme')
            seen.add(tid)
            require(theme['source'] in ('title','keyword','story'), 'Invalid native quote source')
            quote=theme['evidence']
            require(isinstance(quote,str) and 0<len(quote)<=70 and quote in expected[code][theme['source']], 'Native evidence quote does not match source')
        result[code]=row
    require(set(result)==set(expected), 'Annotation identity coverage mismatch')
    return result


def validate_bridge(bridge):
    require(isinstance(bridge,dict) and bridge.get('version')=='narou-world-feature-bridge-v1', 'Unsupported bridge version')
    require(isinstance(bridge.get('genre_codes'),dict) and isinstance(bridge.get('theme_rules'),list), 'Bridge mappings missing')
    weights=bridge.get('prominence_weights')
    require(isinstance(weights,dict) and set(weights)=={'central','secondary'}, 'Bridge prominence weights missing')
    for weight in [*weights.values(),bridge.get('metadata_genre_weight')]:
        require(type(weight) in (int,float) and math.isfinite(weight) and 0<=weight<=1, 'Invalid bridge feature weight')
    for rule in bridge['theme_rules']:
        require(isinstance(rule,dict) and rule.get('source_id') in NATIVE_THEMES and isinstance(rule.get('prominence'),list) and rule['prominence'] and all(p in weights for p in rule['prominence']) and isinstance(rule.get('target_id'),str), 'Invalid bridge theme rule')
    # This mapping is deliberately partial. Expanded bridges require a new
    # reviewed version rather than treating arbitrary native labels as world gold.
    allowed_rules={('growth','theme.growth'): {'central','secondary'},
                   ('revenge','theme.revenge'): {'central'}, ('romance','genre.romance'): {'central'}}
    require(all((r['source_id'],r['target_id']) in allowed_rules and set(r['prominence'])<=allowed_rules[r['source_id'],r['target_id']] for r in bridge['theme_rules']), 'Unapproved cross-domain theme mapping')
    approved={'101':'genre.romance','102':'genre.romance','201':'genre.fantasy','202':'genre.fantasy',
              '303':'genre.historical','304':'genre.mystery','305':'genre.horror',
              '401':'genre.science_fiction','402':'genre.science_fiction','403':'genre.science_fiction'}
    require(all(approved.get(k)==v for k,v in bridge['genre_codes'].items()), 'Unapproved cross-domain genre mapping')


def load_narou_reference(database, annotations, *, bridge=None):
    """Return all latest rating rows and aggregate audit, with original read state.

    Source files are immutable inputs. No titles, N codes, authors, quotes or
    individual ratings are printed, and failures contain no personal identifiers.
    """
    paths={'database':Path(database).resolve(strict=True),'annotations':Path(annotations).resolve(strict=True),
           'bridge':Path(bridge or ROOT/'config/recommendation-feature-bridge.json').resolve(strict=True)}
    wal=Path(str(paths['database'])+'-wal')
    require(not wal.exists() or wal.stat().st_size==0, 'Live SQLite WAL rejected; require saved checkpoint')
    raw={k:p.read_bytes() for k,p in paths.items()}
    hashes={k:sha(v) for k,v in raw.items()}
    annotation=strict_json(raw['annotations'])
    mapping=strict_json(raw['bridge'])
    validate_bridge(mapping)
    connection=sqlite3.connect(paths['database'].as_uri()+'?mode=ro&immutable=1',uri=True)
    connection.row_factory=sqlite3.Row
    try:
        connection.execute('PRAGMA query_only=ON')
        require(connection.execute('PRAGMA user_version').fetchone()[0]==1, 'Unsupported reference SQLite schema')
        observations_n=connection.execute('SELECT COUNT(*) FROM rating_observations').fetchone()[0]
        joined_n=connection.execute('SELECT COUNT(*) FROM rating_observations JOIN imports USING(snapshot_id)').fetchone()[0]
        require(observations_n==joined_n, 'Orphaned rating observation')
        records=[dict(r) for r in connection.execute('''WITH ranked AS (
          SELECT o.*, i.observed_at, i.imported_at,
                 ROW_NUMBER() OVER(PARTITION BY o.ncode ORDER BY i.observed_at DESC,i.imported_at DESC,i.snapshot_id DESC) AS rank
          FROM rating_observations o JOIN imports i USING(snapshot_id))
          SELECT r.*,s.read_status,s.completed_reading,s.dropped_at_episode,
                 m.writer,m.genre,m.keyword,m.story,m.global_point,m.raw_json,m.fetch_status
          FROM ranked r LEFT JOIN reading_status s USING(ncode)
          LEFT JOIN novel_metadata m USING(ncode) WHERE rank=1 ORDER BY r.ncode''')]
    except sqlite3.Error as exc:
        raise ValueError('Unsupported reference SQLite fields') from exc
    finally:
        connection.close()
    sources=[{k:r.get(k) or '' for k in ('ncode','title','keyword','story')} for r in records if r['fetch_status']=='success' and (r['story'] or '').strip()]
    native=validate_annotations(annotation,sources)
    rows=[]
    seen=set()
    unmapped_counts=Counter()
    for record in records:
        code=normalize_ncode(record['ncode'])
        require(code not in seen, 'Ambiguous canonical rating identity')
        seen.add(code)
        rating=record['my_rating']
        require(rating is None or type(rating) is int and 1<=rating<=5, 'Invalid observed personal rating')
        require(isinstance(record['title'],str), 'Invalid observed title')
        status=record['read_status'] or 'unknown'
        require(status in ('unknown','unread','reading','completed','dropped'), 'Invalid observed reading status')
        common_status={'unknown':'unknown','unread':'unread','reading':'read','completed':'read','dropped':'read'}[status]
        successful=record['fetch_status']=='success'
        metadata=strict_json(record['raw_json']) if successful and record['raw_json'] else {}
        require(isinstance(metadata,dict), 'Invalid metadata raw object')
        if 'ncode' in metadata:
            require(normalize_ncode(metadata['ncode'])==code, 'Metadata identity mismatch')
        userid=metadata.get('userid')
        authors=['narou_user:'+str(userid)] if type(userid) is int and userid>0 else []
        writer=record['writer'] if successful else None
        blocking=unicodedata.normalize('NFKC',writer).strip().casefold() if isinstance(writer,str) else ''
        features={}; provenance={}; native_features={}; unmapped=[]
        base={'mapping_version':mapping['version'],'bridge_sha256':hashes['bridge'],
              'human_taxonomy_truth':False,'raw_confidence':None,'raw_confidence_used_as_weight':False,
              'calibrated_confidence':None,'calibration_status':'not_measured',
              'database_sha256':hashes['database'],'annotations_sha256':hashes['annotations'],
              'source_sha256':annotation['source_sha256']}
        def add(label,weight,proof):
            features[label]=max(features.get(label,0),float(weight))
            provenance.setdefault(label,[]).append({**base,'feature_strength':float(weight),**proof})
        target=mapping['genre_codes'].get(str(record['genre'])) if successful else None
        if target:
            add(target,mapping['metadata_genre_weight'],{'source':'narou_declared_metadata','native_genre_code':record['genre']})
        item=native.get(code)
        if item:
            for theme in item['themes']:
                weight=mapping['prominence_weights'][theme['prominence']]
                native_features['narou.theme.'+theme['id']]=float(weight)
                matches=[r for r in mapping['theme_rules'] if r['source_id']==theme['id'] and theme['prominence'] in r['prominence']]
                if not matches:
                    unmapped.append(theme['id']);unmapped_counts[theme['id']]+=1
                for rule in matches:
                    add(rule['target_id'],weight,{'source':'narou_llm_synopsis_annotation','prompt_version':annotation['prompt_version'],
                        'review_version':annotation.get('review_version'),'review_is_human_taxonomy_gold':False,
                        'native_theme_id':theme['id'],'native_prominence':theme['prominence'],'relation':rule.get('relation'),
                        'evidence':{'source':theme['source'],'quote':theme['evidence']}})
        popularity=record['global_point'] if successful and type(record['global_point']) is int and record['global_point']>=0 else None
        rows.append({'work_id':'narou:'+code,'title':record['title'],'features':features,'native_features':native_features,
                     'unmapped_native_theme_ids':sorted(unmapped),'rating':rating,'author_ids':authors,
                     'author_group_keys':['narou_writer_name:'+blocking] if blocking else [],'series_ids':[],
                     'read_status':common_status,'source_read_status':status,'external_ids':[{'source':'narou','value':code}],
                     'feature_provenance':provenance,'popularity':popularity,
                     'provenance':{**base,'rating_observation':{k:record.get(k) for k in ('snapshot_id','observed_at','imported_at','first_rated_at','last_rated_at')},
                         'original_read_status':status,'completed_reading':record['completed_reading'],
                         'dropped_at_episode':record['dropped_at_episode'],'rated_history_exclusion':rating is not None,
                         'has_read_whole_work':None,'author_identity':'explicit_api_userid' if authors else 'unknown',
                         'author_group_key_is_canonical_identity':False,'series_identity':'unknown',
                         'metadata_fetch_status':record['fetch_status'],'native_annotation_available':item is not None}})
    require(all(sha(paths[k].read_bytes())==h for k,h in hashes.items()), 'Reference source changed during adapter load')
    require(not wal.exists() or wal.stat().st_size==0, 'SQLite WAL appeared during adapter load')
    return {'rows':sorted(rows,key=lambda r:r['work_id']), 'audit':{
        'version':'narou-reference-adapter-v1','source_sha256':hashes,'database_sha256':hashes['database'],
        'annotations_source_sha256':annotation['source_sha256'],'annotation_source_verified':True,'database_unchanged':True,
        'observations_n':observations_n,'latest_rows_n':len(rows),'duplicate_observations_reduced_n':observations_n-len(rows),
        'latest_order':'observed_at DESC, imported_at DESC, snapshot_id DESC',
        'annotation_n':len(native),'missing_annotation_n':len(rows)-len(native),
        'missing_rating_n':sum(r['rating'] is None for r in rows),'rated_history_exclusion_n':sum(r['rating'] is not None for r in rows),
        'missing_first_rated_at_n':sum(not r['first_rated_at'] for r in records),'missing_last_rated_at_n':sum(not r['last_rated_at'] for r in records),
        'explicit_author_identity_n':sum(bool(r['author_ids']) for r in rows),
        'author_name_blocking_key_n':sum(bool(r['author_group_keys']) for r in rows),'series_identity_n':0,
        'empty_shared_features_n':sum(not r['features'] for r in rows),'unmapped_native_theme_counts':dict(sorted(unmapped_counts.items())),
        'mapping_version':mapping['version'],'human_taxonomy_truth':False,'calibration_fitted':False,
        'limits':['Personal ratings are observations, not new-taxonomy human labels.',
                  'First/last rating dates do not date retrospectively fetched metadata or annotations.',
                  'Writer-name keys block evaluation leakage conservatively; they are not canonical author identities.',
                  'Feature omission remains unknown; no absent decision or confidence is inferred.',
                  'Original read status is retained; common read means encountered, not whole-work completion.',
                  'No explicit stable series identity exists in this source contract.']}}
