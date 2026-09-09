from app.db.ids import new_id


def test_new_id_returns_unique_ulid_strings() -> None:
    first = new_id()
    second = new_id()

    assert len(first) == 26
    assert len(second) == 26
    assert first != second
    assert first.isalnum()
