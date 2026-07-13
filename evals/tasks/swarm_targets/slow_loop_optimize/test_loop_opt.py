from loop_opt import slow_accumulate


def test_empty_input():
    assert slow_accumulate([]) == ""


def test_single_element():
    assert slow_accumulate(["alpha"]) == "alpha\n"


def test_preserves_order():
    assert slow_accumulate(["third", "first", "second"]) == (
        "third\nfirst\nsecond\n"
    )


def test_unicode_is_unchanged():
    assert slow_accumulate(["café", "雪", "🚀"]) == "café\n雪\n🚀\n"
