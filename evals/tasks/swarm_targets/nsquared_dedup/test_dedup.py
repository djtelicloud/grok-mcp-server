from dedup import dedup


def test_removes_duplicates_preserving_order():
    assert dedup([3, 1, 3, 2, 1]) == [3, 1, 2]


def test_empty():
    assert dedup([]) == []


def test_all_unique():
    assert dedup([1, 2, 3]) == [1, 2, 3]


def test_strings():
    assert dedup(["a", "b", "a", "c"]) == ["a", "b", "c"]


def test_unhashable_items_preserve_first_occurrence():
    assert dedup([[1], [1], [2], [1]]) == [[1], [2]]


class _HashableValue:
    def __init__(self, value):
        self.value = value

    def __eq__(self, other):
        return self.value == getattr(other, "value", object())

    def __hash__(self):
        return hash(self.value)


class _UnhashableValue(_HashableValue):
    __hash__ = None


def test_equality_crosses_hashability_categories_in_either_order():
    hashable = _HashableValue("same")
    unhashable = _UnhashableValue("same")
    assert dedup([hashable, unhashable]) == [hashable]
    assert dedup([unhashable, hashable]) == [unhashable]
