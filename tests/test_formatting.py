"""Tests for the formatting utility functions."""


def test_format_bytes_gb():
    """Test format_bytes with gigabyte values."""
    from src.utils.formatting import format_bytes

    assert format_bytes(1_073_741_824) == "1.0GB"  # 1 GB exactly
    assert format_bytes(4_000_000_000) == "3.7GB"  # ~3.7 GB


def test_format_bytes_mb():
    """Test format_bytes with megabyte values."""
    from src.utils.formatting import format_bytes

    assert format_bytes(524_288_000) == "500MB"  # 500 MB
    assert format_bytes(1_000_000) == "1MB"  # ~1 MB


def test_format_bytes_small_gb():
    """Test format_bytes at the GB boundary."""
    from src.utils.formatting import format_bytes

    # Just under 1 GB should show MB
    assert format_bytes(1_073_741_823) == "1024MB"
    # Exactly 1 GB should show GB
    assert format_bytes(1_073_741_824) == "1.0GB"


def test_format_bytes_large_values():
    """Test format_bytes with larger GB values."""
    from src.utils.formatting import format_bytes

    assert format_bytes(8_589_934_592) == "8.0GB"  # 8 GB
    assert format_bytes(16_000_000_000) == "14.9GB"  # ~15 GB
