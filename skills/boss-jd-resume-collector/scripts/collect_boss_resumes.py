#!/usr/bin/env python3
"""根据一份 JD 从 BOSS chat/recommend/search 来源各采集 3 份可用简历。"""

from __future__ import annotations

import argparse
import json
import os
import re
import shlex
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any


DEFAULT_SOURCES = ("chat", "recommend", "search")
USABLE_STATUSES = {"downloaded", "skipped_existing"}
SOURCE_RETRY_DELAYS = {
    "chat": [],
    "recommend": [5, 15],
    "search": [5, 15],
}
FAILURE_KINDS = {
    "boss_not_found",
    "login_or_session",
    "permission_or_page_unavailable",
    "json_parse_failed",
    "insufficient_data",
    "candidate_download_failed",
    "unknown",
}


def user_home() -> Path:
    return Path.home()


def default_resume_root() -> Path:
    return user_home() / ".boss-cli" / "resumes"


def default_runs_root() -> Path:
    return user_home() / ".boss-cli" / "runs"


def default_windows_boss_cmd() -> Path:
    return user_home() / "AppData" / "Roaming" / "npm" / "boss.cmd"


def default_skill_toolchain_manifest() -> Path:
    return user_home() / ".boss-cli" / "toolchain" / "boss-command.json"


def default_managed_boss_cmd() -> Path:
    suffix = ".cmd" if os.name == "nt" else ""
    return user_home() / ".boss-cli" / "bin" / f"boss{suffix}"


def safe_path_exists(path: Path) -> tuple[bool, str | None]:
    try:
        return path.exists(), None
    except OSError as exc:
        return False, str(exc)


def safe_segment(value: str, fallback: str = "jd") -> str:
    cleaned = re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", value).strip()
    cleaned = re.sub(r"\s+", "_", cleaned)
    return cleaned[:80] or fallback


def read_jd(args: argparse.Namespace) -> tuple[str, str]:
    if args.jd_file:
        path = Path(args.jd_file).expanduser()
        text = path.read_text(encoding="utf-8")
        return text, str(path)
    if args.jd_text:
        return args.jd_text, "inline"
    if not sys.stdin.isatty():
        text = sys.stdin.read()
        if text.strip():
            return text, "stdin"
    raise SystemExit("缺少 JD。请传入 --jd-file、--jd-text，或通过 stdin 管道输入 JD 文本。")


def infer_job_keyword(jd_text: str) -> str | None:
    lines = [line.strip() for line in jd_text.splitlines() if line.strip()]
    head = " ".join(lines[:6])
    patterns = [
        r"(?:职位|岗位|招聘岗位|岗位名称)[:：\s]*([^\n，,；;｜|]+)",
        r"(Java|Python|Golang|Go|前端|后端|全栈|算法|测试|运维|数据|产品|运营|UI|Android|iOS)[^\n，,；;]{0,24}(?:工程师|开发|实习生|岗位)?",
    ]
    for pattern in patterns:
        match = re.search(pattern, head, flags=re.IGNORECASE)
        if match:
            raw = match.group(1) if match.lastindex else match.group(0)
            keyword = re.sub(r"\s+", " ", raw).strip(" ：:-")
            if keyword:
                return keyword[:40]
    return None


def split_command(command: str) -> list[str]:
    return shlex.split(command, posix=(os.name != "nt"))


def run_command(cmd: list[str], timeout_seconds: int = 180) -> dict[str, Any]:
    try:
        completed = subprocess.run(
            cmd,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_seconds,
        )
    except FileNotFoundError as exc:
        return {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": "",
            "stderr": str(exc),
            "failure_kind": "boss_not_found",
        }
    except subprocess.TimeoutExpired as exc:
        return {
            "ok": False,
            "command": cmd,
            "exit_code": None,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or f"command timed out after {timeout_seconds}s",
            "failure_kind": "unknown",
        }
    return {
        "ok": completed.returncode == 0,
        "command": cmd,
        "exit_code": completed.returncode,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
    }


def boss_cmd(boss_bin: str, *args: str) -> list[str]:
    return [*split_command(boss_bin), *args]


def probe_boss_candidate(candidate: str) -> tuple[bool, str]:
    result = run_command(boss_cmd(candidate, "help"), timeout_seconds=30)
    combined = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
    if not result["ok"]:
        return False, combined.strip() or f"boss help exited with {result.get('exit_code')}"
    if "resumes" not in combined:
        return False, "boss help succeeded, but this executable does not expose the required resumes command"
    return True, ""


def resolve_boss_bin(explicit_boss_bin: str | None) -> tuple[str | None, dict[str, Any]]:
    attempts: list[dict[str, Any]] = []
    candidates: list[tuple[str, str]] = []
    if explicit_boss_bin:
        candidates.append(("argument", explicit_boss_bin))
    env_boss_bin = os.environ.get("BOSS_BIN", "").strip()
    if env_boss_bin and env_boss_bin != explicit_boss_bin:
        candidates.append(("environment", env_boss_bin))
    manifest_path = default_skill_toolchain_manifest()
    manifest_exists, manifest_error = safe_path_exists(manifest_path)
    if manifest_exists:
        try:
            manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
            manifest_boss_bin = str(manifest.get("boss_bin") or "").strip()
            if manifest_boss_bin:
                candidates.append(("skill_toolchain_manifest", manifest_boss_bin))
        except (OSError, json.JSONDecodeError) as exc:
            attempts.append({
                "source": "skill_toolchain_manifest",
                "candidate": str(manifest_path),
                "ok": False,
                "error": str(exc),
            })
    elif manifest_error:
        attempts.append({
            "source": "skill_toolchain_manifest",
            "candidate": str(manifest_path),
            "ok": False,
            "error": manifest_error,
        })

    managed_boss = default_managed_boss_cmd()
    managed_exists, managed_error = safe_path_exists(managed_boss)
    if managed_exists:
        candidates.append(("skill_managed_bin", str(managed_boss)))
    elif managed_error:
        attempts.append({
            "source": "skill_managed_bin",
            "candidate": str(managed_boss),
            "ok": False,
            "error": managed_error,
        })

    if os.name == "nt":
        windows_cmd = default_windows_boss_cmd()
        windows_exists, windows_error = safe_path_exists(windows_cmd)
        if windows_exists:
            candidates.append(("windows_npm_global", str(windows_cmd)))
        elif windows_error:
            attempts.append({
                "source": "windows_npm_global",
                "candidate": str(windows_cmd),
                "ok": False,
                "error": windows_error,
            })

    path_boss_cmd = shutil.which("boss.cmd")
    if path_boss_cmd:
        candidates.append(("path_cmd", path_boss_cmd))
    path_boss = shutil.which("boss")
    if path_boss:
        candidates.append(("path", path_boss))

    seen: set[str] = set()
    for source, candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        try:
            cmd = split_command(candidate)
        except ValueError as exc:
            attempts.append({"source": source, "candidate": candidate, "ok": False, "error": str(exc)})
            continue
        if not cmd:
            attempts.append({"source": source, "candidate": candidate, "ok": False, "error": "empty command"})
            continue
        executable = cmd[0]
        if any(sep in executable for sep in ("/", "\\")):
            exists, exists_error = safe_path_exists(Path(executable))
        else:
            exists = shutil.which(executable) is not None
            exists_error = None
        attempt: dict[str, Any] = {"source": source, "candidate": candidate, "ok": False, "exists": exists}
        if exists_error:
            attempt["error"] = exists_error
        if not exists:
            attempt.setdefault("error", "candidate does not exist or is not on PATH")
            attempts.append(attempt)
            continue
        probe_ok, probe_error = probe_boss_candidate(candidate)
        attempt["ok"] = probe_ok
        if probe_error:
            attempt["error"] = probe_error
        attempts.append(attempt)
        if probe_ok:
            return candidate, {"ok": True, "selected": candidate, "selected_source": source, "attempts": attempts}

    return None, {
        "ok": False,
        "selected": None,
        "failure_kind": "boss_not_found",
        "message": "未找到支持 resumes 命令的 boss 可执行命令。请设置 --boss-bin、BOSS_BIN，或重新运行 bootstrap_boss_cli。",
        "attempts": attempts,
    }


def classify_failure(text: str, *, json_error: bool = False, usable_count: int | None = None) -> str | None:
    lowered = text.lower()
    if json_error:
        return "json_parse_failed"
    if "not recognized" in lowered or "no such file" in lowered or "找不到" in text or "无法将" in text:
        return "boss_not_found"
    if any(token in lowered for token in ("login", "captcha", "forbidden", "risk", "session")) or any(
        token in text for token in ("登录", "扫码", "验证码", "风控", "账号")
    ):
        return "login_or_session"
    if any(token in text for token in ("权限", "付费", "深度搜索", "页面", "无法访问", "未找到")):
        return "permission_or_page_unavailable"
    if usable_count is not None and usable_count != 3:
        return "insufficient_data"
    if any(token in lowered for token in ("download_failed", "missing_identifiers")):
        return "candidate_download_failed"
    return None


def build_boss_resumes_command(
    boss_bin: str,
    source: str,
    root: Path,
    limit: int,
    job_keyword: str | None,
    search_keyword: str | None = None,
    search_job: str | None = None,
    search_city: str | None = None,
) -> list[str]:
    cmd = boss_cmd(
        boss_bin,
        "resumes",
        "--from",
        source,
        "--limit",
        str(limit),
        "--root",
        str(root),
        "--json",
    )
    if source == "recommend" and job_keyword:
        cmd.extend(["--job", job_keyword])
    if source == "search":
        if search_keyword:
            cmd.extend(["--keyword", search_keyword])
        if search_job:
            cmd.extend(["--job", search_job])
        if search_city:
            cmd.extend(["--city", search_city])
    return cmd


def count_usable_results(source_result: dict[str, Any]) -> int:
    usable_count = 0
    for item in source_result.get("results", []):
        status = item.get("status")
        artifacts = item.get("artifacts") or {}
        resume_md = artifacts.get("resumeMarkdownPath")
        resume_json = artifacts.get("resumeJsonPath")
        if status in USABLE_STATUSES and path_exists(resume_md) and path_exists(resume_json):
            usable_count += 1
    return usable_count


def source_retry_delays(source: str) -> list[int]:
    return SOURCE_RETRY_DELAYS.get(source, [])


def source_attempts(source: str) -> int:
    return len(source_retry_delays(source)) + 1


def compact_attempt_result(result: dict[str, Any], attempt: int, max_attempts: int) -> dict[str, Any]:
    copied = dict(result)
    copied.pop("attempts", None)
    copied["attempt"] = attempt
    copied["max_attempts"] = max_attempts
    copied["usable_count"] = count_usable_results(copied)
    return copied


def run_with_retries(
    *,
    source: str,
    operation: str,
    runner: Any,
    is_success: Any,
    retry_delays: list[int] | None = None,
) -> dict[str, Any]:
    delays = retry_delays if retry_delays is not None else source_retry_delays(source)
    max_attempts = len(delays) + 1
    attempts: list[dict[str, Any]] = []
    last_result: dict[str, Any] | None = None
    for index in range(max_attempts):
        attempt = index + 1
        result = runner()
        result["operation"] = operation
        result["attempt"] = attempt
        result["max_attempts"] = max_attempts
        result["usable_count"] = count_usable_results(result)
        attempts.append(compact_attempt_result(result, attempt, max_attempts))
        last_result = result
        if is_success(result):
            break
        if index < len(delays):
            time.sleep(delays[index])
    if last_result is None:
        raise RuntimeError(f"{operation} did not run")
    final = dict(last_result)
    final["attempts"] = attempts
    final["attempt_count"] = len(attempts)
    final["max_attempts"] = max_attempts
    return final


def run_boss_resumes_command(cmd: list[str], source: str, timeout_seconds: int) -> dict[str, Any]:
    completed = run_command(cmd, timeout_seconds=timeout_seconds)
    if not completed["ok"]:
        combined = f"{completed.get('stdout', '')}\n{completed.get('stderr', '')}"
        return {
            "ok": False,
            "source": source,
            "command": cmd,
            "exit_code": completed.get("exit_code"),
            "stdout": completed.get("stdout", ""),
            "stderr": completed.get("stderr", ""),
            "results": [],
            "counts": {},
            "usable_count": 0,
            "failure_kind": completed.get("failure_kind") or classify_failure(combined) or "unknown",
            "errors": [completed.get("stderr", "").strip() or completed.get("stdout", "").strip() or "boss resumes failed"],
        }

    try:
        parsed = json.loads(completed["stdout"])
    except json.JSONDecodeError as exc:
        return {
            "ok": False,
            "source": source,
            "command": cmd,
            "exit_code": completed.get("exit_code"),
            "stdout": completed.get("stdout", ""),
            "stderr": completed.get("stderr", ""),
            "results": [],
            "counts": {},
            "usable_count": 0,
            "failure_kind": "json_parse_failed",
            "errors": [f"boss resumes did not return valid JSON: {exc}"],
        }

    parsed["command"] = cmd
    parsed["exit_code"] = completed.get("exit_code")
    parsed["stderr"] = completed.get("stderr", "")
    parsed["failure_kind"] = None
    return parsed


def run_resume_probe(
    boss_bin: str,
    source: str,
    root: Path,
    job_keyword: str,
    search_keyword: str | None,
    search_job: str | None,
    search_city: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    cmd = build_boss_resumes_command(
        boss_bin,
        source,
        root,
        1,
        job_keyword,
        search_keyword=search_keyword,
        search_job=search_job,
        search_city=search_city,
    )
    result = run_with_retries(
        source=source,
        operation="probe",
        runner=lambda: run_boss_resumes_command(cmd, source, timeout_seconds),
        is_success=lambda item: item.get("ok") and count_usable_results(item) >= 1,
    )
    usable_count = count_usable_results(result)
    result["usable_count"] = usable_count
    if not result.get("ok"):
        result["message"] = result.get("stderr") or result.get("stdout") or f"{source} resume probe failed"
        return result
    if usable_count < 1:
        result["ok"] = False
        result["failure_kind"] = "insufficient_data"
        result["message"] = f"{source} resume probe returned {usable_count} usable resumes"
        result["errors"] = [result["message"]]
        return result
    result["failure_kind"] = None
    result["message"] = ""
    return result


def run_preflight(
    boss_bin: str,
    job_keyword: str,
    resume_root: Path,
    search_keyword: str,
    search_job: str,
    search_city: str | None,
    timeout_seconds: int,
) -> dict[str, Any]:
    checks = [
        ("boss_help", boss_cmd(boss_bin, "help")),
    ]
    results: list[dict[str, Any]] = []
    for name, cmd in checks:
        result = run_command(cmd, timeout_seconds=timeout_seconds)
        combined = f"{result.get('stdout', '')}\n{result.get('stderr', '')}"
        if not result["ok"]:
            result["failure_kind"] = result.get("failure_kind") or classify_failure(combined) or "unknown"
            result["message"] = result.get("stderr") or result.get("stdout") or f"{name} failed"
        else:
            result["failure_kind"] = None
            result["message"] = ""
        result["name"] = name
        results.append(result)

    hard_failed = [item for item in results if item["name"] == "boss_help" and not item["ok"]]
    if hard_failed:
        return {
            "ok": False,
            "hard_ok": False,
            "probes_ok": False,
            "checks": results,
            "failure_kind": hard_failed[0]["failure_kind"],
            "message": hard_failed[0]["message"],
            "probe_failure_kind": None,
            "probe_message": "",
        }

    for source in DEFAULT_SOURCES:
        result = run_resume_probe(
            boss_bin,
            source,
            resume_root,
            job_keyword,
            search_keyword,
            search_job,
            search_city,
            timeout_seconds,
        )
        result["name"] = f"{source}_resume_probe"
        results.append(result)

    probe_failed = [item for item in results if item["name"] != "boss_help" and not item["ok"]]
    return {
        "ok": not hard_failed,
        "hard_ok": not hard_failed,
        "probes_ok": not probe_failed,
        "checks": results,
        "failure_kind": hard_failed[0]["failure_kind"] if hard_failed else None,
        "message": hard_failed[0]["message"] if hard_failed else "",
        "probe_failure_kind": probe_failed[0]["failure_kind"] if probe_failed else None,
        "probe_message": probe_failed[0]["message"] if probe_failed else "",
    }


def source_probe_results(preflight: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for check in preflight.get("checks", []):
        name = str(check.get("name") or "")
        suffix = "_resume_probe"
        if name.endswith(suffix):
            source = name[: -len(suffix)]
            out[source] = check
    return out


def run_boss_resumes(
    boss_bin: str,
    source: str,
    root: Path,
    job_keyword: str | None,
    search_keyword: str | None = None,
    search_job: str | None = None,
    search_city: str | None = None,
    timeout_seconds: int = 180,
) -> dict[str, Any]:
    cmd = build_boss_resumes_command(
        boss_bin,
        source,
        root,
        3,
        job_keyword,
        search_keyword=search_keyword,
        search_job=search_job,
        search_city=search_city,
    )
    if source == "search":
        if not search_keyword:
            return {
                "ok": False,
                "source": source,
                "command": cmd,
                "exit_code": None,
                "stdout": "",
                "stderr": "",
                "results": [],
                "counts": {},
                "usable_count": 0,
                "failure_kind": "insufficient_data",
                "errors": ["搜索关键词不明确。请传入 --search-keyword 或 --job-keyword。"],
            }

    return run_with_retries(
        source=source,
        operation="collection",
        runner=lambda: run_boss_resumes_command(cmd, source, timeout_seconds),
        is_success=lambda item: item.get("ok") and count_usable_results(item) >= 3,
    )


def path_exists(value: str | None) -> bool:
    return bool(value) and Path(value).exists()


def validate_source_result(source_result: dict[str, Any]) -> tuple[int, list[str], str | None]:
    errors: list[str] = []
    usable_count = 0
    for item in source_result.get("results", []):
        status = item.get("status")
        artifacts = item.get("artifacts") or {}
        resume_md = artifacts.get("resumeMarkdownPath")
        resume_json = artifacts.get("resumeJsonPath")
        if status in USABLE_STATUSES and path_exists(resume_md) and path_exists(resume_json):
            usable_count += 1
            continue
        name = item.get("candidateName") or "未知候选人"
        message = item.get("message") or "缺少简历产物文件"
        errors.append(f"{name}: {status} - {message}")

    if usable_count != 3:
        errors.append(f"{source_result.get('source')}: 需要 3 份可用简历，实际只有 {usable_count} 份")

    if source_result.get("failure_kind"):
        failure_kind = source_result["failure_kind"]
    elif usable_count != 3:
        failure_kind = classify_failure(json.dumps(source_result, ensure_ascii=False), usable_count=usable_count) or "insufficient_data"
    elif errors:
        failure_kind = "candidate_download_failed"
    else:
        failure_kind = None
    return usable_count, errors, failure_kind


def result_item_key(item: dict[str, Any]) -> str:
    artifacts = item.get("artifacts") or {}
    return (
        item.get("candidateId")
        or item.get("geekId")
        or artifacts.get("resumeJsonPath")
        or artifacts.get("resumeMarkdownPath")
        or f"{item.get('candidateName')}:{item.get('jobId')}:{item.get('status')}"
    )


def merge_result_items(*results: dict[str, Any] | None) -> list[dict[str, Any]]:
    seen: set[str] = set()
    merged: list[dict[str, Any]] = []
    for result in results:
        if not result:
            continue
        for item in result.get("results", []):
            key = result_item_key(item)
            if key in seen:
                continue
            seen.add(key)
            merged.append(item)
    return merged


def count_usable_items(items: list[dict[str, Any]]) -> int:
    return count_usable_results({"results": items})


def attach_probe_result(source_result: dict[str, Any], probe_result: dict[str, Any] | None) -> dict[str, Any]:
    result = dict(source_result)
    if probe_result:
        result["probe_result"] = probe_result
        result["probe_usable_count"] = count_usable_results(probe_result)
    else:
        result["probe_result"] = None
        result["probe_usable_count"] = 0
    effective_results = merge_result_items(result, probe_result)
    result["effective_results"] = effective_results
    result["effective_usable_count"] = count_usable_items(effective_results)
    return result


def flatten_items(source_results: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for source, result in source_results.items():
        for item in result.get("effective_results") or result.get("results", []):
            artifacts = item.get("artifacts") or {}
            items.append(
                {
                    "source": source,
                    "candidateName": item.get("candidateName"),
                    "candidateId": item.get("candidateId"),
                    "jobName": item.get("jobName"),
                    "jobId": item.get("jobId"),
                    "status": item.get("status"),
                    "message": item.get("message"),
                    "resumeMarkdownPath": artifacts.get("resumeMarkdownPath"),
                    "resumeJsonPath": artifacts.get("resumeJsonPath"),
                    "rawResponsePath": artifacts.get("rawResponsePath"),
                }
            )
    return items


def unique_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        key = item.get("candidateId") or f"{item.get('source')}:{item.get('candidateName')}"
        if key in seen:
            continue
        seen.add(key)
        out.append(item)
    return out


def troubleshooting_hint(failure_kind: str | None) -> str:
    if failure_kind == "boss_not_found":
        return "设置 --boss-bin 或 BOSS_BIN，或把包含 boss.cmd 的 npm 全局目录加入 PATH。"
    if failure_kind == "login_or_session":
        return "先运行 boss login，并确认当前浏览器会话仍保持 BOSS 登录态。"
    if failure_kind == "permission_or_page_unavailable":
        return "手动打开 BOSS，确认当前账号可以访问推荐页和深搜页。"
    if failure_kind == "json_parse_failed":
        return "手动运行 summary/manifest 中记录的命令；使用 --json 时 stdout 必须是纯 JSON。"
    if failure_kind == "insufficient_data":
        return "环境可用，但该来源返回的本地可用简历不足 3 份。"
    if failure_kind == "candidate_download_failed":
        return "查看候选人级错误；部分候选人可能缺少在线简历标识或产物文件。"
    return "查看 collection_manifest.json 中失败命令的 stdout/stderr。"


def empty_source_result(source: str, failure_kind: str, message: str) -> dict[str, Any]:
    return {
        "ok": False,
        "status": "collection_not_started",
        "source": source,
        "command": [],
        "exit_code": None,
        "stdout": "",
        "stderr": "",
        "results": [],
        "counts": {},
        "usable_count": 0,
        "probe_usable_count": 0,
        "effective_usable_count": 0,
        "effective_results": [],
        "failure_kind": failure_kind,
        "errors": [message],
    }


def write_summary(path: Path, manifest: dict[str, Any]) -> None:
    lines = [
        "# BOSS JD 简历采集结果",
        "",
        f"- 本轮成功: {manifest['ok']}",
        f"- 可用简历总数: {manifest['usable_total']}",
        f"- 各来源可用数: {manifest['source_usable_counts']}",
        f"- 失败来源: {', '.join(manifest['failed_sources']) or '无'}",
        f"- JD 来源: {manifest['jd']['source']}",
        f"- 岗位关键词: {manifest['jd'].get('job_keyword') or '无'}",
        f"- boss 命令: {manifest['boss']['selected'] or '未找到'}",
        f"- 简历根目录: {manifest['resume_root']}",
        "",
        "## 采集前自检",
        "",
    ]
    preflight = manifest["preflight"]
    lines.append(f"- 自检通过: {preflight['ok']}")
    lines.append(f"- 硬性自检通过: {preflight.get('hard_ok', preflight['ok'])}")
    lines.append(f"- 来源 probe 全部通过: {preflight.get('probes_ok', preflight['ok'])}")
    if preflight.get("probe_failure_kind"):
        lines.append(f"- probe 失败分类: {preflight.get('probe_failure_kind')}")
    if not preflight["ok"]:
        lines.append(f"- 失败分类: {preflight.get('failure_kind') or 'unknown'}")
        lines.append(f"- 下一步建议: {troubleshooting_hint(preflight.get('failure_kind'))}")
    for check in preflight.get("checks", []):
        detail = f"- {check['name']}: 通过={check['ok']}, 失败分类={check.get('failure_kind') or '无'}"
        if check.get("operation") == "probe":
            detail += f", 可用={check.get('usable_count', 0)}, attempts={check.get('attempt_count', 1)}/{check.get('max_attempts', 1)}"
        lines.append(detail)
        if check.get("message"):
            lines.append(f"  - {check['message']}")

    lines.extend(["", "## 来源采集结果", ""])
    for source in manifest["requested_sources"]:
        result = manifest["sources"][source]
        lines.append(
            f"- {source}: 正式采集可用={result['usable_count']}/3, "
            f"状态={result.get('status') or 'unknown'}, "
            f"probe 可用={result.get('probe_usable_count', 0)}, "
            f"实际已落地可用={result.get('effective_usable_count', result['usable_count'])}, "
            f"失败分类={result.get('failure_kind') or '无'}, "
            f"attempts={result.get('attempt_count', 1)}/{result.get('max_attempts', 1)}, "
            f"downloaded={result.get('counts', {}).get('downloaded', 0)}, "
            f"skipped_existing={result.get('counts', {}).get('skipped_existing', 0)}, "
            f"missing_identifiers={result.get('counts', {}).get('missing_identifiers', 0)}, "
            f"download_failed={result.get('counts', {}).get('download_failed', 0)}"
        )
        if result.get("failure_kind"):
            lines.append(f"  - 下一步建议: {troubleshooting_hint(result.get('failure_kind'))}")
        for error in result.get("errors", []):
            lines.append(f"  - {error}")
    path.write_text("\n".join(lines).rstrip() + "\n", encoding="utf-8")


def build_manifest(
    *,
    run_dir: Path,
    resume_root: Path,
    jd_source: str,
    jd_path: Path,
    job_keyword: str,
    boss_info: dict[str, Any],
    preflight: dict[str, Any],
    sources: dict[str, dict[str, Any]],
    requested_sources: tuple[str, ...],
) -> dict[str, Any]:
    items = flatten_items(sources)
    source_usable_counts = {
        source: sources[source].get("effective_usable_count", sources[source]["usable_count"])
        for source in requested_sources
    }
    source_collection_counts = {source: sources[source]["usable_count"] for source in requested_sources}
    source_probe_counts = {source: sources[source].get("probe_usable_count", 0) for source in requested_sources}
    failed_sources = [
        source
        for source in requested_sources
        if sources[source]["usable_count"] != 3 or bool(sources[source].get("errors"))
    ]
    return {
        "ok": preflight["ok"] and all(source_collection_counts[source] == 3 and not sources[source]["errors"] for source in requested_sources),
        "created_at": datetime.now().isoformat(timespec="seconds"),
        "requested_sources": list(requested_sources),
        "run_dir": str(run_dir),
        "resume_root": str(resume_root),
        "boss": boss_info,
        "preflight": preflight,
        "jd": {
            "source": jd_source,
            "path": str(jd_path),
            "job_keyword": job_keyword,
        },
        "sources": sources,
        "items": items,
        "unique_items": unique_items(items),
        "usable_total": sum(source_usable_counts.values()),
        "source_usable_counts": source_usable_counts,
        "source_collection_counts": source_collection_counts,
        "source_probe_counts": source_probe_counts,
        "failed_sources": failed_sources,
    }


def write_outputs(run_dir: Path, manifest: dict[str, Any]) -> tuple[Path, Path]:
    manifest_path = run_dir / "collection_manifest.json"
    summary_path = run_dir / "collection_summary.md"
    manifest_path.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    write_summary(summary_path, manifest)
    return manifest_path, summary_path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--jd-file")
    parser.add_argument("--jd-text")
    parser.add_argument("--job-keyword")
    parser.add_argument("--boss-bin")
    parser.add_argument("--resume-root", default=str(default_resume_root()))
    parser.add_argument("--runs-root", default=str(default_runs_root()))
    parser.add_argument("--command-timeout-seconds", type=int, default=900)
    parser.add_argument("--search-keyword", help="search 来源使用的必填关键词；默认使用 --job-keyword 或 JD 推断关键词。")
    parser.add_argument("--search-job", help="search 来源可选岗位筛选；只有显式传入时才启用。")
    parser.add_argument("--search-city", help="search 来源可选城市筛选。")
    args = parser.parse_args()
    requested_sources = DEFAULT_SOURCES

    jd_text, jd_source = read_jd(args)
    job_keyword = (args.job_keyword or "").strip() or infer_job_keyword(jd_text)
    if not job_keyword:
        raise SystemExit("岗位关键词不明确。请先传入 --job-keyword 后再采集简历。")

    search_keyword = (args.search_keyword or "").strip() or job_keyword
    search_job = (args.search_job or "").strip() or job_keyword
    search_city = (args.search_city or "").strip() or None

    created_at = datetime.now().strftime("%Y%m%d_%H%M%S")
    runs_root = Path(args.runs_root).expanduser()
    run_dir = runs_root / f"{created_at}_{safe_segment(job_keyword)}"
    run_dir.mkdir(parents=True, exist_ok=False)

    jd_path = run_dir / "jd.md"
    jd_path.write_text(jd_text.strip() + "\n", encoding="utf-8")

    resume_root = Path(args.resume_root).expanduser()
    boss_bin, boss_info = resolve_boss_bin(args.boss_bin)
    sources: dict[str, dict[str, Any]]
    if not boss_bin:
        preflight = {
            "ok": False,
            "checks": [],
            "failure_kind": "boss_not_found",
            "message": boss_info["message"],
        }
        sources = {source: empty_source_result(source, "boss_not_found", boss_info["message"]) for source in requested_sources}
    else:
        preflight = run_preflight(
            boss_bin,
            job_keyword,
            resume_root,
            search_keyword,
            search_job,
            search_city,
            args.command_timeout_seconds,
        )
        probes = source_probe_results(preflight)
        sources = {}
        if preflight["ok"]:
            for source in requested_sources:
                result = run_boss_resumes(
                    boss_bin,
                    source,
                    resume_root,
                    job_keyword,
                    search_keyword=search_keyword,
                    search_job=search_job,
                    search_city=search_city,
                    timeout_seconds=args.command_timeout_seconds,
                )
                usable_count, errors, failure_kind = validate_source_result(result)
                result["usable_count"] = usable_count
                result["errors"] = errors
                result["failure_kind"] = failure_kind
                result["status"] = "collection_failed" if failure_kind or errors else "collection_finished"
                sources[source] = attach_probe_result(result, probes.get(source))
        else:
            message = preflight.get("message") or "采集前自检失败"
            failure_kind = preflight.get("failure_kind") or "unknown"
            sources = {source: empty_source_result(source, failure_kind, message) for source in requested_sources}

    manifest = build_manifest(
        run_dir=run_dir,
        resume_root=resume_root,
        jd_source=jd_source,
        jd_path=jd_path,
        job_keyword=job_keyword,
        boss_info=boss_info,
        preflight=preflight,
        sources=sources,
        requested_sources=requested_sources,
    )
    manifest["command_timeout_seconds"] = args.command_timeout_seconds
    manifest_path, summary_path = write_outputs(run_dir, manifest)

    print(
        json.dumps(
            {
                "ok": manifest["ok"],
                "run_dir": str(run_dir),
                "manifest": str(manifest_path),
                "summary": str(summary_path),
                "job_keyword": job_keyword,
                "usable_total": manifest["usable_total"],
                "source_usable_counts": manifest["source_usable_counts"],
                "failed_sources": manifest["failed_sources"],
                "preflight_ok": manifest["preflight"]["ok"],
                "preflight_failure_kind": manifest["preflight"].get("failure_kind"),
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0 if manifest["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
