"""Create local-only independent credentials once; never prints their contents."""
import argparse
from pathlib import Path
from dotenv import dotenv_values
import secrets


def configure(directory, port=None):
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Port must be from 1 to 65535")
    if port == 3306:
        raise ValueError("EQA requires a separate MySQL host port; 3306 is not allowed")
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    bootstrap=directory/'bootstrap.env'
    runtime=directory/'runtime.env'
    if bootstrap.exists() and runtime.exists():
        if port is not None and any(
            dotenv_values(path, interpolate=False).get("EQA_DB_PORT", "3307") != str(port)
            for path in (bootstrap, runtime)
        ):
            raise RuntimeError("Refusing to change existing credentials/config; edit EQA_DB_PORT in both local env files explicitly")
        return
    if bootstrap.exists() or runtime.exists():
        raise RuntimeError('Incomplete credential pair; restore the missing local env file before continuing')
    root_password=secrets.token_urlsafe(32)
    reader_password=secrets.token_urlsafe(32)
    runtime_text=f'EQA_DB_HOST=127.0.0.1\nEQA_DB_PORT={port or 3307}\nEQA_DB_USER=eqa_reader\nEQA_DB_PASSWORD={reader_password}\nEQA_DATASET_ID=synthetic-v1\n'
    bootstrap.write_text(f'EQA_BOOTSTRAP_PASSWORD={root_password}\n'+runtime_text,encoding='utf-8')
    runtime.write_text(runtime_text,encoding='utf-8')


if __name__=='__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, help='MySQL host port for a new credential pair (default 3307)')
    args = parser.parse_args()
    configure(Path(__file__).resolve().parents[1]/'.local', port=args.port)
    print('Local bootstrap/runtime credentials ready (contents not displayed).')
