"""HTTP routes."""
from inventory.models import Product
from inventory.service import add_stock, apply_discount, stock_report

app = None  # framework application object is created elsewhere
PRODUCTS = [Product("A-1", 10, 2.5)]


@app.get("/stock")
def get_stock():
    return stock_report(PRODUCTS)


@app.post("/stock/{sku}/add")
def post_add(sku: str, amount: int):
    return add_stock(PRODUCTS[0], amount).quantity


@app.get("/price/{sku}")
def get_price(sku: str, discount: float = 0):
    return apply_discount(PRODUCTS[0], discount)
