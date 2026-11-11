import pytest

from inventory.models import Product
from inventory.service import add_stock, apply_discount


def test_add_stock():
    p = add_stock(Product("A", 1), 2)
    assert p.quantity == 3


def test_add_stock_rejects_zero():
    with pytest.raises(ValueError):
        add_stock(Product("A"), 0)


def test_apply_discount():
    assert apply_discount(Product("A", 1, 10.0), 50) == 5.0
