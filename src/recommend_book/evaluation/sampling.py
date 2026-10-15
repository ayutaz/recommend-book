"""Grouped quota sampling and descriptive frame deviations, never gold metrics."""
from collections import Counter
import random
import re

from .metrics import records


def coarse_language(row):
    return row.get('original_language') if row.get('original_language') in ('ja','en') else 'other'


def _features(row):
    year=row.get('first_publication_year')
    band='unknown' if year is None else 'pre1900' if year<1900 else '1900-1949' if year<1950 else '1950-1999' if year<2000 else '2000+'
    evidence=[item for item in row.get('evidence',[]) if item.get('llm_input_allowed') is True]
    kinds={item['kind'] for item in evidence}
    genre=False
    for item in evidence:
        if item.get('kind')=='subject' and 'claims.P136' in (item.get('locator') or ''):
            match=re.search(r'^Genres:\s*(.*?)\s*; Main subjects',item.get('text',''))
            if match and match[1].strip():
                genre=True
    return {'original_language':[row.get('original_language') or 'unknown'],
            'coarse_language':[coarse_language(row)],'year':[band],
            'genre_known':['known' if genre else 'unknown'],
            'evidence_kind':sorted(kinds),'evidence_depth':[next((kind for kind in ('excerpt','synopsis','note') if kind in kinds),'facts')]}


def _summary(features):
    out={metric:Counter() for metric in ('original_language','coarse_language','year','genre_known','evidence_kind','evidence_depth')}
    for row in features:
        for metric,values in row.items():
            out[metric].update(values)
    return out


def distribution_report(inputs, partitions):
    rows=records(inputs)
    features={row['work_id']:_features(row) for row in rows}
    frame=_summary(features.values())
    def section(ids):
        counts=_summary(features[work] for work in ids)
        n=len(ids)
        result={}
        for metric,values in counts.items():
            keys=sorted(set(frame[metric])|set(values))
            delta={key:100*(values[key]/n-frame[metric][key]/len(rows)) if n and rows else None for key in keys}
            result[metric]={'counts':dict(sorted(values.items())),'denominator':n,
                'rates':{key:values[key]/n if n else None for key in keys},
                'deviation_percentage_points':delta,
                'max_absolute_deviation_percentage_points':max((abs(v) for v in delta.values() if v is not None),default=None)}
        return result
    return {'frame':section(list(features)),
        'partitions':{name:section(works) for name,works in partitions.items()},
        'definitions':{'genre_known':'Nonempty explicit Genres field in allowed frozen Wikidata P136 evidence, not themes, inferred genre or a human/model correctness judgment.',
            'evidence_kind':'Allowed frozen evidence kinds per work; memberships overlap.',
            'comparison':'Percentage-point deviations from this fixed convenience frame. Quotas and small samples cannot establish population representativeness or accuracy.'}}


def balanced_partitions(items, groups, quotas, seed, subset, attempts=64):
    """Try whole mixed groups, then exact 1D subsets of pure language groups.

    Bounded seeded candidates choose the lowest descriptive frame deviation.
    A pure-only fallback is explicit and reports excluded mixed groups. Failure
    is never repaired by cutting groups, relaxing quotas, or shrinking samples.
    """
    by_id={row['work_id']:row for row in items}
    features={work:_features(row) for work,row in by_id.items()}
    reference=_summary(features.values())
    pure={language:[] for language in ('ja','en','other')}
    mixed=[]
    for group in groups:
        counts=Counter(coarse_language(by_id[work]) for work in group)
        if len(counts)==1:
            pure[next(iter(counts))].append(group)
        else:
            mixed.append((group,counts))
    best=None
    successful=0
    for attempt in range(attempts+1):
        rng=random.Random(seed+attempt)
        remaining={name:dict(values) for name,values in quotas.items()}
        selected={name:[] for name in quotas}
        excluded=[]
        ordered=list(mixed);rng.shuffle(ordered)
        for group,counts in ordered:
            available=[name for name in quotas if all(remaining[name][language]>=n for language,n in counts.items())]
            if not available or attempt==attempts:
                excluded.append(group)
                continue
            name=rng.choice(available)
            selected[name].extend(group)
            for language,n in counts.items():
                remaining[name][language]-=n
        feasible=True
        for language in pure:
            available=list(pure[language]);rng.shuffle(available)
            for name in quotas:
                chosen=subset(available,remaining[name][language])
                if chosen is None:
                    feasible=False
                    break
                indexes=set(chosen)
                selected[name].extend(work for index in chosen for work in available[index])
                available=[group for index,group in enumerate(available) if index not in indexes]
            if not feasible:
                break
        if not feasible:
            continue
        successful+=1
        score=0
        for works in selected.values():
            counts=_summary(features[work] for work in works)
            for metric in ('original_language','year','genre_known','evidence_kind','evidence_depth'):
                # Mean absolute deviation gives each metadata dimension equal
                # weight; evidence memberships are treated as independent rates.
                keys=sorted(set(reference[metric])|set(counts[metric]))
                score+=sum(abs(counts[metric][key]/len(works)-reference[metric][key]/len(items)) for key in keys)/max(1,len(keys))
        rank=(sum(map(len,excluded)),score,attempt)
        if best is None or rank<best[0]:
            best=(rank,{name:sorted(works) for name,works in selected.items()},excluded)
    if best is None:
        raise ValueError('exact grouped language quotas cannot be constructed; revise design explicitly')
    rank,selected,excluded=best
    return selected,{'method':'Whole mixed-language groups followed by exact pure-language group subset sums; bounded seeded candidates minimize frame metadata deviation.',
        'candidate_attempts':attempts+1,'successful_candidates':successful,'selected_attempt':rank[2],
        'metadata_deviation_score':rank[1],'mixed_groups_total':len(mixed),'mixed_works_total':sum(len(group) for group,_ in mixed),
        'mixed_groups_selected':len(mixed)-len(excluded),'mixed_works_selected':sum(len(group) for group,_ in mixed)-sum(map(len,excluded)),
        'mixed_groups_excluded':len(excluded),'mixed_works_excluded':sum(map(len,excluded)),
        'excluded_mixed_work_ids':sorted(work for group in excluded for work in group),
        'excluded_mixed_language_counts':dict(Counter(coarse_language(by_id[work]) for group in excluded for work in group)),
        'limitation':'Finite candidate search does not prove globally optimal balance or infeasibility of other assignments. Any exclusion changes the evaluable frame and is explicit; rare languages and label supports are not guaranteed.'}
