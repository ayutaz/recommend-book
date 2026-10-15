import json

import pytest

from recommend_book.evaluation.splits import connected_groups, create_split_manifest


def test_authority_aliases_and_series_ids_stay_in_one_partition(tmp_path):
    inputs = [{"work_id": f"w{i}", "authors": [f"different name {i}"], "series": None}
              for i in range(12)]
    inputs[4]["authors"] = inputs[5]["authors"] = ["Name fallback"]
    identities = {"w0": ["author:db:authority-one"], "w1": ["author:db:authority-one"],
                  "w2": ["series:db:series-one"], "w3": ["series:db:series-one"]}
    groups = connected_groups(inputs, group_identities=identities)
    assert groups["w0"] == groups["w1"]
    assert groups["w2"] == groups["w3"]
    assert groups["w4"] == groups["w5"]
    assert len({groups["w0"], groups["w2"], groups["w4"]}) == 3
    manifest = create_split_manifest(inputs, tmp_path, sizes=(4, 4, 4), group_identities=identities)
    partitions = {work: partition for partition, works in manifest["partitions"].items() for work in works}
    for left, right in (("w0", "w1"), ("w2", "w3"), ("w4", "w5")):
        assert partitions[left] == partitions[right]
    reordered = {key: list(reversed(values)) * 2 for key, values in reversed(list(identities.items()))}
    assert create_split_manifest(inputs, tmp_path, sizes=(4, 4, 4), group_identities=reordered) == manifest
    unchanged = (tmp_path / "split_manifest.json").read_bytes()
    changed = {**identities, "w0": [*identities["w0"], "author:external:new-link"]}
    with pytest.raises(ValueError, match="immutable"):
        create_split_manifest(inputs, tmp_path, sizes=(4, 4, 4), group_identities=changed)
    assert (tmp_path / "split_manifest.json").read_bytes() == unchanged
    assert manifest["group_identities_sha256"]
    assert json.loads(unchanged)["group_identities"]["w0"] == identities["w0"]


def test_bad_identity_sidecar_is_rejected():
    inputs = [{"work_id": "a", "authors": []}]
    with pytest.raises(ValueError, match="unknown work"):
        connected_groups(inputs, group_identities={"missing": ["author:db:one"]})
    with pytest.raises(ValueError, match="identity tokens"):
        connected_groups(inputs, group_identities={"a": "author:db:one"})
