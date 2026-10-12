"""Frozen author/series connected-group partition manifests."""
from __future__ import annotations
from collections import defaultdict
import hashlib
import json
from pathlib import Path
import random
from .metrics import records


def connected_groups(inputs):
    items=records(inputs)
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
        for key in keys:
            if key in owners:
                union(item['work_id'],owners[key])
            else:
                owners[key]=item['work_id']
    return {key:find(key) for key in parent}


def _subset(groups,target):
    reachable={0:[]}
    for index,group in enumerate(groups):
        for total,chosen in list(reachable.items())[::-1]:
            new=total+len(group)
            if new<=target and new not in reachable:
                reachable[new]=chosen+[index]
        if target in reachable:
            return reachable[target]
    return None


def create_split_manifest(inputs, output_dir, *, sizes=(150,100,150), seed=1729):
    """Select exact sample sizes without dividing connected author/series groups.

    Does not silently shrink an impossible design. Existing manifest and full
    input snapshot digest must agree; changes require a new output directory.
    """
    items=sorted(records(inputs),key=lambda x:x['work_id'])
    ids=[r['work_id'] for r in items]
    if len(ids)!=len(set(ids)):
        raise ValueError('duplicate work id in split input')
    if len(sizes)!=3 or any(not isinstance(size,int) or size<=0 for size in sizes):
        raise ValueError('three positive partition sizes required')
    digest=hashlib.sha256(json.dumps(items,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    path=Path(output_dir)/'split_manifest.json'
    if path.exists():
        saved=json.loads(path.read_text('utf-8'))
        hashable={key:value for key,value in saved.items() if key!='manifest_sha256'}
        actual=hashlib.sha256(json.dumps(hashable,ensure_ascii=False,indent=2).encode()).hexdigest()
        if saved.get('manifest_sha256')!=actual:
            raise ValueError('immutable split manifest checksum invalid')
        if saved['input_sha256']!=digest or saved['seed']!=seed or saved['sizes']!=list(sizes):
            raise ValueError('immutable split manifest differs; use a new evaluation version/directory')
        return saved
    if len(items)<sum(sizes):
        raise ValueError(f'requires {sum(sizes)} works; only {len(items)} available')
    group_map=connected_groups(items)
    grouped=defaultdict(list)
    for work in ids:
        grouped[group_map[work]].append(work)
    groups=list(grouped.values())
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
    partitions=None
    for attempt in range(min(100,max(1,len(ordered)))):
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
    encoded=json.dumps(manifest,ensure_ascii=False,indent=2)
    manifest['manifest_sha256']=hashlib.sha256(encoded.encode()).hexdigest()
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf-8')
    return manifest
