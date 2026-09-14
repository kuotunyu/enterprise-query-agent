from decimal import Decimal as D
from scripts.bootstrap_data import synthetic_data, derive_geolocation
from tests.oracle import calculate

def test_development_hand_calculations():
    facts = calculate(synthetic_data())
    assert facts['gmv'] == D('242')
    assert facts['order_count'] == 3
    assert facts['aov'] == D('242') / 3
    assert facts['merchandise'] == D('220')
    assert facts['category_gmv'] == {'A': D('154'), 'B': D('88')}
    assert facts['payment'] == D('459')
    assert facts['repeat_rate'] == D('0.5')
    assert facts['review_average'] == D('4')
    assert facts['review_valid'] == 2 and facts['review_missing'] == 1
    assert facts['late_rate'] == D('0.5')
    assert facts['late_eligible'] == 2 and facts['late_missing'] == 1
    assert facts['difference'] == D('132') and facts['growth'] == D('1.2')
    assert facts['category_difference'] == {'A': D('44'), 'B': D('88')}
    assert facts['category_aov'] == {'A': D('77'), 'B': D('44')}
    assert facts['sp_gmv'] == D('209')
    assert facts['bad_join_gmv'] != facts['gmv']

def test_variants_and_zip_cardinality():
    base = calculate(synthetic_data())
    for variant in ('split_payment', 'older_review'):
        actual = calculate(synthetic_data(variant))
        for key in ('gmv','review_average','payment','sp_gmv'):
            assert actual[key] == base[key]
    locations = derive_geolocation(synthetic_data()['geolocation'])
    assert len(locations) == 2
    assert locations[0]['n_points'] == 2

def test_fixture_ids_and_serialized_snapshots():
    import json, re
    from pathlib import Path
    for variant in ('base','split_payment','older_review'):
        data=synthetic_data(variant)
        assert json.loads(Path(f'data/synthetic/{variant}.json').read_text())==data
        for rows in data.values():
            for row in rows:
                for key,value in row.items():
                    if key.endswith('_id') and key not in ('order_item_id',):
                        assert re.fullmatch('[0-9a-f]{32}',value)

def test_csv_cleaning_and_manifest(tmp_path):
    import csv
    from scripts.bootstrap_data import TABLES, FILES, load_csv
    fixture=synthetic_data()
    for table in TABLES:
        rows=fixture[table]
        if table=='geolocation': rows=rows+[rows[0]]
        with (tmp_path/FILES[table]).open('w',newline='',encoding='utf-8') as f:
            columns=list(rows[0]); headers=[c.replace('_length','_lenght') for c in columns]
            writer=csv.writer(f); writer.writerow(headers)
            for row in rows: writer.writerow([row[c] for c in columns])
    data,manifest=load_csv(tmp_path)
    assert len(data['geolocation'])==3
    assert data['orders'][3]['order_delivered_customer_date'] is None
    assert data['customers'][0]['customer_zip_code_prefix']=='01000'
    assert data['products'][0]['product_name_length']=='1'
    geo=next(m for m in manifest if m['file']==FILES['geolocation'])
    assert geo['source_rows']==4 and geo['loaded_rows']==3
    assert len(geo['sha256'])==64

import os
import pytest

@pytest.fixture
def mysql():
    if os.getenv('EQA_INTEGRATION')!='1': pytest.skip('Set EQA_INTEGRATION=1 with isolated MySQL running')
    import pymysql
    conn=pymysql.connect(host=os.getenv('EQA_DB_HOST','127.0.0.1'),port=int(os.getenv('EQA_DB_PORT','3307')),user='eqa_reader',password=os.environ['EQA_DB_PASSWORD'],database='eqa_v1')
    yield conn
    conn.close()

@pytest.mark.integration
def test_real_db_hand_calculations(mysql):
    with mysql.cursor() as cur:
        scope="o.order_status='delivered' AND o.order_purchase_timestamp >= '2018-07-01' AND o.order_purchase_timestamp < '2018-08-01'"
        cur.execute(f'SELECT SUM(g.gmv),COUNT(*),SUM(g.gmv)/COUNT(*) FROM orders o JOIN v_order_gmv g USING(order_id) WHERE {scope}')
        gmv,count,aov=cur.fetchone()
        assert gmv==D('242') and count==3 and abs(aov-D('242')/3)<D('.000001')
        cur.execute(f'SELECT SUM(i.price) FROM orders o JOIN order_items i USING(order_id) WHERE {scope}')
        assert cur.fetchone()[0]==D('220')
        cur.execute(f'SELECT p.product_category_name,SUM(i.price+i.freight_value),COUNT(DISTINCT o.order_id) FROM orders o JOIN order_items i USING(order_id) JOIN products p USING(product_id) WHERE {scope} GROUP BY p.product_category_name')
        categories={cat:(value,n) for cat,value,n in cur.fetchall()}
        assert categories=={'A':(D('154'),2),'B':(D('88'),2)}
        assert {c:v/n for c,(v,n) in categories.items()}=={'A':D('77'),'B':D('44')}
        cur.execute(f'SELECT SUM(g.gmv) FROM orders o JOIN v_order_gmv g USING(order_id) LEFT JOIN order_payments p USING(order_id) LEFT JOIN v_order_review r USING(order_id) WHERE {scope}')
        assert cur.fetchone()[0]!=D('242')
        cur.execute("SELECT SUM(p.payment_value) FROM orders o JOIN order_payments p USING(order_id) WHERE o.order_purchase_timestamp >= '2018-07-01' AND o.order_purchase_timestamp < '2018-08-01'")
        assert cur.fetchone()[0]==D('459')
        cur.execute(f'SELECT c.customer_unique_id,COUNT(*) FROM orders o JOIN customers c USING(customer_id) WHERE {scope} GROUP BY c.customer_unique_id')
        people=cur.fetchall(); assert len(people)==2 and sum(n>=2 for _,n in people)==1
        cur.execute(f'SELECT AVG(r.review_score),COUNT(r.review_score),COUNT(*)-COUNT(r.review_score) FROM orders o LEFT JOIN v_order_review r USING(order_id) WHERE {scope}')
        assert cur.fetchone()==(D('4'),2,1)
        cur.execute(f'SELECT SUM(order_delivered_customer_date > order_estimated_delivery_date),COUNT(order_delivered_customer_date),SUM(order_delivered_customer_date IS NULL) FROM orders o WHERE {scope}')
        assert cur.fetchone()==(D('1'),2,D('1'))
        cur.execute("SELECT SUM(g.gmv) FROM orders o JOIN v_order_gmv g USING(order_id) WHERE o.order_status='delivered' AND o.order_purchase_timestamp >= '2018-06-01' AND o.order_purchase_timestamp < '2018-07-01'")
        previous=cur.fetchone()[0]; assert gmv-previous==D('132') and (gmv-previous)/previous==D('1.2')
        cur.execute(f"SELECT SUM(g.gmv) FROM orders o JOIN v_order_gmv g USING(order_id) JOIN customers c USING(customer_id) LEFT JOIN geolocation_zip z ON z.zip_code_prefix=c.customer_zip_code_prefix WHERE {scope} AND c.customer_state='SP'")
        assert cur.fetchone()[0]==D('209')

@pytest.mark.integration
def test_real_runtime_cannot_read_review_text(mysql):
    import pymysql
    with mysql.cursor() as cur:
        with pytest.raises(pymysql.MySQLError): cur.execute('SELECT review_comment_message FROM order_reviews')

def test_bootstrap_refuses_unmarked_or_foreign_destination():
    from scripts.bootstrap_data import verify_destination
    class Cursor:
        def __init__(self,identity,marker): self.rows=iter([identity,marker])
        def execute(self,_): pass
        def fetchone(self): return next(self.rows)
    for identity,marker in [(('olist','source',3306,'8.4.11'),('eqa_v1',)),(('eqa_v1','isolated',3306,'8.0.0'),('eqa_v1',)),(('eqa_v1','isolated',3306,'8.4.11'),('other_project',))]:
        with pytest.raises(RuntimeError): verify_destination(Cursor(identity,marker))
    assert verify_destination(Cursor(('eqa_v1','isolated',3306,'8.4.11'),('eqa_v1',)))['database']=='eqa_v1'
