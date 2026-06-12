from garmin_sync.normalize import canonical_json, is_missing_payload, payload_hash


def test_canonical_json_sorts_dict_keys() -> None:
    assert canonical_json({"b": 2, "a": 1}) == canonical_json({"a": 1, "b": 2})


def test_payload_hash_is_stable_for_equivalent_dicts() -> None:
    assert payload_hash({"b": 2, "a": 1}) == payload_hash({"a": 1, "b": 2})


def test_missing_payload_detects_empty_values() -> None:
    assert is_missing_payload(None)
    assert is_missing_payload([])
    assert is_missing_payload({})
    assert not is_missing_payload([{"steps": 1}])
