"""Entry point of the inventory service."""
from inventory.api import app
from inventory.config import api_port


def main():
    app.run(port=api_port(), debug=True)


if __name__ == "__main__":
    main()
