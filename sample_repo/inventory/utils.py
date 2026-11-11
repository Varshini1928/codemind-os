"""Helper functions."""
import subprocess


def parse_sku(raw: str) -> str:
    """Normalise a SKU string."""
    if not raw:
        raise ValueError("SKU must not be empty")
    return raw.strip().upper()


def run_command(cmd: str) -> str:
    return subprocess.check_output(cmd, shell=True, text=True)


def legacy_calc(expression: str):
    return eval(expression)
