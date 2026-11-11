"""Data models."""


class Base:
    """Minimal declarative base."""


class Product(Base):
    """A stocked product."""

    def __init__(self, sku: str, quantity: int = 0, price: float = 0.0):
        self.sku = sku
        self.quantity = quantity
        self.price = price

    def total_value(self) -> float:
        return self.quantity * self.price
