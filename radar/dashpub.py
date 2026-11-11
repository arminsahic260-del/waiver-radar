"""Dashboard data from the PC: radar.json goes to the `podaci` branch as ONE orphan commit (force-updated), so the
repo doesn't grow with every pass and main stays clean. The PWA reads it from raw.githubusercontent.com.

Token: GITHUB_TOKEN from the environment / .env, else `gh auth token` (the GitHub CLI login on this PC). The token
is only sent to api.github.com and never logged."""
from __future__ import annotations

import base64
import logging
import os
import shutil
import subprocess
import time

import requests

log = logging.getLogger("radar.dashpub")
API = "https://api.github.com"
_token_cache: tuple[str | None, float] = (None, 0.0)


def token() -> str | None:
    global _token_cache
    t = os.environ.get("GITHUB_TOKEN")
    if t:
        return t
    tok, at = _token_cache
    if tok and time.time() - at < 3600:
        return tok
    gh = shutil.which("gh")
    if not gh:
        return None
    try:
        out = subprocess.run([gh, "auth", "token"], capture_output=True, text=True, timeout=20,
                             creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        tok = out.stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        tok = None
    _token_cache = (tok, time.time())
    return tok


def raw_url(repo: str, branch: str, path: str = "radar.json") -> str:
    return f"https://raw.githubusercontent.com/{repo}/{branch}/{path}"


def publish(repo: str, branch: str, files: dict[str, bytes], message: str = "podaci") -> bool:
    tok = token()
    if not tok:
        log.warning("dashboard: nema GitHub tokena (gh auth login ili GITHUB_TOKEN)")
        return False
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {tok}", "Accept": "application/vnd.github+json",
                      "X-GitHub-Api-Version": "2022-11-28", "User-Agent": "waiver-radar"})
    try:
        tree = []
        for path, content in files.items():
            r = s.post(f"{API}/repos/{repo}/git/blobs", timeout=30,
                       json={"content": base64.b64encode(content).decode(), "encoding": "base64"})
            r.raise_for_status()
            tree.append({"path": path, "mode": "100644", "type": "blob", "sha": r.json()["sha"]})
        r = s.post(f"{API}/repos/{repo}/git/trees", json={"tree": tree}, timeout=30)
        r.raise_for_status()
        r = s.post(f"{API}/repos/{repo}/git/commits", json={"message": message, "tree": r.json()["sha"],
                                                          "parents": []}, timeout=30)
        r.raise_for_status()
        sha = r.json()["sha"]
        r = s.patch(f"{API}/repos/{repo}/git/refs/heads/{branch}", json={"sha": sha, "force": True}, timeout=30)
        if r.status_code in (404, 422):
            r = s.post(f"{API}/repos/{repo}/git/refs", json={"ref": f"refs/heads/{branch}", "sha": sha}, timeout=30)
        r.raise_for_status()
        return True
    except requests.RequestException as e:
        code = getattr(getattr(e, "response", None), "status_code", None)
        log.warning("dashboard: GitHub %s", code or type(e).__name__)
        return False


def fetch_generated(repo: str, branch: str) -> str | None:
    """`generated` of the PC's last published radar.json (GitHub Actions uses it to skip its own pass)."""
    try:
        r = requests.get(raw_url(repo, branch), timeout=20, params={"t": int(time.time())})
        if r.status_code != 200:
            return None
        return r.json().get("generated")
    except (requests.RequestException, ValueError):
        return None
