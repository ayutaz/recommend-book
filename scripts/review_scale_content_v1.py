"""Genuine review of exact selected completed works; never clear other labels."""
from __future__ import annotations

import json
from pathlib import Path
import sys
from types import ModuleType

ROOT = Path(__file__).resolve().parents[1]
PROTOCOL = 'complete-selected-input-native-output-internal-review-v1'
CONTENT_RULES = '''
This is a content-enriched route. The evidence may be an encyclopedia synopsis,
commentary or translation, not the original novel. Judge labels about the novel,
never transfer the evidence author's wording, fluency, darkness or article layout
to the novel's prose style, mood or narrative structure. A style label requires
supplied evidence explicitly describing the novel's own style, or an identified
original-language novel excerpt; a translated synopsis cannot establish it.
Themes need the novel's sustained treatment, structure needs its narrative form,
and protagonist attributes need a character established as centrally driving that
novel. A plot-summary section heading, encyclopedic ordering, listed character,
or event alone does not establish the corresponding taxonomy label. Keep unknown
and hold unsupported inference; no axis must be filled to meet a count target.
'''


def load_review(selected_work_ids):
    selected = list(selected_work_ids)
    if not selected or len(selected) != len(set(selected)):
        raise ValueError('Distinct nonempty genuine review selection required')
    path = ROOT / 'scripts/review_world_pilot_v1.py'
    source = path.read_text(encoding='utf-8').replace('\r\n', '\n')
    replacements = {
        "            if state['status'] != 'completed':\n":
            "            if state['status'] != 'completed' or state['work_id'] not in SELECTED_WORK_IDS:\n",
        '    if len(records) != len(works) or not records:\n':
            "    if {r['work'].work_id for r in records} != SELECTED_WORK_IDS or not records:\n",
        "    audit = {'version': review_version, 'protocol': protocol, 'run_id': manifest['run_id'],\n":
            "    audit = {'version': review_version, 'protocol': protocol, 'run_id': manifest['run_id'],\n"
            "        'selected_work_ids': sorted(SELECTED_WORK_IDS),\n",
    }
    for before, after in replacements.items():
        if source.count(before) != 1:
            raise ValueError('Immutable genuine review anchor drift')
        source = source.replace(before, after)
    result = ModuleType('content_selected_native_review')
    result.__file__ = str(path)
    result.SELECTED_WORK_IDS = frozenset(selected)
    exec(compile(source, str(path), 'exec'), result.__dict__)
    return result


def execute(args):
    if str(ROOT / 'scripts') not in sys.path:
        sys.path.insert(0, str(ROOT / 'scripts'))
    import review_world_pilot_v2 as v2
    selection = json.loads(args.selection_path.read_bytes())
    return load_review(selection['selected_work_ids']).execute(args,
        instructions=v2.INSTRUCTIONS + CONTENT_RULES,
        extra_source_paths=(Path(v2.__file__), Path(__file__), args.selection_path),
        protocol=PROTOCOL)
