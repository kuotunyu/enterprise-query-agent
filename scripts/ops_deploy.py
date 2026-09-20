"""Explicitly scoped local operations deployment. Never reads an existing env file.

Every operation writes a fresh nonsecret receipt, including failures. Docker output
is retained after redacting this stack's generated credentials, never streamed.
"""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import socket
import subprocess
import time
from uuid import uuid4
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
OPS = ROOT / '.local' / 'ops'
IMAGE_ID = re.compile(r'sha256:[0-9a-f]{64}')


def validate_stack(name):
    if not isinstance(name, str) or not re.fullmatch(r'eqaops-[a-z0-9][a-z0-9-]{0,39}', name):
        raise ValueError('Use a unique eqaops- name with lowercase letters/digits/hyphens')
    return name


def validate_port(port):
    if type(port) is not int or not 1024 <= port <= 65535 or port in (3306, 3307, 3308):
        raise ValueError('HTTP port must be 1024..65535 and not a database port')
    return port


def stack_path(root, name):
    validate_stack(name)
    root = root.absolute()
    if root.resolve() != root:
        raise ValueError('Operations root must not redirect through a linked directory')
    path = root / name
    if path.resolve() != path or path.resolve().parent != root:
        raise ValueError('Stack path must stay directly beneath the operations directory')
    return path


def write_json(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('x', encoding='utf-8') as handle:
        json.dump(data, handle, ensure_ascii=False, indent=2)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def credentials(path):
    names = ('runtime.env', 'bootstrap.env', 'mysql.env')
    if any((path / name).exists() for name in names):
        raise FileExistsError('Refusing to replace credentials')
    reader, admin = secrets.token_hex(32), secrets.token_hex(32)
    runtime = f'EQA_DB_USER=eqa_reader\nEQA_DB_PASSWORD={reader}\nEQA_DATASET_ID=synthetic-v1\n'
    texts = (runtime, f'EQA_BOOTSTRAP_PASSWORD={admin}\n{runtime}', f'MYSQL_ROOT_PASSWORD={admin}\n')
    for name, text in zip(names, texts):
        fd = os.open(path / name, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as handle:
            handle.write(text)


def context_files(paths):
    files = {'Dockerfile.ops', '.dockerignore', 'pyproject.toml', 'uv.lock', 'README.md', 'LICENSE'}
    prefixes = ('src/', 'scripts/', 'tests/', 'catalog/', 'db/', 'data/synthetic/')
    return [p for p in paths if (p in files or p.startswith(prefixes))
            and not any(part.startswith('.') or part == '__pycache__' for part in Path(p).parts)
            and not p.endswith('.pyc')] + [p for p in paths if p == '.dockerignore']


def validate_image(receipt):
    if receipt.get('kind') != 'eqaops-image' or not IMAGE_ID.fullmatch(receipt.get('image_id', '')):
        raise ValueError('A successful lab image receipt with an exact image ID is required')
    return receipt['image_id']


def materialize_context(source, target, hashes):
    target.mkdir(parents=True, exist_ok=False)
    for path, expected in hashes.items():
        full = source / path
        if full.is_symlink() or not full.resolve().is_relative_to(source.resolve()):
            raise ValueError('Build context contains a linked path')
        destination = target / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(full, destination)
        if digest(destination) != expected:
            raise ValueError('Source changed after build snapshot')


def command(args, *, env=None, timeout=300, redact=(), stdin=None, combined=False):
    process = subprocess.run(args, cwd=ROOT, env=env, input=stdin, capture_output=True,
                             timeout=timeout)
    output = (process.stdout + (process.stderr if combined or process.returncode else b'')).decode('utf-8', errors='replace')
    for value in redact:
        output = output.replace(value, '[REDACTED]')
    if process.returncode:
        raise RuntimeError(f'Command failed ({process.returncode}): {output[-12000:]}')
    return output.strip()


def inspect_image(reference):
    result = json.loads(command(['docker', 'image', 'inspect', reference]))[0]
    return {'id': result['Id'], 'repo_digests': result.get('RepoDigests', []),
            'labels': result['Config'].get('Labels') or {}}


@contextmanager
def operation(directory, action):
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / (datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S') + '-' + action + '-' + uuid4().hex[:8] + '.json')
    receipt = {'action': action, 'started_utc': datetime.now(timezone.utc).isoformat(), 'status': 'running'}
    started = time.perf_counter()
    try:
        yield receipt
    except Exception as exc:
        receipt.update(status='failed', error=str(exc))
        raise
    else:
        receipt['status'] = 'passed'
    finally:
        receipt['elapsed_seconds'] = time.perf_counter() - started
        write_json(path, receipt)
        print(json.dumps({'receipt': str(path), 'status': receipt['status']}))


def build(target):
    with operation(OPS / 'builds', 'build-' + target) as result:
        sha = command(['git', 'rev-parse', 'HEAD'])
        dirty = bool(command(['git', 'status', '--porcelain']))
        paths = context_files(command(['git', 'ls-files']).splitlines())
        if 'Dockerfile.ops' not in paths:
            raise ValueError('Commit or stage deployment sources before building')
        hashes = {p: digest(ROOT / p) for p in paths}
        result.update(source_sha=sha, source_dirty=dirty, files=hashes,
                      lock_sha256=digest(ROOT / 'uv.lock'), target=target,
                      deployment_files={p: digest(ROOT / p) for p in
                          ('compose.ops.yaml', 'db/bootstrap/00_identity.sql')})
        pinned = {}
        for key, ref in [('python', 'python:3.12-slim-bookworm'), ('uv', 'ghcr.io/astral-sh/uv:0.11.18')]:
            command(['docker', 'pull', ref], timeout=600)
            identity = inspect_image(ref)
            if not identity['repo_digests']:
                raise ValueError('Pulled base has no immutable repository digest')
            pinned[key] = identity
        mysql = inspect_image('mysql:8.4')
        result.update(base_images=pinned, mysql_image=mysql)
        context = OPS / 'builds' / ('context-' + uuid4().hex)
        materialize_context(ROOT, context, hashes)
        result['context_manifest_sha256'] = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        tag = 'eqaops-local:' + sha[:12] + '-' + target + '-' + uuid4().hex[:8]
        output = command(['docker', 'build', '--target', target, '-f', 'Dockerfile.ops',
                          '--build-arg', 'PYTHON_IMAGE=' + pinned['python']['repo_digests'][0],
                          '--build-arg', 'UV_IMAGE=' + pinned['uv']['repo_digests'][0],
                          '--build-arg', 'SOURCE_SHA=' + sha,
                          '--build-arg', 'SOURCE_DIRTY=' + str(dirty).lower(), '-t', tag, str(context)],
                         timeout=900, combined=True)
        image = inspect_image(tag)
        result.update(kind='eqaops-image', image_id=image['id'], image=image,
                      tag=tag, build_output=output)


def load_stack(name):
    path = stack_path(OPS, name)
    config = json.loads((path / 'stack.json').read_text(encoding='utf-8'))
    if config.get('kind') != 'eqaops-stack' or config.get('stack') != name:
        raise ValueError('Stack receipt identity mismatch')
    validate_port(config['http_port'])
    if digest(path / 'compose.ops.yaml') != config['compose_sha256']:
        raise ValueError('Stored compose snapshot changed')
    if digest(path / '00_identity.sql') != config['identity_sql_sha256']:
        raise ValueError('Stored initialization SQL changed')
    return path, config


def compose(path, config, args, image=None, timeout=300):
    env = {k: v for k, v in os.environ.items() if not k.startswith(('EQA_', 'COMPOSE_'))}
    env.update(EQA_OPS_STACK_DIR=path.as_posix(), EQA_OPS_HTTP_PORT=str(config['http_port']),
               EQA_OPS_APP_IMAGE=image or config['initial_image_id'],
               EQA_OPS_MYSQL_IMAGE=config['mysql_image']['id'])
    hidden = []
    for filename in ('runtime.env', 'mysql.env'):
        for line in (path / filename).read_text().splitlines():
            if 'PASSWORD=' in line:
                hidden.append(line.split('=', 1)[1])
    return command(['docker', 'compose', '--project-name', config['stack'], '--env-file',
                    str(path / 'compose.env'), '-f', str(path / 'compose.ops.yaml'), *args],
                   env=env, timeout=timeout, redact=hidden)


def check_container(info, name, port):
    labels = info['Config'].get('Labels') or {}
    if labels.get('com.docker.compose.project') != name:
        raise ValueError('Container belongs to a different project')
    service = labels.get('com.docker.compose.service')
    if service not in ('mysql', 'app', 'bootstrap', 'integration'):
        raise ValueError('Unknown service in project')
    bindings = info['HostConfig'].get('PortBindings') or {}
    if service == 'app':
        if bindings != {'8011/tcp': [{'HostIp': '127.0.0.1', 'HostPort': str(port)}]}:
            raise ValueError('App must publish exactly the loopback HTTP port')
        if info.get('State', {}).get('Status') == 'running' and info.get('NetworkSettings', {}).get('Ports') != bindings:
            raise ValueError('App loopback HTTP port is not actually published')
    elif bindings:
        raise ValueError('Database/tool must never publish a host port')
    for mount in info.get('Mounts', []):
        if mount['Type'] == 'volume' and mount.get('Name') != name + '_mysql_data':
            raise ValueError('Foreign volume attached')


def resources(path, config):
    ids = command(['docker', 'ps', '-aq', '--filter', 'label=com.docker.compose.project=' + config['stack']]).splitlines()
    output = []
    for cid in ids:
        info = json.loads(command(['docker', 'inspect', cid]))[0]
        check_container(info, config['stack'], config['http_port'])
        output.append({'id': cid, 'name': info['Name'], 'image': info['Image'],
                       'state': info['State']['Status'], 'ports': info['HostConfig'].get('PortBindings'),
                       'actual_ports': info['NetworkSettings'].get('Ports'),
                       'mounts': [{'type': m['Type'], 'name': m.get('Name'), 'destination': m['Destination']}
                                  for m in info.get('Mounts', [])]})
    return output


def http(config, endpoint, method='GET'):
    request = Request(f"http://127.0.0.1:{config['http_port']}{endpoint}", method=method,
                      data=b'{}' if method == 'POST' else None,
                      headers={'Content-Type': 'application/json'})
    try:
        with urlopen(request, timeout=3) as response:
            return response.status, json.load(response)
    except HTTPError as error:
        return error.code, json.load(error)


def wait_ready(config, timeout=90):
    deadline = time.monotonic() + timeout
    latest = None
    while time.monotonic() < deadline:
        try:
            latest = http(config, '/health/ready')
            if latest[0] == 200 and latest[1].get('ready'):
                return latest[1]
        except (URLError, TimeoutError, ConnectionError):
            pass
        time.sleep(1)
    raise RuntimeError('Readiness deadline exceeded; last response=' + str(latest))


def drain(config, timeout=70):
    status, response = http(config, '/ops/drain', 'POST')
    if status != 200:
        raise RuntimeError('Drain refused')
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        status, metrics = http(config, '/ops/metrics')
        if status == 200 and metrics.get('active_jobs') == 0:
            return {'drain': response, 'metrics': metrics}
        time.sleep(.25)
    raise RuntimeError('Drain deadline exceeded; no forced stop/replacement performed')


def deploy(name, port, image_receipt):
    path = stack_path(OPS, name)
    validate_port(port)
    image = json.loads(Path(image_receipt).read_text(encoding='utf-8'))
    image_id = validate_image(image)
    if image.get('status') != 'passed':
        raise ValueError('Image build did not pass')
    for file, expected in image['deployment_files'].items():
        if digest(ROOT / file) != expected:
            raise ValueError('Deployment files changed since image build')
    for kind in ('container', 'network', 'volume'):
        existing = command(['docker', kind, 'ls', '-aq' if kind == 'container' else '-q', '--filter', 'label=com.docker.compose.project=' + name])
        if existing:
            raise ValueError('Project resources already exist; refusing adoption')
    for resource in (name + '_mysql_data', name + '_private', name + '_ingress'):
        listing = command(['docker', 'volume' if resource.endswith('_data') else 'network', 'ls', '--format', '{{.Name}}'])
        if resource in listing.splitlines():
            raise ValueError('Resource name already exists')
    with socket.socket() as sock:
        sock.bind(('127.0.0.1', port))
    path.mkdir(parents=True, exist_ok=False)
    with operation(path / 'receipts', 'deploy') as result:
        credentials(path)
        for folder in ('init', 'state'):
            (path / folder).mkdir()
        (path / 'compose.env').write_text('', encoding='utf-8')
        shutil.copyfile(ROOT / 'compose.ops.yaml', path / 'compose.ops.yaml')
        shutil.copyfile(ROOT / 'db/bootstrap/00_identity.sql', path / '00_identity.sql')
        config = {'kind': 'eqaops-stack', 'stack': name, 'http_port': port,
                  'initial_image_id': image_id, 'mysql_image': image['mysql_image'],
                  'build_receipt': str(Path(image_receipt).resolve()),
                  'source_sha': image['source_sha'], 'source_dirty': image['source_dirty'],
                  'lock_sha256': image['lock_sha256'],
                  'compose_sha256': digest(path / 'compose.ops.yaml'),
                  'identity_sql_sha256': digest(path / '00_identity.sql')}
        write_json(path / 'stack.json', config)
        result['configuration'] = config
        result['mysql_start'] = compose(path, config, ['up', '-d', '--wait', '--wait-timeout', '180', 'mysql'])
        result['bootstrap'] = compose(path, config, ['run', '--rm', '--no-deps', 'bootstrap'])
        result['fixture'] = json.loads((path / 'init/dataset-manifest.json').read_text())
        result['oracle'] = json.loads(compose(path, config, ['run', '--rm', '--no-deps', 'integration']))
        if result['oracle']['installed_lock_sha256'] != config['lock_sha256']:
            raise ValueError('Installed dependency lock differs from build receipt')
        result['app_start'] = compose(path, config, ['up', '-d', '--no-deps', 'app'])
        result['resources'] = resources(path, config)
        result['readiness'] = wait_ready(config)
        result['meta'] = http(config, '/ops/meta')[1]
        result['resources'] = resources(path, config)


def container_oracle():
    # Independent Python arithmetic plus the original MySQL/compiler hand oracle.
    from decimal import Decimal
    from scripts.bootstrap_data import synthetic_data
    from tests.oracle import calculate
    from tests.test_engine import answer
    from enterprise_query.executor import Executor
    data = json.loads((ROOT / 'data/synthetic/base.json').read_text())
    if data != synthetic_data():
        raise ValueError('Published base fixture differs from bootstrap generator')
    gold = calculate(data)
    executor = Executor()
    actual = answer(executor, ['gmv', 'delivered_order_count', 'aov'])
    values = [f.value for f in actual.facts]
    assert values == [gold['gmv'], gold['order_count'], gold['aov']] == [Decimal('242'), 3, Decimal('242') / 3]
    assert answer(executor, ['payment_value']).facts[0].value == gold['payment'] == Decimal('459')
    assert answer(executor, ['average_review_score']).facts[0].value == gold['review_average'] == Decimal('4')
    assert answer(executor, ['late_rate']).facts[0].value == gold['late_rate'] == Decimal('.5')
    assert answer(executor, ['repeat_customer_rate']).facts[0].value == gold['repeat_rate'] == Decimal('.5')
    print(json.dumps({'status': 'passed', 'identity': executor.check_identity(),
                      'fixture_file_sha256': digest(ROOT / 'data/synthetic/base.json'),
                      'installed_lock_sha256': digest(ROOT / 'uv.lock'),
                      'independent_oracle': {k: str(v) for k, v in gold.items()}}))


def manage(name, action, image_receipt=None):
    path, config = load_stack(name)
    with operation(path / 'receipts', action) as result:
        result['before'] = resources(path, config)
        if action == 'verify':
            result['oracle'] = json.loads(compose(path, config, ['run', '--rm', '--no-deps', 'integration']))
            result['integration_output'] = compose(path, config, ['run', '--rm', '--no-deps', 'integration',
                'python', '-m', 'pytest', '-m', 'integration', '-q', '-rs', '-p', 'no:cacheprovider'], timeout=300)
        elif action in ('stop', 'remove'):
            apps = [r for r in result['before'] if r['name'].endswith('-app-1') and r['state'] == 'running']
            if apps:
                result['drain'] = drain(config)
            result['stop'] = compose(path, config, ['stop', '-t', '75'])
            if action == 'remove':
                # Evidence and DB volume deliberately retained. No --volumes or image removal.
                result['remove'] = compose(path, config, ['down'])
        elif action in ('update', 'rollback'):
            new = json.loads(Path(image_receipt).read_text(encoding='utf-8'))
            image = validate_image(new)
            if new.get('status') != 'passed' or inspect_image(image)['labels'].get('eqaops.kind') != 'eqaops-image':
                raise ValueError('Not a successful lab image')
            result['requested_image'] = image
            result['drain'] = drain(config)
            result['replace'] = compose(path, config, ['up', '-d', '--no-deps', '--force-recreate', 'app'], image=image)
            result['after'] = resources(path, config)
            result['readiness'] = wait_ready(config, timeout=15 if new.get('target') == 'unready' else 90)
        if action not in ('stop', 'remove'):
            result['ready'] = http(config, '/health/ready')
            result['meta'] = http(config, '/ops/meta')
        result['after'] = resources(path, config)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='action', required=True)
    build_parser = sub.add_parser('build')
    build_parser.add_argument('--target', choices=('runtime', 'unready'), default='runtime')
    create = sub.add_parser('deploy')
    create.add_argument('stack')
    create.add_argument('--port', type=int, required=True)
    create.add_argument('--image-receipt', required=True)
    for action in ('status', 'verify', 'stop', 'remove', 'update', 'rollback'):
        entry = sub.add_parser(action)
        entry.add_argument('stack')
        if action in ('update', 'rollback'):
            entry.add_argument('--image-receipt', required=True)
    sub.add_parser('container-oracle')
    args = parser.parse_args()
    if args.action == 'container-oracle':
        container_oracle()
    elif args.action == 'build':
        build(args.target)
    elif args.action == 'deploy':
        deploy(args.stack, args.port, args.image_receipt)
    else:
        manage(args.stack, args.action, getattr(args, 'image_receipt', None))


if __name__ == '__main__':
    main()
