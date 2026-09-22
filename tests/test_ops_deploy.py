"""Deployment boundary regressions; no Docker daemon or credentials required."""
import importlib.util
import json
from pathlib import Path
import sys

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'ops_deploy.py'


def module():
    assert SCRIPT.exists(), 'isolated deployment CLI has not been implemented'
    spec = importlib.util.spec_from_file_location('ops_deploy', SCRIPT)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('name', ['eqa_v1', 'mvtec', 'eqaops', 'eqaops-../x', 'eqaops/x', 'eqaops;down', 'eqaops-A'])
def test_foreign_or_path_like_stack_is_rejected(name):
    with pytest.raises(ValueError):
        module().validate_stack(name)


def test_stack_directory_cannot_escape_through_symlink(tmp_path):
    deployment = module()
    outside = tmp_path / 'outside'
    outside.mkdir()
    root = tmp_path / 'ops'
    root.mkdir()
    try:
        (root / 'eqaops-link').symlink_to(outside, target_is_directory=True)
    except OSError:
        pytest.skip('symlink creation unavailable')
    with pytest.raises(ValueError):
        deployment.stack_path(root, 'eqaops-link')


@pytest.mark.parametrize('port', [0, 3306, 3307, 65536, True])
def test_invalid_or_database_http_port_rejected(port):
    with pytest.raises(ValueError):
        module().validate_port(port)


def test_receipt_creation_is_exclusive_and_does_not_change_failure(tmp_path):
    deployment = module()
    path = tmp_path / 'attempt.json'
    deployment.write_json(path, {'status': 'failed'})
    with pytest.raises(FileExistsError):
        deployment.write_json(path, {'status': 'passed'})
    assert json.loads(path.read_text()) == {'status': 'failed'}


def test_generated_credentials_are_separate_and_never_replaced(tmp_path):
    deployment = module()
    deployment.credentials(tmp_path)
    runtime = (tmp_path / 'runtime.env').read_text()
    bootstrap = (tmp_path / 'bootstrap.env').read_text()
    root = (tmp_path / 'mysql.env').read_text()
    assert 'EQA_BOOTSTRAP_PASSWORD' not in runtime
    assert 'MYSQL_ROOT_PASSWORD' not in runtime
    reader = dict(line.split('=', 1) for line in runtime.splitlines())['EQA_DB_PASSWORD']
    admin = dict(line.split('=', 1) for line in root.splitlines())['MYSQL_ROOT_PASSWORD']
    assert reader != admin and len(reader) >= 32
    assert f'EQA_BOOTSTRAP_PASSWORD={admin}' in bootstrap
    assert f'EQA_DB_PASSWORD={reader}' in bootstrap
    with pytest.raises(FileExistsError):
        deployment.credentials(tmp_path)
    assert (tmp_path / 'runtime.env').read_text() == runtime


def test_build_allowlist_excludes_private_and_unrelated_evidence():
    deployment = module()
    paths = ['src/enterprise_query/ops.py', 'uv.lock', 'data/synthetic/base.json',
             '.local/ops/secret.env', '.env', '.git/config', 'data/raw/orders.csv',
             'reports/formal-luna.json', 'src/__pycache__/oops.pyc']
    assert deployment.context_files(paths) == paths[:3]


def test_image_receipt_rejects_tags_and_non_lab_identity():
    deployment = module()
    with pytest.raises(ValueError):
        deployment.validate_image({'image_id': 'latest', 'kind': 'eqaops-image'})
    with pytest.raises(ValueError):
        deployment.validate_image({'image_id': 'sha256:' + 'a' * 64, 'kind': 'foreign'})


def test_resource_audit_refuses_db_host_mapping_and_foreign_project():
    deployment = module()
    safe = {'Name': '/eqaops-test-mysql-1', 'Config': {'Labels': {
        'com.docker.compose.project': 'eqaops-test', 'com.docker.compose.service': 'mysql'}},
        'HostConfig': {'PortBindings': {}}, 'Mounts': []}
    deployment.check_container(safe, 'eqaops-test', 18011)
    safe['HostConfig']['PortBindings'] = {'3307/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '3307'}]}
    with pytest.raises(ValueError):
        deployment.check_container(safe, 'eqaops-test', 18011)
    safe['HostConfig']['PortBindings'] = {}
    with pytest.raises(ValueError):
        deployment.check_container(safe, 'eqaops-other', 18011)


def test_failed_operation_receipt_preserves_outcome_and_duration(tmp_path):
    deployment = module()
    with pytest.raises(ValueError, match='controlled failure'):
        with deployment.operation(tmp_path, 'check') as receipt:
            receipt['run_id'] = 'first'
            raise ValueError('controlled failure')
    record = json.loads(next(tmp_path.glob('*.json')).read_text())
    assert record['status'] == 'failed' and record['run_id'] == 'first'
    assert record['elapsed_seconds'] >= 0 and 'controlled failure' in record['error']


def test_command_keeps_json_stdout_separate_and_redacts_failed_output():
    deployment = module()
    output = deployment.command([sys.executable, '-c', 'import sys; print("{}"); print("warning", file=sys.stderr)'])
    assert json.loads(output) == {}
    with pytest.raises(RuntimeError) as error:
        deployment.command([sys.executable, '-c', 'print("synthetic-secret"); raise SystemExit(2)'],
                           redact=['synthetic-secret'])
    assert 'synthetic-secret' not in str(error.value)


def test_compose_success_stderr_capture_is_opt_in_and_redacted(monkeypatch, tmp_path):
    deployment = module()
    (tmp_path / 'runtime.env').write_text('EQA_DB_PASSWORD=synthetic-secret\n')
    (tmp_path / 'mysql.env').write_text('MYSQL_ROOT_PASSWORD=synthetic-secret\n')
    config = dict(stack='eqaops-test', http_port=18012, initial_image_id='fixed', mysql_image={'id': 'mysql'})
    original = deployment.command
    def substitute_child(args, **kwargs):
        return original([sys.executable, '-c',
            'import sys; print("{}"); print("Started synthetic-secret", file=sys.stderr)'], **kwargs)
    monkeypatch.setattr(deployment, 'command', substitute_child)
    assert json.loads(deployment.compose(tmp_path, config, ['up'])) == {}
    output = deployment.compose(tmp_path, config, ['up'], combined=True)
    assert 'Started [REDACTED]' in output
    assert 'synthetic-secret' not in output


def test_drain_timeout_never_reports_lingering_job_as_stopped(monkeypatch):
    deployment = module()
    responses = iter([(200, {'outstanding_jobs': 1}), (200, {'active_jobs': 1})])
    monkeypatch.setattr(deployment, 'http', lambda *args: next(responses))
    ticks = iter([0, 0, 2])
    monkeypatch.setattr(deployment.time, 'monotonic', lambda: next(ticks))
    monkeypatch.setattr(deployment.time, 'sleep', lambda _: None)
    with pytest.raises(RuntimeError, match='no forced stop'):
        deployment.drain({}, timeout=1)


def test_context_materialization_rejects_source_changed_after_snapshot(tmp_path):
    deployment = module()
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'uv.lock').write_text('accepted')
    hashes = {'uv.lock': deployment.digest(source / 'uv.lock')}
    (source / 'uv.lock').write_text('changed')
    with pytest.raises(ValueError, match='changed'):
        deployment.materialize_context(source, tmp_path / 'context', hashes)


def test_context_materialization_copies_only_snapshot_files(tmp_path):
    deployment = module()
    source = tmp_path / 'source'
    source.mkdir()
    (source / 'uv.lock').write_text('accepted')
    (source / 'secret.env').write_text('never-copy')
    hashes = {'uv.lock': deployment.digest(source / 'uv.lock')}
    target = tmp_path / 'context'
    deployment.materialize_context(source, target, hashes)
    assert sorted(p.name for p in target.iterdir()) == ['uv.lock']
    assert deployment.digest(target / 'uv.lock') == hashes['uv.lock']


def test_running_app_requires_actual_published_loopback_port():
    deployment = module()
    expected = {'8011/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '18011'}]}
    app = {'Config': {'Labels': {'com.docker.compose.project': 'eqaops-test',
                                'com.docker.compose.service': 'app'}},
           'HostConfig': {'PortBindings': expected}, 'Mounts': [],
           'State': {'Status': 'running'}, 'NetworkSettings': {'Ports': {'8011/tcp': []}}}
    with pytest.raises(ValueError, match='actually published'):
        deployment.check_container(app, 'eqaops-test', 18011)
    app['NetworkSettings']['Ports'] = expected
    deployment.check_container(app, 'eqaops-test', 18011)
