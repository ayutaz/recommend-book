"""Recovery v3 parent preparer with the fixed-runtime chunk1 bootstrap."""
from __future__ import annotations

import sys

from recommend_book import scale20k_native_v3 as state

# The generated parent recipe imports this name.  The actual child subprocess
# intentionally imports the immutable chunk1 program from the fixed runtime.
sys.modules['recommend_book.scale20k_native_v1'] = state
recipe = state.load_recipe('preparer', __file__)
frozen_prepare = recipe.frozen_prepare
prepare = recipe.prepare
trial_control = recipe.trial_control

if __name__ == '__main__':
    recipe.main()
