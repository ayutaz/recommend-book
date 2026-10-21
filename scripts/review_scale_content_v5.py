"""Five-work review with an output-schema work-id enum."""
import copy as copy_module
import hashlib
from pathlib import Path
from types import ModuleType


REVIEW_V4_SHA = '197191358eb71a2c4cf9e9d52145e59eccb5bcbf11a0040330cb082f1db43410'


def review_schema(selected_work_ids):
    """Return the unchanged review protocol with a bounded work-id enum."""
    import review_world_pilot_v1 as protocol

    selected = list(selected_work_ids)
    if not selected or len(selected) != len(set(selected)) or len(selected) > 5:
        raise ValueError('A distinct one-to-five-work review selection is required')
    schema = copy_module.deepcopy(protocol.review_schema())
    work_id = copy_module.deepcopy(schema['properties']['reviews']['items']['properties']['work_id'])
    work_id['enum'] = sorted(selected)
    schema['properties']['reviews']['items']['properties']['work_id'] = work_id
    return schema


def adapted_source(raw):
    if hashlib.sha256(raw).hexdigest() != REVIEW_V4_SHA:
        raise ValueError('Frozen review-v4 changed')
    recipe = ModuleType('frozen_review_v4_recipe')
    recipe.__file__ = str(Path(__file__).resolve())
    text = raw.decode('utf-8').replace('\r\n', '\n')
    boundary = "\nexec(compile(adapted_source("
    if text.count(boundary) != 1:
        raise ValueError('Review-v4 recipe boundary differs')
    exec(compile(text.split(boundary)[0], '<frozen-review-v4-recipe>', 'exec'), recipe.__dict__)
    source = recipe.adapted_source(Path(__file__).with_name('review_scale_content_v3.py').read_bytes())
    changes = (
        ("    source=path.read_text(encoding='utf-8').replace('\\r\\n','\\n')\n",
         "    source=path.read_text(encoding='utf-8').replace('\\r\\n','\\n')\n"
         "    source=source.replace(\"def review_schema():\\n\", \"def review_schema():\\n    return REVIEW_SCHEMA\\n\")\n"
         "    source=source.replace(\"    save(args.output / 'schema.json', schema)\\n\", \"    save(args.output / 'schema.json', schema)\\n    pin(args.output / 'schema.json')\\n\")\n"),
        ('    module.EXPECTED_PROMPT_CHARS=expected_chars\n',
         '    module.REVIEW_SCHEMA=review_schema(ids)\n'
         '    module.EXPECTED_PROMPT_CHARS=expected_chars\n'),
        ('Path(__file__).with_name("review_scale_content_v3.py"),selection,plan,args.selection_path),',
         'Path(__file__).with_name("review_scale_content_v3.py"),'
         'Path(__file__).with_name("review_scale_content_v4.py"),selection,plan,args.selection_path),'),
    )
    for before, after in changes:
        if source.count(before) != 1:
            raise ValueError('Review-v5 adapter anchor drift: ' + before)
        source = source.replace(before, after)
    return source


exec(compile(adapted_source(Path(__file__).with_name('review_scale_content_v4.py').read_bytes()),
             str(Path(__file__).resolve()), 'exec'), globals())
