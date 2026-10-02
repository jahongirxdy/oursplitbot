from oursplit.parsing import parse_amount, parse_inline_query


def test_parse_amount():
    assert parse_amount("100000") == 100_000
    assert parse_amount("100k") == 100_000
    assert parse_amount("1.5m") == 1_500_000


def test_inline_query():
    assert parse_inline_query("100k groceries") == (100_000, "groceries")
