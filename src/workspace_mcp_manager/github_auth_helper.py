from __future__ import annotations

import argparse
import os
import signal
import subprocess
import termios
from pathlib import Path
from typing import Sequence

from .development_environment import MANAGED_GITHUB_ENV_REMOVE


QUALIFIED_LOGIN_ARGS = (
    "auth",
    "login",
    "--hostname",
    "github.com",
    "--with-token",
    "--insecure-storage",
)

HELPER_OK = 0
HELPER_LOGIN_FAILED = 20
HELPER_TTY_REQUIRED = 21
HELPER_INPUT_EMPTY = 22
HELPER_TIMEOUT = 23
HELPER_INTERRUPTED = 24
HELPER_CANCELLED = 25
HELPER_INPUT_TOO_LARGE = 26

MAX_CREDENTIAL_BYTES = 16 * 1024


def _warning(instance_id: str, profile: str) -> str:
    return f"""GitHub access setup — {instance_id}

Profile:
  {profile}

Paste the GitHub access token below.

Input is VISIBLE so you can verify that paste succeeded.

Anyone who can see this terminal, terminal scrollback,
or a screen recording can see the token while it is entered.

GitHub CLI will store the credential inside this
instance's private manager-owned GitHub profile using
the qualified local plaintext-storage mode.

The token will not be stored in manager declarations,
arguments, environment variables, logs, diagnostics,
plans, verification records, or TUI state.

Token:
> """


def _login_env(*, home: str, exec_path: str, profile: str) -> dict[str, str]:
    env = dict(os.environ)
    for key in MANAGED_GITHUB_ENV_REMOVE:
        env.pop(key, None)
    env["HOME"] = home
    env["PATH"] = exec_path
    env["GH_CONFIG_DIR"] = profile
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GH_PROMPT_DISABLED"] = "1"
    env["GH_NO_UPDATE_NOTIFIER"] = "1"
    env.pop("SSH_AUTH_SOCK", None)
    return env


def _settle(process: subprocess.Popen[bytes]) -> None:
    if process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=2.0)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=2.0)


def _read_visible_line(tty_fd: int) -> tuple[int, bytes | None]:
    attributes = termios.tcgetattr(tty_fd)
    original = list(attributes)
    if not (attributes[3] & termios.ECHO):
        attributes[3] |= termios.ECHO
        termios.tcsetattr(tty_fd, termios.TCSANOW, attributes)
    try:
        data = os.read(tty_fd, MAX_CREDENTIAL_BYTES + 2)
    except KeyboardInterrupt:
        return HELPER_CANCELLED, None
    finally:
        if attributes != original:
            termios.tcsetattr(tty_fd, termios.TCSANOW, original)
    if len(data) > MAX_CREDENTIAL_BYTES + 1 or (len(data) == MAX_CREDENTIAL_BYTES + 2 and not data.endswith(b"\n")):
        return HELPER_INPUT_TOO_LARGE, None
    value = data.rstrip(b"\r\n")
    if not value:
        return HELPER_INPUT_EMPTY, None
    return HELPER_OK, value


def run_helper(
    *,
    instance_id: str,
    profile: str,
    gh_binary: str,
    home: str,
    exec_path: str,
    timeout_seconds: float,
) -> int:
    if not Path(gh_binary).is_absolute() or not Path(profile).is_absolute() or not Path(home).is_absolute():
        return HELPER_LOGIN_FAILED
    try:
        tty_fd = os.open("/dev/tty", os.O_RDWR | os.O_NOCTTY)
    except OSError:
        return HELPER_TTY_REQUIRED
    try:
        if not os.isatty(tty_fd):
            return HELPER_TTY_REQUIRED
        os.write(tty_fd, _warning(instance_id, profile).encode("utf-8", errors="strict"))
        read_status, credential = _read_visible_line(tty_fd)
        if read_status != HELPER_OK or credential is None:
            return read_status

        old_umask = os.umask(0o077)
        process: subprocess.Popen[bytes] | None = None
        submitted = False
        try:
            process = subprocess.Popen(
                [gh_binary, *QUALIFIED_LOGIN_ARGS],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                shell=False,
                start_new_session=False,
                env=_login_env(home=home, exec_path=exec_path, profile=profile),
            )
            submitted = True
            try:
                process.communicate(input=credential + b"\n", timeout=timeout_seconds)
            except subprocess.TimeoutExpired:
                _settle(process)
                return HELPER_TIMEOUT
            except KeyboardInterrupt:
                _settle(process)
                return HELPER_INTERRUPTED if submitted else HELPER_CANCELLED
            return HELPER_OK if process.returncode == 0 else HELPER_LOGIN_FAILED
        except KeyboardInterrupt:
            if process is not None:
                _settle(process)
            return HELPER_INTERRUPTED if submitted else HELPER_CANCELLED
        except OSError:
            if process is not None:
                _settle(process)
            return HELPER_LOGIN_FAILED
        finally:
            credential = None
            os.umask(old_umask)
    finally:
        os.close(tty_fd)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="workspace-mcp-manager-github-auth-helper")
    parser.add_argument("--instance-id", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--gh-binary", required=True)
    parser.add_argument("--home", required=True)
    parser.add_argument("--exec-path", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=120.0)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return run_helper(
        instance_id=args.instance_id,
        profile=args.profile,
        gh_binary=args.gh_binary,
        home=args.home,
        exec_path=args.exec_path,
        timeout_seconds=max(1.0, min(float(args.timeout_seconds), 120.0)),
    )


if __name__ == "__main__":
    signal.signal(signal.SIGPIPE, signal.SIG_DFL)
    raise SystemExit(main())
