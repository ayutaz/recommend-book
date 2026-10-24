"""V20 entry over the fixed one-work/10M-token mandatory admission recipe."""
import importlib.util
from pathlib import Path


def load_state():
    path = Path(__file__).resolve().parents[1] / 'src/recommend_book/scale20k_native_v1.py'
    spec = importlib.util.spec_from_file_location('scale20k_preparation_state', path)
    state = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(state)
    return state


state = load_state()
recipe = state.load_recipe('preparer', __file__)
frozen_prepare = recipe.frozen_prepare
prepare = recipe.prepare
trial_control = recipe.trial_control


if __name__ == '__main__':
    recipe.main()
