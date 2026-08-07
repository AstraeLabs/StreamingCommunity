# 07.08.26

import json
import logging
import os
import re
import shutil
import time
import zipfile
from urllib.parse import urlparse, urlunparse

from VibraVid.utils import config_manager
from VibraVid.utils.http_client import create_client, get_headers

logger = logging.getLogger(__name__)

_CACHE_ROOT = "imported_service"
_REFRESH_INTERVAL = 15 * 60
_SLUG_RE = re.compile(r"[^A-Za-z0-9._-]+")


def is_remote_source(source: str) -> bool:
    """True if `source` is an http(s) URL to a GitHub/Gitea repository rather than a local path."""
    try:
        return urlparse(source).scheme in ("http", "https")
    except Exception:
        return False


def redact(source: str) -> str:
    """Strip any embedded credentials from a source URL, for safe logging."""
    try:
        parsed = urlparse(source)
        netloc = parsed.hostname or parsed.netloc.rsplit("@", 1)[-1]
        if parsed.port:
            netloc = f"{netloc}:{parsed.port}"
        return urlunparse((parsed.scheme, netloc, parsed.path, "", "", ""))
    except Exception:
        return source


def _slug(text: str) -> str:
    return _SLUG_RE.sub("_", text).strip("_") or "x"


def _repo_from_path(path: str) -> tuple[str, str] | None:
    parts = [p for p in path.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1]
    repo = repo[:-4] if repo.endswith(".git") else repo
    return owner, repo


def _cache_dir(netloc: str, owner: str, repo: str, ref: str) -> str:
    """Human-readable cache location: .cache/imported_service/<host>__<owner>__<repo>__<ref>/"""
    name = "__".join(_slug(part) for part in (netloc, owner, repo, ref))
    return os.path.join(config_manager.base_path, ".cache", _CACHE_ROOT, name)


def _read_manifest(manifest_path: str) -> dict | None:
    try:
        with open(manifest_path, encoding="utf-8") as fh:
            data = json.load(fh)
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _write_manifest(manifest_path: str, data: dict) -> None:
    try:
        with open(manifest_path, "w", encoding="utf-8") as fh:
            json.dump(data, fh)
    except Exception as e:
        logger.warning(f"Could not persist imp_service manifest '{manifest_path}': {e}")


def _default_branch(client, api_base: str, owner: str, repo: str, is_github: bool, auth) -> str:
    url = f"https://api.github.com/repos/{owner}/{repo}" if is_github else f"{api_base}/api/v1/repos/{owner}/{repo}"
    try:
        response = client.get(url, auth=auth, timeout=20)
        if response.status_code == 200:
            return response.json().get("default_branch") or "main"
    except Exception as e:
        logger.debug(f"Could not resolve default branch for {owner}/{repo}: {e}")
    return "main"


def _remote_commit_sha(client, api_base: str, owner: str, repo: str, ref: str, is_github: bool, auth) -> str | None:
    """Cheap lookup of the latest commit sha for `ref`, used to skip re-downloading an unchanged repo."""
    try:
        if is_github:
            url = f"https://api.github.com/repos/{owner}/{repo}/commits/{ref}"
            response = client.get(url, auth=auth, timeout=20, headers={"Accept": "application/vnd.github+json"})
            if response.status_code == 200:
                return response.json().get("sha")
            return None

        response = client.get(f"{api_base}/api/v1/repos/{owner}/{repo}/branches/{ref}", auth=auth, timeout=20)
        if response.status_code == 200:
            return (response.json().get("commit") or {}).get("id")

        response = client.get(f"{api_base}/api/v1/repos/{owner}/{repo}/tags/{ref}", auth=auth, timeout=20)
        if response.status_code == 200:
            return (response.json().get("commit") or {}).get("sha")
    except Exception as e:
        logger.debug(f"Could not resolve latest commit for {owner}/{repo}#{ref}: {e}")

    return None


def _archive_url(scheme: str, netloc: str, owner: str, repo: str, ref: str, is_github: bool) -> str:
    if is_github:
        return f"https://codeload.github.com/{owner}/{repo}/zip/refs/heads/{ref}"
    return f"{scheme}://{netloc}/{owner}/{repo}/archive/{ref}.zip"


def _extract_single_root(zip_path: str, dest_dir: str) -> str | None:
    """Extract `zip_path` into `dest_dir`, unwrapping the single top-level folder GitHub/Gitea archives add."""
    if os.path.isdir(dest_dir):
        shutil.rmtree(dest_dir, ignore_errors=True)
    os.makedirs(dest_dir, exist_ok=True)

    with zipfile.ZipFile(zip_path) as zf:
        names = [n for n in zf.namelist() if n.strip()]
        if not names:
            return None
        top_levels = {name.split("/", 1)[0] for name in names}
        zf.extractall(dest_dir)

    if len(top_levels) == 1:
        wrapped = os.path.join(dest_dir, next(iter(top_levels)))
        if os.path.isdir(wrapped):
            return wrapped

    return dest_dir


def resolve_remote_source(source: str, force_refresh: bool = False) -> str | None:
    """
    Resolve a GitHub/Gitea repository URL (an `imp_service` entry) to a local directory of site
    modules, under `.cache/imported_service/<host>__<owner>__<repo>__<ref>/`
    """
    try:
        parsed = urlparse(source)
    except Exception as e:
        logger.error(f"Invalid imp_service git source: {e}")
        return None

    repo_info = _repo_from_path(parsed.path)
    if repo_info is None:
        logger.error(f"imp_service git source '{redact(source)}' must point to '<host>/<owner>/<repo>'")
        return None
    owner, repo = repo_info

    netloc = parsed.hostname or ""
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    clean_url = urlunparse((parsed.scheme, netloc, f"/{owner}/{repo}", "", "", ""))
    is_github = netloc.lower() in ("github.com", "www.github.com")
    api_base = f"{parsed.scheme}://{netloc}"

    username, password = parsed.username, parsed.password
    auth = (username, password or "") if username else None

    ref = parsed.fragment.strip() or None

    with create_client(headers=get_headers()) as client:
        if ref is None:
            ref = _default_branch(client, api_base, owner, repo, is_github, auth)

        cache_dir = _cache_dir(netloc, owner, repo, ref)
        manifest_path = cache_dir + ".json"
        manifest = _read_manifest(manifest_path)
        have_cache = bool(manifest) and os.path.isdir(cache_dir) and os.path.isdir(manifest.get("base_path", ""))

        if not force_refresh and have_cache and (time.time() - manifest.get("fetched_at", 0)) < _REFRESH_INTERVAL:
            return manifest["base_path"]

        remote_sha = _remote_commit_sha(client, api_base, owner, repo, ref, is_github, auth)

        if not force_refresh and have_cache and remote_sha and remote_sha == manifest.get("commit_sha"):
            logger.debug(f"imp_service source '{clean_url}#{ref}' unchanged upstream (commit {remote_sha[:7]}), reusing cache")
            manifest["fetched_at"] = time.time()
            _write_manifest(manifest_path, manifest)
            return manifest["base_path"]

        archive_url = _archive_url(parsed.scheme, netloc, owner, repo, ref, is_github)
        logger.info(f"Fetching imp_service source '{owner}/{repo}' ({ref}) from {netloc}")

        try:
            response = client.get(archive_url, auth=auth, timeout=60)
            response.raise_for_status()
        except Exception as e:
            logger.error(f"Failed to download imp_service source '{clean_url}' ({ref}): {e}")
            if have_cache:
                logger.warning(f"Falling back to previously cached copy of '{clean_url}'")
                return manifest["base_path"]
            return None

    tmp_zip = cache_dir + ".zip"
    try:
        os.makedirs(os.path.dirname(cache_dir), exist_ok=True)
        with open(tmp_zip, "wb") as fh:
            fh.write(response.content)

        extracted = _extract_single_root(tmp_zip, cache_dir)
        if extracted is None:
            logger.error(f"imp_service source '{clean_url}' archive was empty")
            return None

        _write_manifest(manifest_path, {"base_path": extracted, "ref": ref, "commit_sha": remote_sha, "fetched_at": time.time()})
        return extracted
    except Exception as e:
        logger.error(f"Failed to extract imp_service source '{clean_url}': {e}")
        return None
    finally:
        try:
            os.remove(tmp_zip)
        except OSError:
            pass
