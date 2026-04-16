"""
afterimage.cli
==============
Command-line entry point for AFTERIMAGE.

Security hardening vs. the original script
-------------------------------------------
* ``--password`` / ``-p`` is intentionally **removed**.  Passwords passed as
  CLI arguments appear in:
    - shell history (``~/.bash_history``, ``~/.zsh_history``, …)
    - ``ps aux`` / ``/proc/<pid>/cmdline``
    - system audit logs (auditd, macOS Unified Log, Windows Event Log)

  Instead, the password is always read interactively via ``getpass.getpass``
  which writes to ``/dev/tty`` directly, bypassing shell history and process
  listings.

  If the calling environment cannot provide a TTY (e.g. a CI pipeline),
  set the environment variable ``AFTERIMAGE_PASSWORD`` instead.  That
  variable is read once and immediately cleared from ``os.environ``.

Usage
-----
    # Transmit
    python -m afterimage --tx secret.zip

    # Receive
    python -m afterimage --rx output.zip [--camera 1]

    # Non-interactive (CI / scripting)
    AFTERIMAGE_PASSWORD=hunter2 python -m afterimage --tx secret.zip
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys

from .protocol import AfterImage

__all__ = ["main"]

_ENV_VAR = "AFTERIMAGE_PASSWORD"


def _get_password(confirm: bool = False) -> str:
    """
    Retrieve the encryption password through the safest available channel.

    Priority order
    --------------
    1. Environment variable ``AFTERIMAGE_PASSWORD`` (non-interactive mode).
       The variable is deleted from ``os.environ`` immediately after reading.
    2. Interactive ``getpass`` prompt (writes directly to ``/dev/tty``).

    Parameters
    ----------
    confirm:
        When ``True`` (transmitter mode), ask the user to enter the password
        twice and abort if they don't match.

    Returns
    -------
    str
        The passphrase, guaranteed non-empty.
    """
    # ── Non-interactive path ───────────────────────────────────────────────
    env_pw = os.environ.get(_ENV_VAR)
    if env_pw is not None:
        # Scrub the env var so child processes cannot inherit it
        os.environ.pop(_ENV_VAR, None)
        if not env_pw:
            print(
                f"[!] {_ENV_VAR} is set but empty — aborting.",
                file=sys.stderr,
            )
            sys.exit(1)
        return env_pw

    # ── Interactive path ───────────────────────────────────────────────────
    try:
        password = getpass.getpass("Password: ")
        if not password:
            print("[!] Password must not be empty.", file=sys.stderr)
            sys.exit(1)

        if confirm:
            confirm_pw = getpass.getpass("Confirm password: ")
            if password != confirm_pw:
                print("[!] Passwords do not match — aborting.", file=sys.stderr)
                sys.exit(1)

        return password

    except (EOFError, KeyboardInterrupt):
        print("\n[!] Interrupted.", file=sys.stderr)
        sys.exit(1)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="afterimage",
        description="AFTERIMAGE — Optical Air-Gap Data Exfiltration Protocol",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
examples:
  transmit a file:
    python -m afterimage --tx secret.zip

  receive to a file:
    python -m afterimage --rx recovered.zip

  use a specific camera:
    python -m afterimage --rx recovered.zip --camera 1

  non-interactive (password via env var):
    AFTERIMAGE_PASSWORD=... python -m afterimage --tx secret.zip

security note:
  never pass the password as --password on the command line.
  it will appear in shell history and process listings.
        """,
    )

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument(
        "--tx",
        metavar="FILE",
        help="transmit FILE as a QR stream (sender mode)",
    )
    mode.add_argument(
        "--rx",
        metavar="OUTPUT",
        help="receive from camera and save to OUTPUT (receiver mode)",
    )

    parser.add_argument(
        "--camera",
        "-c",
        type=int,
        default=0,
        metavar="IDX",
        help="camera device index (default: 0)",
    )

    return parser


def main(argv: list | None = None) -> int:
    """
    Entry point for ``python -m afterimage`` and the ``afterimage`` console
    script installed by pip.

    Returns
    -------
    int
        Exit code (0 = success, 1 = failure).
    """
    parser = _build_parser()
    args = parser.parse_args(argv)

    if args.tx:
        password = _get_password(confirm=True)
        AfterImage(password).tx(args.tx)
        return 0

    # args.rx
    password = _get_password(confirm=False)
    success = AfterImage(password).rx(args.rx, camera_idx=args.camera)
    return 0 if success else 1


if __name__ == "__main__":
    sys.exit(main())