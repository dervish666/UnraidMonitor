"""Shared formatting utility functions."""


def format_bytes(bytes_val: int) -> str:
    """Format bytes as human-readable string.

    Args:
        bytes_val: Number of bytes.

    Returns:
        Human-readable string like "1.5GB" or "500MB".
    """
    gb = bytes_val / (1024**3)
    if gb >= 1.0:
        return f"{gb:.1f}GB"
    mb = bytes_val / (1024**2)
    return f"{mb:.0f}MB"
