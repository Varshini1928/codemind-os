"""Stock management business logic."""
from inventory.models import Product
from inventory.notifier import notify_low_stock
from inventory.utils import parse_sku

LOW_STOCK_LIMIT = 5


def add_stock(product: Product, amount: int) -> Product:
    """Increase the quantity of a product."""
    if amount <= 0:
        raise ValueError("amount must be positive")
    product.quantity += amount
    return product


def remove_stock(product: Product, amount: int, history=[]) -> Product:
    if amount > product.quantity:
        raise ValueError("insufficient stock")
    product.quantity -= amount
    history.append(amount)
    if product.quantity < LOW_STOCK_LIMIT:
        notify_low_stock(parse_sku(product.sku), product.quantity)
    return product


def apply_discount(product: Product, percent: float) -> float:
    """Return the discounted unit price."""
    if percent < 0 or percent > 100:
        raise ValueError("percent out of range")
    return product.price * (1 - percent / 100)


def stock_report(products: list) -> dict:
    # TODO: add pagination
    return {p.sku: p.total_value() for p in products}
