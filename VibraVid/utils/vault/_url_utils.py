# 09.05.26

from urllib.parse import urlparse


def clean_license_url(license_url: str) -> str:
    """Strip query params / fragments from a license URL."""
    if not license_url:
        return ""

    parsed = urlparse(license_url)
    return f"{parsed.scheme}://{parsed.netloc}{parsed.path}".rstrip("/")
