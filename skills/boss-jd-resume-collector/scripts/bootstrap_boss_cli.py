#!/usr/bin/env python3
"""Bootstrap boss-cli for a fresh Codex desktop machine.

The script installs user-scoped runtime tools only. It does not require admin
rights and writes a manifest that collect_boss_resumes.py can auto-discover.
"""

from __future__ import annotations

import argparse
import json
import os
import platform
import shutil
import stat
import subprocess
import sys
import tarfile
import tempfile
import urllib.request
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_REPO_URL = "https://github.com/monster1389/boss-cli"
DEFAULT_REPO_REF = "codex-resume-sync"
DEFAULT_NODE_VERSION = "22.21.0"


def user_home() -> Path:
    return Path.home()


def default_root() -> Path:
    return user_home() / ".boss-cli"


def default_runtime_dir() -> Path:
    return default_root() / "runtime"


def default_repo_dir() -> Path:
    return default_root() / "src" / "boss-cli"


def default_bin_dir() -> Path:
    return default_root() / "bin"


def default_manifest_path() -> Path:
    return default_root() / "toolchain" / "boss-command.json"


def default_windows_npm_bin_dir() -> Path:
    appdata = os.environ.get("APPDATA")
    return (Path(appdata) if appdata else user_home() / "AppData" / "Roaming") / "npm"


def run(cmd: list[str], *, cwd: Path | None = None, timeout_seconds: int = 600) -> None:
    completed = subprocess.run(
        cmd,
        cwd=str(cwd) if cwd else None,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
        timeout=timeout_seconds,
    )
    if completed.returncode != 0:
        raise RuntimeError(
            "command failed\n"
            f"command: {json.dumps(cmd, ensure_ascii=False)}\n"
            f"cwd: {cwd or Path.cwd()}\n"
            f"exit_code: {completed.returncode}\n"
            f"stdout:\n{completed.stdout}\n"
            f"stderr:\n{completed.stderr}"
        )


def download(url: str, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    with urllib.request.urlopen(url, timeout=120) as response:
        with target.open("wb") as fh:
            shutil.copyfileobj(response, fh)


def platform_key() -> tuple[str, str]:
    system = platform.system().lower()
    machine = platform.machine().lower()
    if machine in {"amd64", "x86_64"}:
        arch = "x64"
    elif machine in {"arm64", "aarch64"}:
        arch = "arm64"
    else:
        raise RuntimeError(f"unsupported CPU architecture: {platform.machine()}")
    if system.startswith("win"):
        return "win", arch
    if system == "linux":
        return "linux", arch
    if system == "darwin":
        return "darwin", arch
    raise RuntimeError(f"unsupported OS: {platform.system()}")


def extract_archive(archive: Path, target_dir: Path) -> Path:
    if target_dir.exists():
        shutil.rmtree(target_dir)
    target_dir.parent.mkdir(parents=True, exist_ok=True)
    extract_root = target_dir.parent / f".extract-{target_dir.name}"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    extract_root.mkdir(parents=True)
    if archive.suffix == ".zip":
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(extract_root)
    else:
        with tarfile.open(archive) as tf:
            tf.extractall(extract_root)
    entries = list(extract_root.iterdir())
    if len(entries) != 1 or not entries[0].is_dir():
        raise RuntimeError(f"unexpected archive layout in {archive}")
    entries[0].rename(target_dir)
    shutil.rmtree(extract_root, ignore_errors=True)
    return target_dir


def ensure_managed_node(runtime_dir: Path, version: str) -> dict[str, str]:
    os_key, arch = platform_key()
    if os_key == "win":
        artifact = f"node-v{version}-win-{arch}.zip"
        node_rel = "node.exe"
        npm_rel = "npm.cmd"
    elif os_key == "linux":
        artifact = f"node-v{version}-linux-{arch}.tar.xz"
        node_rel = "bin/node"
        npm_rel = "bin/npm"
    else:
        artifact = f"node-v{version}-darwin-{arch}.tar.gz"
        node_rel = "bin/node"
        npm_rel = "bin/npm"

    install_dir = runtime_dir / f"node-v{version}-{os_key}-{arch}"
    node_path = install_dir / node_rel
    npm_path = install_dir / npm_rel
    marker = install_dir / ".boss-cli-bootstrap-complete"
    if not marker.exists():
        url = f"https://nodejs.org/dist/v{version}/{artifact}"
        with tempfile.TemporaryDirectory(prefix="boss-cli-node-") as tmp:
            archive = Path(tmp) / artifact
            download(url, archive)
            extract_archive(archive, install_dir)
        marker.write_text(datetime.now().isoformat(timespec="seconds") + "\n", encoding="utf-8")

    if not node_path.exists() or not npm_path.exists():
        raise RuntimeError(f"managed Node install is incomplete: {install_dir}")
    return {
        "node": str(node_path),
        "npm": str(npm_path),
        "install_dir": str(install_dir),
    }


def find_git(runtime_dir: Path) -> str:
    system_git = shutil.which("git")
    if system_git:
        return system_git
    if platform.system().lower().startswith("win"):
        return ensure_windows_mingit(runtime_dir)
    raise RuntimeError("git was not found on PATH. Install git, then rerun bootstrap_boss_cli.py.")


def latest_mingit_asset() -> tuple[str, str]:
    api_url = "https://api.github.com/repos/git-for-windows/git/releases/latest"
    request = urllib.request.Request(api_url, headers={"Accept": "application/vnd.github+json"})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = json.loads(response.read().decode("utf-8"))
    for asset in data.get("assets", []):
        name = str(asset.get("name") or "")
        if name.startswith("MinGit-") and name.endswith("-64-bit.zip"):
            return name, str(asset["browser_download_url"])
    raise RuntimeError("could not find a MinGit 64-bit zip asset in the latest git-for-windows release")


def ensure_windows_mingit(runtime_dir: Path) -> str:
    install_root = runtime_dir / "mingit"
    git_candidates = list(install_root.glob("**/cmd/git.exe"))
    if git_candidates:
        return str(git_candidates[0])
    name, url = latest_mingit_asset()
    with tempfile.TemporaryDirectory(prefix="boss-cli-mingit-") as tmp:
        archive = Path(tmp) / name
        download(url, archive)
        if install_root.exists():
            shutil.rmtree(install_root)
        install_root.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(archive) as zf:
            zf.extractall(install_root)
    git_candidates = list(install_root.glob("**/cmd/git.exe"))
    if not git_candidates:
        raise RuntimeError(f"MinGit install is incomplete: {install_root}")
    return str(git_candidates[0])


def ensure_repo(git_bin: str, repo_url: str, repo_ref: str, repo_dir: Path) -> None:
    if repo_dir.exists() and not (repo_dir / ".git").exists():
        raise RuntimeError(f"repo directory exists but is not a git repo: {repo_dir}")
    if not repo_dir.exists():
        repo_dir.parent.mkdir(parents=True, exist_ok=True)
        run([git_bin, "clone", "--branch", repo_ref, "--single-branch", repo_url, str(repo_dir)], timeout_seconds=900)
        return

    status = subprocess.run(
        [git_bin, "status", "--porcelain"],
        cwd=str(repo_dir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    if status.returncode != 0:
        raise RuntimeError(status.stderr or status.stdout or f"git status failed in {repo_dir}")
    if status.stdout.strip():
        raise RuntimeError(f"repo has local changes; refusing to update managed checkout: {repo_dir}")

    run([git_bin, "fetch", "origin", repo_ref], cwd=repo_dir, timeout_seconds=900)
    local_branches = subprocess.run(
        [git_bin, "branch", "--list", repo_ref],
        cwd=str(repo_dir),
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        encoding="utf-8",
        errors="replace",
    )
    if local_branches.returncode != 0:
        raise RuntimeError(local_branches.stderr or "git branch --list failed")
    if local_branches.stdout.strip():
        run([git_bin, "checkout", repo_ref], cwd=repo_dir)
    else:
        run([git_bin, "checkout", "-b", repo_ref, f"origin/{repo_ref}"], cwd=repo_dir)
    run([git_bin, "pull", "--ff-only", "origin", repo_ref], cwd=repo_dir, timeout_seconds=900)


def install_and_build(npm_bin: str, repo_dir: Path) -> None:
    run([npm_bin, "ci"], cwd=repo_dir, timeout_seconds=1200)
    run([npm_bin, "run", "build"], cwd=repo_dir, timeout_seconds=900)


def write_boss_wrapper(bin_dir: Path, node_bin: str, cli_js: Path) -> Path:
    bin_dir.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        wrapper = bin_dir / "boss.cmd"
        wrapper.write_text(
            f'@echo off\r\n"{node_bin}" "{cli_js}" %*\r\n',
            encoding="utf-8",
        )
    else:
        wrapper = bin_dir / "boss"
        wrapper.write_text(
            f'#!/usr/bin/env sh\nexec "{node_bin}" "{cli_js}" "$@"\n',
            encoding="utf-8",
        )
        wrapper.chmod(wrapper.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
    return wrapper


def link_boss_commands(node_bin: str, repo_dir: Path) -> Path:
    cli_js = repo_dir / "dist" / "cli" / "index.js"
    if not cli_js.exists():
        raise RuntimeError(f"boss-cli build output was not found: {cli_js}")
    managed_wrapper = write_boss_wrapper(default_bin_dir(), node_bin, cli_js)
    if os.name == "nt":
        write_boss_wrapper(default_windows_npm_bin_dir(), node_bin, cli_js)
    return managed_wrapper


def write_manifest(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-url", default=DEFAULT_REPO_URL)
    parser.add_argument("--repo-ref", default=DEFAULT_REPO_REF)
    parser.add_argument("--repo-dir", default=str(default_repo_dir()))
    parser.add_argument("--runtime-dir", default=str(default_runtime_dir()))
    parser.add_argument("--node-version", default=DEFAULT_NODE_VERSION)
    args = parser.parse_args()

    runtime_dir = Path(args.runtime_dir).expanduser()
    repo_dir = Path(args.repo_dir).expanduser()
    node = ensure_managed_node(runtime_dir, args.node_version)
    git_bin = find_git(runtime_dir)
    ensure_repo(git_bin, args.repo_url, args.repo_ref, repo_dir)
    install_and_build(node["npm"], repo_dir)
    boss_bin = link_boss_commands(node["node"], repo_dir)
    manifest = {
        "ok": True,
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "repo_url": args.repo_url,
        "repo_ref": args.repo_ref,
        "repo_dir": str(repo_dir),
        "runtime_dir": str(runtime_dir),
        "node": node["node"],
        "npm": node["npm"],
        "git": git_bin,
        "boss_bin": str(boss_bin),
    }
    write_manifest(default_manifest_path(), manifest)
    print(json.dumps(manifest, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
