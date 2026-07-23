#!/usr/bin/env python3
"""Preflight check — one go/no-go signal before you run anything.

Verifies the environment a coding agent (or a human) needs to run the
deployment gate end to end: Python version, dependencies, credentials, and
live Langfuse connectivity. Prints a PASS / WARN / FAIL line per check and
exits non-zero if any hard requirement is missing, so it wires cleanly into
`make` or a CI step.

Usage:
    uv run python scripts/preflight.py       # (or: python scripts/preflight.py)

Env resolution matches the rest of the repo: .env is loaded with
override=True, the Langfuse host is LANGFUSE_BASE_URL -> LANGFUSE_HOST ->
https://cloud.langfuse.com, and an LLM key is ANTHROPIC_API_KEY (Claude, the
default) or LLM_API_KEY / OPENAI_API_KEY (OpenAI-compatible).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Tally of hard failures (exit 1) and soft warnings (exit 0, but flagged).
_fails: list[str] = []
_warns: list[str] = []


def _p(status: str, msg: str, hint: str = "") -> None:
    line = f"[{status:<4}] {msg}"
    if hint:
        line += f"\n         -> {hint}"
    print(line)


def ok(msg: str) -> None:
    _p("PASS", msg)


def warn(msg: str, hint: str = "") -> None:
    _warns.append(msg)
    _p("WARN", msg, hint)


def fail(msg: str, hint: str = "") -> None:
    _fails.append(msg)
    _p("FAIL", msg, hint)


def check_python() -> None:
    v = sys.version_info
    if (v.major, v.minor) >= (3, 11):
        ok(f"Python {v.major}.{v.minor}.{v.micro} (>= 3.11)")
    else:
        fail(
            f"Python {v.major}.{v.minor} is too old (need >= 3.11)",
            "Install Python 3.11+ (see pyproject.toml requires-python).",
        )


def check_dependencies() -> None:
    required = ["langfuse", "dotenv", "openai", "anthropic", "fastapi"]
    missing = []
    for mod in required:
        try:
            __import__(mod)
        except ImportError:
            missing.append(mod)
    if not missing:
        ok("Core dependencies importable (langfuse, dotenv, openai, anthropic, fastapi)")
    else:
        pkg = {"dotenv": "python-dotenv"}
        names = ", ".join(pkg.get(m, m) for m in missing)
        fail(
            f"Missing dependencies: {names}",
            "Run `uv sync` (or `pip install -r requirements.txt`), "
            "then invoke this via `uv run python scripts/preflight.py`.",
        )


def check_env_file() -> None:
    env = REPO_ROOT / ".env"
    if env.exists() or env.is_symlink():
        target = f" -> {os.readlink(env)}" if env.is_symlink() else ""
        ok(f".env present{target}")
    else:
        warn(
            ".env not found",
            "Copy .env.example to .env and fill in credentials "
            "(or export the LANGFUSE_*/ *_API_KEY vars in your shell).",
        )


def check_langfuse_creds() -> tuple[str | None, str | None, str]:
    pk = os.getenv("LANGFUSE_PUBLIC_KEY")
    sk = os.getenv("LANGFUSE_SECRET_KEY")
    host = os.getenv("LANGFUSE_BASE_URL", os.getenv("LANGFUSE_HOST", "https://cloud.langfuse.com"))
    if pk and sk:
        ok(f"Langfuse credentials set (host: {host})")
    else:
        missing = " and ".join(
            n for n, val in [("LANGFUSE_PUBLIC_KEY", pk), ("LANGFUSE_SECRET_KEY", sk)] if not val
        )
        fail(
            f"Missing {missing}",
            "HUMAN STEP: create a Langfuse project and paste its API keys into .env. "
            "Cloud: https://cloud.langfuse.com  |  Self-host: see selfhost/README.md.",
        )
    return pk, sk, host


def check_llm_key() -> None:
    anthropic = os.getenv("ANTHROPIC_API_KEY")
    openai = os.getenv("LLM_API_KEY") or os.getenv("OPENAI_API_KEY")
    providers = []
    if anthropic:
        providers.append("Anthropic/Claude")
    if openai:
        providers.append("OpenAI-compatible")
    if providers:
        ok(f"LLM API key set ({', '.join(providers)})")
    else:
        fail(
            "No LLM API key found (need ANTHROPIC_API_KEY or LLM_API_KEY/OPENAI_API_KEY)",
            "HUMAN STEP: add a key to .env. The default model (claude-sonnet-4-6) needs "
            "ANTHROPIC_API_KEY. Dataset loading / --dry-run do not need an LLM key.",
        )


def check_langfuse_connectivity(pk: str | None, sk: str | None, host: str) -> None:
    if not (pk and sk):
        warn("Skipping Langfuse connectivity check (credentials missing)")
        return
    try:
        from langfuse import Langfuse
    except ImportError:
        warn("Skipping Langfuse connectivity check (langfuse not importable)")
        return
    try:
        client = Langfuse(public_key=pk, secret_key=sk, host=host)
        if client.auth_check():
            ok(f"Langfuse reachable and credentials valid ({host})")
        else:
            fail(
                f"Langfuse auth_check failed for {host}",
                "Keys may be wrong, or the host is not reachable. If self-hosting, "
                "confirm the stack is up: docker compose -f selfhost/docker-compose.yml ps",
            )
    except Exception as exc:  # noqa: BLE001 - surface any connectivity error verbatim
        fail(
            f"Could not reach Langfuse at {host}: {exc}",
            "Check LANGFUSE_BASE_URL and network access. Self-host: `make up` first.",
        )


def main() -> int:
    try:
        from dotenv import load_dotenv

        load_dotenv(override=True)
    except ImportError:
        pass  # python-dotenv is optional; env may come from the shell.

    print("Preflight — deployment-gate environment check")
    print("=" * 60)
    check_python()
    check_dependencies()
    check_env_file()
    pk, sk, host = check_langfuse_creds()
    check_llm_key()
    check_langfuse_connectivity(pk, sk, host)
    print("=" * 60)

    if _fails:
        print(f"RESULT: NOT READY — {len(_fails)} blocking issue(s), {len(_warns)} warning(s).")
        print("Fix the FAIL items above, then re-run `make preflight`.")
        return 1
    if _warns:
        print(f"RESULT: READY (with {len(_warns)} warning(s)). You can run the gate.")
        return 0
    print("RESULT: READY. All checks passed — you can run the gate end to end.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
