# Never imports slow_mod — used to prove the zero-coverage preflight refusal.
def test_unrelated():
    assert 1 + 1 == 2
