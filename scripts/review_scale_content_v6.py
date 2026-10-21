"""Review-v6: restore the bounded review helper in the generated module globals."""
import hashlib
import importlib
import inspect
from pathlib import Path


REVIEW_V5_SHA = '2208ffe025536d2985d15833fd9fdc36dabe37086ec0595d16a278fdf16436f6'


def _review5():
    return importlib.import_module('review_scale_content_v5')


def review_concurrency(args):
    """Use the unchanged review-v4 concurrency guard in the v6 globals."""
    review4 = importlib.import_module('review_scale_content_v4')
    return review4.review_concurrency(args)


def review_schema(selected_work_ids):
    """Use the unchanged review-v5 selected-work enum schema."""
    return _review5().review_schema(selected_work_ids)


def adapted_source(raw):
    review5_path = Path(__file__).with_name('review_scale_content_v5.py').resolve(strict=True)
    if hashlib.sha256(review5_path.read_bytes()).hexdigest() != REVIEW_V5_SHA:
        raise ValueError('Frozen review-v5 changed')
    source = _review5().adapted_source(raw)
    before = ('Path(__file__).with_name("review_scale_content_v3.py"),'
              'Path(__file__).with_name("review_scale_content_v4.py"),selection,plan,args.selection_path),')
    after = ('Path(__file__).with_name("review_scale_content_v3.py"),'
             'Path(__file__).with_name("review_scale_content_v4.py"),'
             'Path(__file__).with_name("review_scale_content_v5.py"),'
             'selection,plan,args.selection_path),')
    if source.count(before) != 1:
        raise ValueError('Review-v6 source pin anchor drift')
    return source.replace(before, after)


_generated = adapted_source(Path(__file__).with_name('review_scale_content_v4.py').read_bytes())
exec(compile(_generated, str(Path(__file__).resolve()), 'exec'), globals())


if inspect.signature(execute).parameters.get('max_review_concurrency') is not None:
    raise ValueError('Review execute interface unexpectedly changed')
