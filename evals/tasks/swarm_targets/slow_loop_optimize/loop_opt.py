"""Golden allocation anti-pattern for the swarm optimizer.

The implementation is intentionally quadratic: every iteration rebuilds the
entire accumulated string and allocates two short-lived lists. The observable
contract is deliberately small and deterministic so the oracle can distinguish
performance rewrites from semantic changes.
"""


def slow_accumulate(records):
    output = ""
    for record in records:
        line_parts = [record, "\n"]
        output = "".join([output, "".join(line_parts)])
    return output
