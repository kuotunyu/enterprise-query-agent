import os
import threading
import time
import pytest

HEAVY = 'SELECT SUM(a.price*b.price*c.price*d.price*e.price*f.price*g.price*h.price*i.price*j.price*k.price) AS total FROM order_items a CROSS JOIN order_items b CROSS JOIN order_items c CROSS JOIN order_items d CROSS JOIN order_items e CROSS JOIN order_items f CROSS JOIN order_items g CROSS JOIN order_items h CROSS JOIN order_items i CROSS JOIN order_items j CROSS JOIN order_items k'

@pytest.fixture
def executor():
    if os.getenv('EQA_INTEGRATION')!='1': pytest.skip('isolated MySQL service required')
    from enterprise_query.executor import Executor
    return Executor()


@pytest.mark.integration
def test_mysql_statement_limit_without_client_timer(executor):
    import pymysql
    with executor.connect() as conn:
        cur=conn.cursor()
        cur.execute('SET SESSION MAX_EXECUTION_TIME = 100')
        start=time.monotonic()
        with pytest.raises(pymysql.err.OperationalError) as failure:
            cur.execute(HEAVY)
        assert failure.value.args[0]==3024
        assert time.monotonic()-start<2
        cur.execute('SELECT 1 AS alive')
        assert cur.fetchone()['alive']==1


@pytest.mark.integration
def test_default_five_second_deadline_and_no_residual(executor):
    from enterprise_query.executor import QueryTimeout
    start=time.monotonic()
    with pytest.raises(QueryTimeout): executor.execute(HEAVY)
    assert 4 <= time.monotonic()-start < 7
    with executor.connect() as conn:
        cur=conn.cursor(); cur.execute('SHOW PROCESSLIST')
        assert not any('SUM(a.price' in str(r.get('Info') or '') for r in cur.fetchall())


@pytest.mark.integration
def test_explicit_cancel_stops_real_query(executor):
    errors=[]
    def execute():
        try: executor.execute(HEAVY)
        except Exception as e: errors.append(type(e).__name__)
    worker=threading.Thread(target=execute)
    worker.start()
    deadline=time.monotonic()+2
    while not executor._active and time.monotonic()<deadline: time.sleep(.01)
    executor.cancel()
    worker.join(2)
    assert not worker.is_alive() and errors==['QueryTimeout']
    with executor.connect() as conn:
        cur=conn.cursor();cur.execute('SHOW PROCESSLIST')
        assert not any('SUM(a.price' in str(r.get('Info') or '') for r in cur.fetchall())


@pytest.mark.integration
def test_data_version_mismatch_rejected_before_business_query(executor):
    from enterprise_query.executor import IdentityError
    executor.dataset_id='unrecorded-version'
    with pytest.raises(IdentityError): executor.execute('SELECT COUNT(*) FROM orders')


@pytest.mark.integration
def test_legal_statement_terminator(executor):
    assert executor.execute('SELECT 1 AS n;').rows == [{'n':1}]
    assert executor.execute('WITH t AS (SELECT order_id FROM orders) SELECT COUNT(*) AS n FROM t').rows == [{'n':5}]


def test_secret_not_in_executor_representation():
    from enterprise_query.executor import Executor
    assert 'never-expose-this' not in repr(Executor(password='never-expose-this'))


@pytest.mark.integration
def test_cancel_during_identity_does_not_start_business_sql(executor):
    entered=threading.Event(); release=threading.Event(); outcomes=[]
    original=executor._identity
    def identity(conn):
        meta=original(conn)
        entered.set()
        assert release.wait(3)
        return meta
    executor._identity=identity
    def work():
        try: outcomes.append(executor.execute('SELECT COUNT(*) AS n FROM orders'))
        except Exception as exc: outcomes.append(type(exc).__name__)
    worker=threading.Thread(target=work);worker.start()
    assert entered.wait(3)
    executor.cancel()
    release.set();worker.join(3)
    assert outcomes==['QueryTimeout'], 'cancelled preflight must not dispatch business SQL'
    assert not executor._active and not executor._pending


@pytest.mark.integration
def test_cancel_while_connection_is_pending_skips_identity(executor):
    entered=threading.Event(); release=threading.Event(); outcomes=[]; identities=[]
    original_connect=executor.connect
    original_identity=executor._identity
    def connect(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original_connect(*args, **kwargs)
    def identity(conn):
        identities.append(conn.thread_id())
        return original_identity(conn)
    executor.connect=connect
    executor._identity=identity
    def work():
        try: outcomes.append(executor.execute('SELECT COUNT(*) AS n FROM orders', task_id='pending'))
        except Exception as exc: outcomes.append(type(exc).__name__)
    worker=threading.Thread(target=work); worker.start()
    try:
        assert entered.wait(3)
        executor.cancel('pending')
    finally:
        release.set(); worker.join(3)
    assert not worker.is_alive()
    assert outcomes==['QueryTimeout']
    assert identities==[]
    assert not executor._active and not executor._pending


@pytest.mark.integration
def test_cancel_at_dispatch_boundary_closes_idle_runtime_connection(executor):
    entered=threading.Event(); release=threading.Event(); outcomes=[]
    original_connect=executor.connect
    def connect(*args, **kwargs):
        conn=original_connect(*args, **kwargs)
        original_cursor=conn.cursor
        def cursor(*args, **kwargs):
            cur=original_cursor(*args, **kwargs)
            original_execute=cur.execute
            def execute(sql, parameters=None):
                if sql.startswith('SELECT * FROM ('):
                    entered.set()
                    assert release.wait(3)
                return original_execute(sql, parameters)
            cur.execute=execute
            return cur
        conn.cursor=cursor
        return conn
    executor.connect=connect
    def work():
        try: outcomes.append(executor.execute(HEAVY))
        except Exception as exc: outcomes.append(type(exc).__name__)
    worker=threading.Thread(target=work); worker.start()
    try:
        assert entered.wait(3)
        executor.cancel()
    finally:
        release.set(); worker.join(3)
    assert not worker.is_alive() and outcomes==['QueryTimeout']
    assert not executor._active and not executor._pending
    with original_connect() as conn:
        with conn.cursor() as cur:
            cur.execute('SHOW PROCESSLIST')
            assert not any('SUM(a.price' in str(row.get('Info') or '') for row in cur.fetchall())
