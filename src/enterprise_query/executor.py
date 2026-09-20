"""SELECT-only connections, verified dataset identity and real server/client deadlines."""
import hashlib
import json
import os
import threading
import time
from uuid import uuid4
from dataclasses import dataclass, field
import pymysql
from enterprise_query.contracts import QueryResult
from enterprise_query.sql_policy import check_sql, PolicyError, SCHEMA


class QueryTimeout(TimeoutError):
    pass


class IdentityError(RuntimeError):
    pass


@dataclass
class Executor:
    host: str = field(default_factory=lambda: os.getenv('EQA_DB_HOST','127.0.0.1'))
    port: int = field(default_factory=lambda: int(os.getenv('EQA_DB_PORT','3307')))
    user: str = field(default_factory=lambda: os.getenv('EQA_DB_USER','eqa_reader'))
    password: str = field(default_factory=lambda: os.getenv('EQA_DB_PASSWORD',''), repr=False)
    database: str = 'eqa_v1'
    dataset_id: str = field(default_factory=lambda: os.getenv('EQA_DATASET_ID','synthetic-v1'))

    def __post_init__(self):
        self._active = {}
        self._pending = {}
        self._lock = threading.Lock()

    def connect(self, read_timeout=6):
        return pymysql.connect(host=self.host, port=self.port, user=self.user, password=self.password,
                               database=self.database, charset='utf8mb4', autocommit=True,
                               cursorclass=pymysql.cursors.DictCursor, connect_timeout=3,
                               read_timeout=read_timeout, write_timeout=3, local_infile=False, client_flag=0)

    def _identity(self, conn):
        with conn.cursor() as cur:
            cur.execute('SELECT DATABASE() AS db, CURRENT_USER() AS user, VERSION() AS version')
            who = cur.fetchone()
            if who['db'] != 'eqa_v1' or not who['user'].startswith('eqa_reader@') or not who['version'].startswith('8.4.'):
                raise IdentityError('requires eqa_v1 MySQL 8.4 and eqa_reader')
            cur.execute('SHOW GRANTS')
            grants = [next(iter(r.values())) for r in cur.fetchall()]
            # Explicit allow-list of SELECT table grants, and harmless USAGE. No roles/global SELECT.
            for grant in grants:
                if grant.startswith('GRANT USAGE ON *.* TO ') and ' WITH GRANT OPTION' not in grant:
                    continue
                if not grant.startswith('GRANT SELECT ON `eqa_v1`.') or ' WITH GRANT OPTION' in grant:
                    raise IdentityError('runtime privileges exceed SELECT table grants')
                target = grant.split(' ON ',1)[1].split(' TO ',1)[0].split('.',1)[1].strip('`')
                if target not in set(SCHEMA)|{'eqa_metadata'}:
                    raise IdentityError('runtime has unapproved table or wildcard privilege')
            cur.execute('SELECT key_name,value_text FROM eqa_metadata')
            meta = {r['key_name']:r['value_text'] for r in cur.fetchall()}
            expected = {'project_id':'eqa_v1','dataset_id':self.dataset_id,'schema_version':'schema-v1','etl_version':'etl-v1'}
            if any(meta.get(k)!=v for k,v in expected.items()):
                raise IdentityError('database or data version mismatch')
            return meta

    def check_identity(self):
        with self.connect() as conn:
            return self._identity(conn)

    def cancel(self, task_id=None):
        with self._lock:
            for event, token in self._pending.items():
                if task_id is None or token == task_id:
                    event.set()
            connections = [(cid, event) for cid, (token, event) in self._active.items()
                           if task_id is None or token == task_id]
        for cid, event in connections:
            # Kill the connection, including an idle preflight connection. KILL QUERY
            # would leave an idle connection able to dispatch SQL after cancellation.
            try:
                with self.connect() as conn:
                    with self._lock:
                        if cid in self._active and self._active[cid][1] is event:
                            with conn.cursor() as cur:
                                cur.execute('KILL CONNECTION %s', (cid,))
                                # MySQL acknowledges KILL before the target thread
                                # finishes cleanup. Keep terminal delivery behind a
                                # bounded check of this executor's own connection.
                                stopped_by = time.monotonic()+1
                                while time.monotonic() < stopped_by:
                                    cur.execute('SHOW PROCESSLIST')
                                    if not any(row['Id'] == cid for row in cur.fetchall()):
                                        break
                                    time.sleep(.01)
            except pymysql.MySQLError:
                pass

    def execute(self, sql, parameters=(), timeout_ms=5000, task_id=None):
        check_sql(sql.replace('%%','%'))
        timeout_ms = min(5000, max(1,int(timeout_ms)))
        started = time.monotonic()
        conn = None
        cid = None
        canceled = threading.Event()
        token = task_id if task_id is not None else str(uuid4())
        with self._lock:
            # Register before connecting: cancellation must survive a pending connect.
            self._pending[canceled] = token
        def deadline():
            self.cancel(task_id=token)
        def check_canceled():
            if canceled.is_set():
                raise QueryTimeout('SQL deadline exceeded or query canceled')
        timer = threading.Timer(timeout_ms/1000, deadline)
        try:
            timer.start()
            check_canceled()
            conn = self.connect(read_timeout=timeout_ms/1000+1)
            cid = conn.thread_id()
            with self._lock:
                self._active[cid] = (token, canceled)
            check_canceled()
            self._identity(conn)
            check_canceled()
            with conn.cursor() as cur:
                cur.execute('SET SESSION MAX_EXECUTION_TIME = %s', (timeout_ms,))
                check_canceled()
                # Server-side outer cap protects transfer size and reports truncation via 201st row.
                statement = sql.rstrip().removesuffix(';')
                cur.execute('SELECT * FROM ('+statement+'\n) AS eqa_limited LIMIT 201', tuple(parameters))
                rows = cur.fetchall()
                descriptions = cur.description
            check_canceled()
            truncated = len(rows)>200
            rows = rows[:200]
            canonical = json.dumps(rows, default=str, ensure_ascii=False, sort_keys=True, separators=(',',':'))
            return QueryResult(query_id=str(uuid4()), sql=sql, parameters=list(parameters),
                               columns=[d[0] for d in descriptions], column_types=[str(d[1]) for d in descriptions],
                               rows=rows,row_count=len(rows),truncated=truncated,
                               elapsed_ms=(time.monotonic()-started)*1000,dataset_id=self.dataset_id,
                               result_hash=hashlib.sha256(canonical.encode()).hexdigest())
        except pymysql.MySQLError as exc:
            if canceled.is_set() or exc.args[0] in (3024,1317,2013):
                self.cancel(token)
                raise QueryTimeout('SQL deadline exceeded; query canceled') from exc
            raise
        finally:
            timer.cancel()
            try:
                with self._lock:
                    self._active.pop(cid,None)
                    self._pending.pop(canceled,None)
                if conn is not None:
                    conn.close()
            finally:
                # cancel() only prevents a future callback; an already-running
                # deadline may still hold a separate cancellation connection.
                # Join outside _lock, which that callback also needs. Completion
                # of execute includes its child work, even if close raises.
                if timer.ident is not None:
                    timer.join()
