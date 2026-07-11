# Deliberately slow suite — used to prove the preflight stage-budget refusal.
import time

from slow_mod import slow_sort


def test_sorts_slowly():
    time.sleep(2.0)
    assert slow_sort([2, 1]) == [1, 2]
