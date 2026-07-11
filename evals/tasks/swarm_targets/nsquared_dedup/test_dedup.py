from dedup import dedup


def test_removes_duplicates_preserving_order():
    assert dedup([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_empty():
    assert dedup([]) == []


def test_all_unique():
    assert dedup([1, 2, 3]) == [1, 2, 3]


def test_strings():
    assert dedup(["a", "b", "a", "c"]) == ["a", "b", "c"]
