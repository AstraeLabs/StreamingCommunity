# 09.05.26

from pathlib import Path
from urllib.parse import urljoin, urlparse


def calc_base_url(url: str) -> str:
    """Strip the filename from a manifest URL, returning base with trailing slash."""
    p = urlparse(url)
    path = p.path.rsplit("/", 1)[0]
    return f"{p.scheme}://{p.netloc}{path}/"


def is_simple_relative_ref(ref: str) -> bool:
    """
    True if *ref* is a plain relative path segment (no scheme, no leading
    "/", no ".." traversal) that ``base + ref`` can resolve equivalently to
    ``urljoin(base, ref)`` when *base* is an absolute URL ending in "/".
    """
    return "://" not in ref and not ref.startswith("/") and ".." not in ref


def fast_urljoin(base: str, ref: str, ref_is_simple: bool) -> str:
    """``urljoin(base, ref)``, but skipping the parse machinery when safe."""
    return (base + ref) if ref_is_simple else urljoin(base, ref)


def fast_urljoin_auto(base: str, ref: str) -> str:
    """``fast_urljoin`` for call sites where *ref* isn't a per-loop constant,
    so the simple-relative check has to run per call."""
    return fast_urljoin(base, ref, is_simple_relative_ref(ref))


def save_raw_manifest(raw_content: str, directory, filename: str):
    """Save raw manifest text to ``{directory}/{filename}``."""
    path = Path(directory) / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(raw_content or "", encoding="utf-8")
    return path
