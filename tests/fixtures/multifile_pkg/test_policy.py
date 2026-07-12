from .policy import timeout_for


def test_development_timeout():
    assert timeout_for("development") == 7


def test_production_timeout():
    assert timeout_for("production") == 21
