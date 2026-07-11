"""Golden anti-pattern: O(N^2) order-preserving dedup via a list membership
scan. The swarm should discover the O(N) seen-set rewrite that keeps the same
observable behavior (first-occurrence order)."""


def dedup(items):
    result = []
    for item in items:
        if item not in result:  # O(N) scan per element -> O(N^2) overall
            result.append(item)
    return result
