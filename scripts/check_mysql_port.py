"""Read-only port check for first startup / a stopped MySQL service."""
import argparse
import os
from pathlib import Path
import socket
import sys

from dotenv import dotenv_values


def check(env_files):
    ports = []
    for path in env_files:
        if not Path(path).is_file():
            raise RuntimeError("Missing local env file; create the documented local configuration first.")
        values = dotenv_values(path, interpolate=False)
        # Shell variables override local files for both Compose and Python clients.
        raw = os.environ.get("EQA_DB_PORT", values.get("EQA_DB_PORT", "3307"))
        try:
            port = int(raw)
            if not 1 <= port <= 65535:
                raise ValueError
        except (ValueError, TypeError):
            raise RuntimeError("EQA_DB_PORT must be a literal integer from 1 to 65535.") from None
        ports.append(port)
    if len(set(ports)) != 1:
        raise RuntimeError("Local env files must agree on EQA_DB_PORT; update Compose and client settings together.")
    port = ports[0]
    if port == 3306:
        raise RuntimeError("EQA requires a separate MySQL host port; 3306 is not allowed.")
    try:
        with socket.socket() as probe:
            if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
                probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
            probe.bind(("127.0.0.1", port))
    except OSError:
        raise RuntimeError(
            f"Port {port} is occupied or unavailable. No service was stopped. "
            "If this project's MySQL is already running, skip this pre-start check. "
            "Otherwise choose a free EQA_DB_PORT in all documented env files."
        ) from None
    return port


def main():
    root = Path(__file__).resolve().parents[1]
    defaults = [root / ".local/bootstrap.env", root / ".local/runtime.env"]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, action="append", help="Explicit env files (repeat for each client); replaces defaults")
    args = parser.parse_args()
    try:
        port = check(args.env_file or defaults)
    except (RuntimeError, OSError) as exc:
        # Never print env contents, credentials, or parser exceptions.
        sys.exit(str(exc) if isinstance(exc, RuntimeError) else "Cannot read local env file.")
    print(f"Port {port} is available now; Compose remains the final bind check.")


if __name__ == "__main__":
    main()
