from inventory.utils import parse_sku


def test_parse_sku():
    assert parse_sku(" ab ") == "AB"
