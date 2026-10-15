"""Explicit versioned execution specs; historical artifacts remain immutable."""
from copy import deepcopy
from dataclasses import dataclass
import json
from pathlib import Path

from jsonschema import Draft202012Validator

from . import prompt as historical_prompt
from . import prompt_v3
from . import native_v3
from .validation import ROOT, validate_result as historical_validate

EXPERIMENT_CANDIDATES = ("definitions-v3", "context-v3")
NATIVE_SPECS = ("native-definitions-v3", "native-context-v3")
SPEC_NAMES = ("guarded-v3-baseline", *EXPERIMENT_CANDIDATES, *NATIVE_SPECS)
_HISTORICAL_SINGLE = historical_prompt.build_prompt
_HISTORICAL_BATCH = historical_prompt.build_batch_prompt
_V3_SINGLE = prompt_v3.build_prompt
_V3_BATCH = prompt_v3.build_batch_prompt
_NATIVE_SINGLE = native_v3.build_prompt
_NATIVE_BATCH = native_v3.build_batch_prompt


@dataclass(frozen=True)
class ExecutionSpec:
    name: str
    prompt_version: str
    prompt_path: Path
    schema_path: Path

    def expected_prompt(self, works, *, model, cli_version, taxonomy):
        """Independent fixed recipe used to bind the complete actual stdin."""
        kwargs = {"model":model,"cli_version":cli_version,"taxonomy":taxonomy}
        if self.name == "guarded-v3-baseline":
            return _HISTORICAL_BATCH(works, **kwargs) if len(works) > 1 else _HISTORICAL_SINGLE(works[0], **kwargs)
        kwargs["profile"] = self.name
        if self.name in NATIVE_SPECS:
            return _NATIVE_BATCH(works, **kwargs) if len(works) > 1 else _NATIVE_SINGLE(works[0], **kwargs)
        return _V3_BATCH(works, **kwargs) if len(works) > 1 else _V3_SINGLE(works[0], **kwargs)

    def build_prompt(self, work, **kwargs):
        if self.name == "guarded-v3-baseline":
            return historical_prompt.build_prompt(work, **kwargs)
        if self.name in NATIVE_SPECS:
            return native_v3.build_prompt(work,profile=self.name,**kwargs)
        return prompt_v3.build_prompt(work, profile=self.name, **kwargs)

    def build_batch_prompt(self, works, **kwargs):
        if self.name == "guarded-v3-baseline":
            return historical_prompt.build_batch_prompt(works, **kwargs)
        if self.name in NATIVE_SPECS:
            return native_v3.build_batch_prompt(works,profile=self.name,**kwargs)
        return prompt_v3.build_batch_prompt(works, profile=self.name, **kwargs)

    def validate_result(self, result, work, taxonomy):
        if self.name == "guarded-v3-baseline":
            return historical_validate(result, work, taxonomy)
        errors = list(Draft202012Validator(json.loads(self.schema_path.read_text(encoding="utf-8"))).iter_errors(result))
        if errors:
            raise ValueError("schema: " + "; ".join(e.message for e in errors[:5]))
        if result.get("prompt_version") != self.prompt_version:
            raise ValueError("Incorrect prompt_version for execution spec")
        if self.name in NATIVE_SPECS:
            return native_v3.validate_native(result,work)
        # The historical validator owns shared grounding semantics. Its identity
        # check is applied to a throwaway view only; the original result, raw
        # response, input hash and stored metadata are never rewritten.
        validation_view = deepcopy(result)
        validation_view["prompt_version"] = "1.0"
        historical_validate(validation_view, work, taxonomy)
        return result


def get_spec(name, *, root=None):
    root = Path(root) if root is not None else ROOT
    if name not in SPEC_NAMES:
        raise ValueError("Unknown classification execution spec: " + str(name))
    baseline = name == "guarded-v3-baseline"
    if name in NATIVE_SPECS:
        return ExecutionSpec(name,native_v3.VERSIONS[name],
            root / "src/recommend_book/classification/native_v3.py",root / "schemas/native-protagonist-v3.schema.json")
    return ExecutionSpec(name, "1.0" if baseline else prompt_v3.VERSIONS[name],
        root / "src/recommend_book/classification" / ("prompt.py" if baseline else "prompt_v3.py"),
        root / "schemas" / ("classification.schema.json" if baseline else "classification-v3.schema.json"))
