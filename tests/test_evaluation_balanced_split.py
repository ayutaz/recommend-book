from collections import Counter
import json
import os
import subprocess
import sys

import pytest

from recommend_book.evaluation.splits import create_split_manifest


def test_balanced_quota_preserves_mixed_groups_and_manifest(tmp_path):
    rows=[{'work_id':f'{language}-{index}','original_language':language,'authors':[f'{language}-{index}'],
           'first_publication_year':1900+index*5,'evidence':[]} for language in ('ja','en','fr') for index in range(20)]
    identities={'ja-0':['author:mixed-a'],'en-0':['author:mixed-a'],
                'ja-1':['series:mixed-b'],'fr-0':['series:mixed-b']}
    quotas={name:{'ja':2,'en':2,'other':2} for name in ('dev','calibration','holdout')}
    manifest=create_split_manifest(rows,tmp_path,sizes=(6,6,6),group_identities=identities,language_quotas=quotas)
    lookup={row['work_id']:row for row in rows}
    partition_by_work={work:name for name,works in manifest['partitions'].items() for work in works}
    for name,works in manifest['partitions'].items():
        counts=Counter(lookup[work]['original_language'] if lookup[work]['original_language'] in ('ja','en') else 'other' for work in works)
        assert dict(counts)==quotas[name]
    assert partition_by_work['ja-0']==partition_by_work['en-0']
    assert partition_by_work['ja-1']==partition_by_work['fr-0']
    assert manifest['balancing']['mixed_groups_selected']==2
    assert manifest['balancing']['mixed_groups_excluded']==0
    assert create_split_manifest(list(reversed(rows)),tmp_path,sizes=(6,6,6),group_identities=identities,language_quotas=quotas)==manifest
    changed={**quotas,'dev':{'ja':3,'en':1,'other':2}}
    with pytest.raises(ValueError,match='immutable'):
        create_split_manifest(rows,tmp_path,sizes=(6,6,6),group_identities=identities,language_quotas=changed)


def test_quota_impossible_without_splitting_author_is_rejected(tmp_path):
    rows=[{'work_id':f'{language}-{index}','original_language':language,'authors':[language]}
          for language in ('ja','en') for index in range(4)]
    quotas={name:{'ja':1,'en':1,'other':0} for name in ('dev','calibration','holdout')}
    with pytest.raises(ValueError,match='grouped language quotas'):
        create_split_manifest(rows,tmp_path,sizes=(2,2,2),language_quotas=quotas)
    assert not (tmp_path/'split_manifest.json').exists()


def test_distribution_report_uses_documented_genre_not_theme_or_model():
    from recommend_book.evaluation.sampling import distribution_report
    rows=[{'work_id':'genre','original_language':'ja','first_publication_year':1900,
           'evidence':[{'kind':'subject','locator':'claims.P136;claims.P921','text':'Genres: mystery; Main subjects (not automatically themes): family','llm_input_allowed':True}]},
          {'work_id':'theme','original_language':'en','first_publication_year':2000,
           'evidence':[{'kind':'subject','locator':'claims.P136;claims.P921','text':'Genres: ; Main subjects (not automatically themes): mystery','llm_input_allowed':True}]}]
    report=distribution_report(rows,{'holdout':['theme']})
    assert report['frame']['genre_known']['counts']=={'known':1,'unknown':1}
    assert report['partitions']['holdout']['genre_known']['deviation_percentage_points']['known']==-50
    assert report['partitions']['holdout']['year']['counts']=={'2000+':1}


def test_zero_other_quota_needs_no_group_and_oversized_mixed_exclusion_is_explicit(tmp_path):
    rows=[{'work_id':f'{language}-{index}','original_language':language,'authors':[f'{language}-{index}']}
          for language in ('ja','en') for index in range(20)]
    for row in rows:
        if int(row['work_id'].split('-')[1])<5:
            row['authors']=['Oversized mixed group']
    quotas={name:{'ja':1,'en':1,'other':0} for name in ('dev','calibration','holdout')}
    manifest=create_split_manifest(rows,tmp_path,sizes=(2,2,2),language_quotas=quotas)
    assert all(len(values)==2 for values in manifest['partitions'].values())
    assert manifest['balancing']['mixed_groups_excluded']==1
    assert manifest['balancing']['mixed_works_excluded']==10
    assert manifest['balancing']['excluded_mixed_language_counts']=={'ja':5,'en':5}


def test_balance_manifest_is_reproducible_across_python_hash_seeds(tmp_path):
    script="""
import json,sys
from recommend_book.evaluation.splits import create_split_manifest
rows=[{'work_id':str(i),'original_language':['ja','en','fr','de','es'][i%5],
       'first_publication_year':None if i%7==0 else 1850+i*3,'authors':[str(i)],
       'evidence':[{'kind':'note' if i%3==0 else 'description','llm_input_allowed':True}]} for i in range(100)]
q={name:{'ja':2,'en':2,'other':3} for name in ('dev','calibration','holdout')}
print(json.dumps(create_split_manifest(rows,sys.argv[1],sizes=(7,7,7),language_quotas=q),sort_keys=True))
"""
    outputs=[subprocess.run([sys.executable,'-c',script,str(tmp_path/seed)],
        env={**os.environ,'PYTHONHASHSEED':seed},capture_output=True,text=True,check=True).stdout for seed in ('1','42')]
    assert json.loads(outputs[0])==json.loads(outputs[1])
