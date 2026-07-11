# Fixture module for swarm sandbox/AST tests: a deliberately slow sort plus
# span-extraction edge cases (decorator, class method, nested def).
import functools


def slow_sort(items):
    """Bubble sort: the golden anti-pattern the swarm should improve."""
    data = list(items)
    n = len(data)
    for i in range(n):
        for j in range(0, n - i - 1):
            if data[j] > data[j + 1]:
                data[j], data[j + 1] = data[j + 1], data[j]
    return data


@functools.lru_cache(maxsize=None)
def decorated_helper(x):
    return x * 2


def outer():
    def inner():
        return 1

    return inner()


class Widget:
    def render(self):
        return "widget"

    def slow_sort(self, items):  # same name as the module-level function
        return slow_sort(items)
