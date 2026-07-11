from slow_mod import slow_sort


def test_sorts():
    assert slow_sort([3, 1, 2]) == [1, 2, 3]


def test_empty_and_duplicates():
    assert slow_sort([]) == []
    assert slow_sort([2, 2, 1]) == [1, 2, 2]


def test_does_not_mutate_input():
    original = [3, 1]
    slow_sort(original)
    assert original == [3, 1]
