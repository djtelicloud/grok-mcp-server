import ast

from src.swarm.transforms import deterministic_transforms


def _by_name(source):
    return dict(deterministic_transforms(source))


def test_append_loop_and_reverse_transform_are_parseable():
    loop = '''
def positives(values):
    result = []
    for value in values:
        if value > 0:
            result.append(value * 2)
    return result
'''.strip()
    compact = _by_name(loop)["append_loop_to_listcomp"]
    ast.parse(compact)
    assert "[value * 2 for value in values if value > 0]" in compact

    expanded = _by_name(compact)["listcomp_to_append_loop"]
    ast.parse(expanded)
    assert ".append(value * 2)" in expanded


def test_constant_branch_reduction_and_noop():
    source = "def f(x):\n    if True:\n        return x\n    return None\n"
    reduced = _by_name(source)["constant_branch_reduction"]
    assert "if True" not in reduced
    assert deterministic_transforms("def f(x):\n    return x\n") == []


def test_append_loop_rejects_self_referential_ordered_dedup():
    source = '''
def deduplicate(items):
    result = []
    for item in items:
        if item not in result:
            result.append(item)
    return result
'''.strip()
    assert "append_loop_to_listcomp" not in _by_name(source)


def test_method_indentation_is_preserved():
    source = "    def f(self, values):\n        out = [value for value in values]\n        return out"
    expanded = _by_name(source)["listcomp_to_append_loop"]
    assert expanded.startswith("    def f")
    ast.parse("class C:\n" + expanded + "\n")
