"""Recovery-generation v2 entry over the fixed one-work admission recipe."""
from __future__ import annotations

import sys

from recommend_book import scale20k_native_v2 as state

sys.modules['recommend_book.scale20k_native_v1'] = state
if sys.modules.get(__name__) is not None:
    sys.modules['prepare_scale20k_content_chunk_v1'] = sys.modules[__name__]

recipe = state.load_recipe('preparer', __file__)
frozen_prepare = recipe.frozen_prepare
prepare = recipe.prepare
trial_control = recipe.trial_control


if __name__ == '__main__':
    recipe.main()
