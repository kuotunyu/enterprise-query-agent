"""Create local-only independent credentials once; never prints their contents."""
from pathlib import Path
import secrets


def configure(directory):
    directory=Path(directory)
    directory.mkdir(parents=True,exist_ok=True)
    bootstrap=directory/'bootstrap.env'
    runtime=directory/'runtime.env'
    if bootstrap.exists() and runtime.exists():
        return
    if bootstrap.exists() or runtime.exists():
        raise RuntimeError('Incomplete credential pair; restore the missing local env file before continuing')
    root_password=secrets.token_urlsafe(32)
    reader_password=secrets.token_urlsafe(32)
    runtime_text=f'EQA_DB_HOST=127.0.0.1\nEQA_DB_PORT=3307\nEQA_DB_USER=eqa_reader\nEQA_DB_PASSWORD={reader_password}\nEQA_DATASET_ID=synthetic-v1\n'
    bootstrap.write_text(f'EQA_BOOTSTRAP_PASSWORD={root_password}\n'+runtime_text,encoding='utf-8')
    runtime.write_text(runtime_text,encoding='utf-8')


if __name__=='__main__':
    configure(Path(__file__).resolve().parents[1]/'.local')
    print('Local bootstrap/runtime credentials ready (contents not displayed).')
