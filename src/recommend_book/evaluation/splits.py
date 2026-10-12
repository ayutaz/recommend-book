"""Frozen author/series connected-group partition manifests."""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
from .metrics import records


def _identity_map(items, group_identities):
    ids={item['work_id'] for item in items}
    if group_identities is None:
        return None
    if not isinstance(group_identities,dict):
        raise ValueError('group identities must be a work-to-token mapping')
    if set(group_identities)-ids:
        raise ValueError('unknown work in group identities')
    normalized={}
    for work in sorted(ids):
        values=group_identities.get(work,[])
        if not isinstance(values,(list,tuple)) or any(not isinstance(token,str) or not token.strip() for token in values):
            raise ValueError('identity tokens must be a list of nonempty strings')
        normalized[work]=sorted({token.strip() for token in values})
    return normalized


def connected_groups(inputs, group_identities=None):
    items=records(inputs)
    identities=_identity_map(items,group_identities)
    parent={r['work_id']:r['work_id'] for r in items}
    def find(key):
        while parent[key]!=key:
            parent[key]=parent[parent[key]]
            key=parent[key]
        return key
    def union(a,b):
        a,b=find(a),find(b)
        if a!=b:
            parent[max(a,b)]=min(a,b)
    owners={}
    for item in sorted(items,key=lambda x:x['work_id']):
        keys=[('author',str(author).strip().casefold()) for author in (item.get('authors') or []) if str(author).strip()]
        if item.get('series'):
            keys.append(('series',item['series'].strip().casefold()))
        if identities is not None:
            keys.extend(('identity',token) for token in identities[item['work_id']])
        for key in keys:
            if key in owners:
                union(item['work_id'],owners[key])
            else:
                owners[key]=item['work_id']
    return {key:find(key) for key in parent}


def _subset(groups,target):
    if target==0:
        return []
    reachable={0:[]}
    for index,group in enumerate(groups):
        for total,chosen in list(reachable.items())[::-1]:
            new=total+len(group)
            if new<=target and new not in reachable:
                reachable[new]=chosen+[index]
        if target in reachable:
            return reachable[target]
    return None


def create_split_manifest(inputs, output_dir, *, sizes=(150,100,150), seed=1729, group_identities=None, language_quotas=None, balancing_attempts=64):
    """Select exact sample sizes without dividing connected author/series groups.

    Does not silently shrink an impossible design. Existing manifest and full
    input snapshot and optional authority identity digests must agree; changes
    require a new output directory. Display-name fallback remains conservative.
    """
    items=sorted(records(inputs),key=lambda x:x['work_id'])
    ids=[r['work_id'] for r in items]
    if len(ids)!=len(set(ids)):
        raise ValueError('duplicate work id in split input')
    if len(sizes)!=3 or any(not isinstance(size,int) or size<=0 for size in sizes):
        raise ValueError('three positive partition sizes required')
    if language_quotas is not None:
        names=('dev','calibration','holdout')
        if not isinstance(language_quotas,dict) or set(language_quotas)!=set(names):
            raise ValueError('language quotas require all three partitions')
        if any(not isinstance(language_quotas[name],dict) or set(language_quotas[name])!={'ja','en','other'} or
               any(not isinstance(value,int) or isinstance(value,bool) or value<0 for value in language_quotas[name].values()) or
               sum(language_quotas[name].values())!=size for name,size in zip(names,sizes)):
            raise ValueError('language quota counts must be nonnegative integers summing to partition sizes')
        language_quotas={name:{language:language_quotas[name][language] for language in ('ja','en','other')} for name in names}
        if not isinstance(balancing_attempts,int) or isinstance(balancing_attempts,bool) or not 1<=balancing_attempts<=256:
            raise ValueError('balancing attempts must be between 1 and 256')
    identities=_identity_map(items,group_identities)
    hash_input=items if identities is None else {'inputs':items,'group_identities':identities}
    digest=hashlib.sha256(json.dumps(hash_input,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    path=Path(output_dir)/'split_manifest.json'
    if path.exists():
        saved=json.loads(path.read_text('utf-8'))
        hashable={key:value for key,value in saved.items() if key!='manifest_sha256'}
        actual=hashlib.sha256(json.dumps(hashable,ensure_ascii=False,indent=2).encode()).hexdigest()
        if saved.get('manifest_sha256')!=actual:
            raise ValueError('immutable split manifest checksum invalid')
        if saved['input_sha256']!=digest or saved['seed']!=seed or saved['sizes']!=list(sizes):
            raise ValueError('immutable split manifest differs; use a new evaluation version/directory')
        if saved.get('language_quotas')!=language_quotas or (language_quotas is not None and saved['balancing']['candidate_attempts']!=balancing_attempts+1):
            raise ValueError('immutable split sampling design differs; use a new directory')
        return saved
    if len(items)<sum(sizes):
        raise ValueError(f'requires {sum(sizes)} works; only {len(items)} available')
    group_map=connected_groups(items,group_identities=identities)
    grouped=defaultdict(list)
    for work in ids:
        grouped[group_map[work]].append(work)
    groups=list(grouped.values())
    balancing=None
    if language_quotas is not None:
        from .sampling import balanced_partitions
        partitions,balancing=balanced_partitions(items,groups,language_quotas,seed,_subset,balancing_attempts)
    # Randomize within rare language/year strata and round-robin strata. This
    # encourages diverse partitions without pretending population sampling.
    by_id={r['work_id']:r for r in items}
    strata=defaultdict(list)
    for group in groups:
        row=by_id[group[0]]
        year=row.get('first_publication_year')
        band='unknown' if year is None else 'pre1900' if year<1900 else '1900-1949' if year<1950 else '1950-1999' if year<2000 else '2000+'
        strata[(row.get('original_language') or 'unknown',band)].append(group)
    rng=random.Random(seed)
    for values in strata.values():
        rng.shuffle(values)
    ordered=[]
    while any(strata.values()):
        for key in sorted(strata):
            if strata[key]:
                ordered.append(strata[key].pop())
    # Retry deterministic rotations when one sequential subset creates a dead end.
    if language_quotas is None:
        partitions=None
    for attempt in range(0 if language_quotas is not None else min(100,max(1,len(ordered)))):
        available=ordered[attempt:]+ordered[:attempt]
        trial={}
        for name,size in zip(('dev','calibration','holdout'),sizes):
            selected=_subset(available,size)
            if selected is None:
                break
            chosen=set(selected)
            trial[name]=sorted(w for i in selected for w in available[i])
            available=[group for i,group in enumerate(available) if i not in chosen]
        if len(trial)==3:
            partitions=trial
            break
    if partitions is None:
        raise ValueError('exact grouped partition cannot be constructed; revise design explicitly')
    distribution={}
    year_distribution={}
    region_distribution={}
    for partition,works in partitions.items():
        counts=defaultdict(int)
        years=defaultdict(int)
        regions=defaultdict(int)
        for work in works:
            counts[by_id[work].get('original_language') or 'unknown']+=1
            year=by_id[work].get('first_publication_year')
            years['unknown' if year is None else 'pre1900' if year<1900 else '1900-1949' if year<1950 else '1950-1999' if year<2000 else '2000+']+=1
            for region in by_id[work].get('region') or ['unknown']:
                regions[region]+=1
        distribution[partition]=dict(sorted(counts.items()))
        year_distribution[partition]=dict(sorted(years.items()))
        region_distribution[partition]=dict(sorted(regions.items()))
    manifest={'schema_version':'1','seed':seed,'sizes':list(sizes),'input_sha256':digest,'partitions':partitions,
              'group_ids':group_map,'language_distribution':distribution,'year_distribution':year_distribution,'region_distribution':region_distribution,
              'sampling_note':'Deterministic grouped convenience sample with language/year round-robin; not population-representative. Author names must be normalized upstream.','unselected_work_ids':sorted(set(ids)-{w for values in partitions.values() for w in values})}
    if identities is not None:
        manifest['schema_version']='2'
        manifest['group_identities']=identities
        manifest['group_identities_sha256']=hashlib.sha256(json.dumps(identities,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
        manifest['grouping_note']='Union of frozen DB author/series identity tokens and normalized display-name fallback; unknown authority links remain unverified.'
    if language_quotas is not None:
        from .sampling import distribution_report
        manifest['schema_version']='3'
        manifest['language_quotas']=language_quotas
        manifest['balancing']=balancing
        manifest['frame_comparison']=distribution_report(items,partitions)
        manifest['sampling_note']='Exact coarse-language quotas and intact author/series groups; descriptive metadata balance within the fixed convenience frame, not population representativeness.'
    encoded=json.dumps(manifest,ensure_ascii=False,indent=2)
    manifest['manifest_sha256']=hashlib.sha256(encoded.encode()).hexdigest()
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest
