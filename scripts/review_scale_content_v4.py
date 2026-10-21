"""Review at most five works per call without changing the prior review recipe."""
import hashlib
from pathlib import Path


V3_SHA = '3bff6f0df2e1496a26133d0f7e6a392808e8f633432b06b974e809a311095439'


def review_concurrency(args):
    slots = getattr(args, 'max_review_concurrency', None)
    if type(slots) is not int or slots not in (4, 8):
        raise ValueError('Explicit actual native concurrency of four or eight is required')
    return slots


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != V3_SHA:
        raise ValueError('Frozen review-v3 changed')
    source = raw.decode('utf-8').replace('\r\n', '\n')
    changes = (
        ('for offset in range(0,len(selected),50):add(selected[offset:offset+50])',
         'for offset in range(0,len(selected),5):add(selected[offset:offset+5])'),
        ('def execute(args):\n',
         'def execute(args):\n    review_slots = review_concurrency(args)\n'),
        ("'version':'scale-content-disjoint-review-plan-v1'",
         "'version':'scale-content-five-work-review-plan-v1'"),
        ("'max_group_works':50, 'max_concurrent_review_cli':2",
         "'max_group_works':5, 'max_concurrent_review_cli':review_slots"),
        ("'reason':'Observed CLI transport maximum1048576chars rejected original1181666char request before model turn'",
         "'reason':'V19 five-work cap; measured transport bound and complete disjoint coverage retained'"),
        ('with ThreadPoolExecutor(max_workers=2) as workers:',
         'with ThreadPoolExecutor(max_workers=review_slots) as workers:'),
        ('extra_source_paths=(Path(original.__file__),Path(semantics.__file__),Path(__file__),selection,plan,args.selection_path),',
         'extra_source_paths=(Path(original.__file__),Path(semantics.__file__),Path(__file__),'
         'Path(__file__).with_name("review_scale_content_v3.py"),selection,plan,args.selection_path),'),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError('Five-work review adapter anchor drift: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('review_scale_content_v3.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
