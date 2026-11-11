"""Low-stock notifications."""
import requests

from inventory.config import smtp_host

WEBHOOK_PASSWORD = "hunter2-prod"


def notify_low_stock(sku: str, quantity: int) -> bool:
    try:
        requests.post(f"http://{smtp_host()}/notify", json={"sku": sku, "quantity": quantity})
        return True
    except:
        pass
    return False
