import pytest
from scripts.bootstrap_data import verify_dataset_replacement


@pytest.mark.parametrize('existing,new,allowed', [
    ('synthetic-v1','synthetic-v1-split_payment',True),
    ('synthetic-v1','olist-local-one',True),
    ('olist-local-one','olist-local-one',True),
    ('olist-local-one','synthetic-v1',False),
    ('olist-local-one','olist-local-two',False),
])
def test_snapshot_replacement_guard(existing,new,allowed):
    class Cursor:
        def execute(self,sql): pass
        def fetchone(self): return (existing,)
    if allowed:
        verify_dataset_replacement(Cursor(),new)
    else:
        with pytest.raises(RuntimeError,match='existing real dataset'):
            verify_dataset_replacement(Cursor(),new)


def test_failed_real_import_retains_snapshot_protection():
    import sqlite3
    with sqlite3.connect(':memory:') as conn:
        conn.execute('CREATE TABLE eqa_metadata (key_name TEXT PRIMARY KEY,value_text TEXT)')
        conn.executemany('INSERT INTO eqa_metadata VALUES (?,?)',[
            ('dataset_id','loading'),('protected_dataset_id','olist-local-original')])
        with pytest.raises(RuntimeError,match='existing real dataset'):
            verify_dataset_replacement(conn.cursor(),'synthetic-v1')
        with pytest.raises(RuntimeError,match='existing real dataset'):
            verify_dataset_replacement(conn.cursor(),'olist-local-different')
        verify_dataset_replacement(conn.cursor(),'olist-local-original')


def test_independent_csv_oracle_matches_development_arithmetic(tmp_path):
    import csv
    from decimal import Decimal as D
    from scripts.bootstrap_data import synthetic_data, FILES
    from scripts.validate_real_data import independent_oracle
    for table,rows in synthetic_data().items():
        with (tmp_path/FILES[table]).open('w',encoding='utf-8',newline='') as handle:
            writer=csv.DictWriter(handle,fieldnames=list(rows[0]))
            writer.writeheader(); writer.writerows(rows)
    result=independent_oracle(tmp_path)
    assert result['gmv']==D('242') and result['merchandise_value']==D('220')
    assert result['payment_value']==D('459') and result['aov']==D('242')/3
    assert result['average_review_score']==D('4') and result['review_missing']==1
    assert result['repeat_customer_rate']==D('.5') and result['late_rate']==D('.5')
    assert result['category_gmv']=={'A':D('154'),'B':D('88')}
    assert result['previous_gmv']==D('110') and result['sp_gmv']==D('209')
