"""Local cost journal. Exclusive writers, atomic replacement, fail closed.

Reservations cover the entire documented model context at long-context cache
write rates, plus maximum output. This avoids treating a tokenizer estimate as
a proven upper bound. Valid usage settles to a conservative ceiling (all input
at cache-write rate); it is not represented as an exact invoice cost.
"""
from contextlib import contextmanager
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
import uuid
from .model_profile import MODEL_PROFILE


class BudgetError(RuntimeError):
    pass


RESERVATION = Decimal('0.5323728')  # 1,050,000 * .50/M + 4,096 * 1.80/M


def encoded(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode('utf-8')


class CostLedger:
    def __init__(self, path, stage):
        self.path = Path(path)
        if stage not in ('development', 'research'):
            raise BudgetError('Explicit cost stage required')
        self.stage = stage
        self._read()

    @classmethod
    def initialize(cls, path, limits):
        """Call only after owner approval; never overwrite an existing ledger."""
        path = Path(path)
        if set(limits) != {'development', 'research'}:
            raise BudgetError('Both independent stage limits are required')
        limits = {key: str(Decimal(value)) for key, value in limits.items()}
        if any(not Decimal(v).is_finite() or Decimal(v) <= 0 for v in limits.values()):
            raise BudgetError('Invalid budget limits')
        data = {'version': 1, 'profile': MODEL_PROFILE, 'limits': limits, 'calls': {}}
        wrapper = {'data': data, 'sha256': hashlib.sha256(encoded(data)).hexdigest()}
        try:
            with path.open('xb') as stream:
                stream.write(encoded(wrapper)); stream.flush(); os.fsync(stream.fileno())
        except FileExistsError as exc:
            raise BudgetError('Existing budget ledger cannot be replaced') from exc

    def _read(self):
        try:
            wrapper = json.loads(self.path.read_bytes())
            data = wrapper['data']
            if hashlib.sha256(encoded(data)).hexdigest() != wrapper['sha256']:
                raise ValueError('checksum')
            if data['version'] != 1 or data['profile'] != MODEL_PROFILE:
                raise ValueError('frozen profile mismatch')
            return data
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise BudgetError('Missing, corrupt or incompatible budget ledger') from exc

    @contextmanager
    def _write(self):
        lock = self.path.with_suffix('.lock')
        try:
            descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError as exc:
            raise BudgetError('Budget ledger locked; no request sent') from exc
        try:
            os.close(descriptor)
            data = self._read()
            yield data
            wrapper = {'data': data, 'sha256': hashlib.sha256(encoded(data)).hexdigest()}
            pending = self.path.with_suffix('.pending')
            with pending.open('wb') as stream:
                stream.write(encoded(wrapper)); stream.flush(); os.fsync(stream.fileno())
            os.replace(pending, self.path)
        finally:
            lock.unlink()

    def _total(self, data):
        return sum((Decimal(c['charged_upper_usd']) for c in data['calls'].values()
                    if c['stage'] == self.stage), Decimal(0))

    def total(self):
        return self._total(self._read())

    def records(self, request_ids):
        return {ticket: call for ticket, call in self._read()['calls'].items()
                if call['stage'] == self.stage and call['request_id'] in request_ids}

    def reserve(self, request_id):
        ticket = uuid.uuid4().hex
        with self._write() as data:
            if self._total(data) + RESERVATION > Decimal(data['limits'][self.stage]):
                raise BudgetError('Stage budget exhausted; no request sent')
            data['calls'][ticket] = {'stage': self.stage, 'request_id': request_id,
                'state': 'reserved', 'reserved_usd': str(RESERVATION),
                'charged_upper_usd': str(RESERVATION), 'usage': None}
        return ticket

    def settle(self, ticket, usage):
        upper = None
        if isinstance(usage, dict):
            inp, out = usage.get('input_tokens'), usage.get('output_tokens')
            if type(inp) is int and type(out) is int and 0 <= inp <= 1050000 and 0 <= out <= 4096:
                input_rate, output_rate = ('.50', '1.80') if inp > 272000 else ('.25', '1.20')
                upper = (inp * Decimal(input_rate) + out * Decimal(output_rate)) / 1000000
        with self._write() as data:
            call = data['calls'][ticket]
            if call['stage'] != self.stage or call['state'] != 'reserved':
                raise BudgetError('Reservation already settled or belongs to another stage')
            call['state'] = 'usage_upper_bound' if upper is not None else 'unknown'
            call['usage'] = usage
            if upper is not None:
                call['charged_upper_usd'] = str(upper)
        return upper


def budgeted_parse(provider, request_id, inputs, schema, timeout):
    """Shared paid boundary for the workbench and all comparison methods."""
    if str(provider.client.base_url) != 'https://api.openai.com/v1/':
        raise BudgetError('Only the standard OpenAI endpoint has a frozen price profile')
    # Text-only local policy; include the schema in the size check. Full-context
    # reservation above covers tokenization and any server framing overhead.
    if len(encoded({'input': inputs, 'schema': schema.model_json_schema()})) > 64000:
        raise BudgetError('Request exceeds local text/schema size limit')
    provider.last_usage = {'tokens': None, 'usd': None, 'provider': 'openai',
        'model': MODEL_PROFILE['model'], 'cost_basis': 'unknown; full reservation retained'}
    ticket = provider.ledger.reserve(request_id)
    provider.last_usage.update(ledger_ticket=ticket, reserved_usd=str(RESERVATION))
    try:
        raw = provider.client.with_options(max_retries=0).responses.with_raw_response.parse(
            model=MODEL_PROFILE['model'], reasoning=MODEL_PROFILE['reasoning'],
            max_output_tokens=4096, service_tier='default', input=inputs,
            text_format=schema, timeout=min(55, timeout))
    except Exception as exc:
        provider.last_usage['error_type'] = type(exc).__name__
        provider.last_usage['http_status'] = getattr(exc, 'status_code', None)
        if getattr(provider, 'trace', None):
            provider.trace(request_id, {'error_type': type(exc).__name__,
                'http_status': getattr(exc, 'status_code', None), 'body': getattr(exc, 'body', None)})
        raise
    if getattr(provider, 'trace', None):
        provider.trace(request_id, {'http_status': raw.http_response.status_code,
            'body': raw.http_response.json()})
    usage = raw.http_response.json().get('usage')
    upper = provider.ledger.settle(ticket, usage)
    provider.last_usage.update(tokens=usage, charged_upper_usd=str(upper) if upper is not None else str(RESERVATION),
        cost_basis='usage-based conservative upper bound' if upper is not None else 'unknown; full reservation retained')
    return raw.parse()
