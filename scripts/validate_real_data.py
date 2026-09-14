"""Finite public-development data check; no provider calls or evaluation tasks."""
import argparse
from collections import Counter, defaultdict
import csv
from decimal import Decimal as D
import json
from pathlib import Path

from enterprise_query.compiler import compile_plan
from enterprise_query.contracts import QueryPlan
from enterprise_query.executor import Executor
from enterprise_query.facts import build_answer
from scripts.bootstrap_data import FILES, load_csv


def independent_oracle(directory):
    # Read source CSV independently, without ETL or compiler-derived expressions.
    def read(table):
        with (directory/FILES[table]).open(encoding='utf-8-sig',newline='') as f:
            return list(csv.DictReader(f))
    orders={r['order_id']:r for r in read('orders')}
    customers={r['customer_id']:r for r in read('customers')}
    products={r['product_id']:r for r in read('products')}
    translations={r['product_category_name']:r['product_category_name_english'] for r in read('product_category_name_translation')}
    july={k:r for k,r in orders.items() if '2018-07-01'<=r['order_purchase_timestamp']<'2018-08-01' and r['order_status']=='delivered'}
    june={k:r for k,r in orders.items() if '2018-06-01'<=r['order_purchase_timestamp']<'2018-07-01' and r['order_status']=='delivered'}
    totals=defaultdict(lambda:D(0)); categories=defaultdict(lambda:D(0)); merchandise=D(0)
    for r in read('order_items'):
        value=D(r['price'])+D(r['freight_value']); totals[r['order_id']]+=value
        if r['order_id'] in july:
            merchandise+=D(r['price'])
            category=products[r['product_id']]['product_category_name']
            categories[translations.get(category,category or 'unknown')]+=value
    latest={}
    for r in read('order_reviews'):
        oid=r['order_id']; rank=lambda x:(x['review_creation_date'],x['review_answer_timestamp'],x['review_id'])
        if oid not in latest or rank(r)>rank(latest[oid]): latest[oid]=r
    scores=[D(latest[k]['review_score']) for k in july if k in latest]
    eligible=[r for r in july.values() if r['order_delivered_customer_date'] and r['order_estimated_delivery_date']]
    late=sum(r['order_delivered_customer_date']>r['order_estimated_delivery_date'] for r in eligible)
    people=Counter(customers[r['customer_id']]['customer_unique_id'] for r in july.values())
    repeats=sum(n>=2 for n in people.values())
    gmv=sum((totals[k] for k in july),D(0)); previous=sum((totals[k] for k in june),D(0))
    return dict(gmv=gmv,merchandise_value=merchandise,delivered_order_count=len(july),aov=gmv/len(july),
        payment_value=sum((D(r['payment_value']) for r in read('order_payments') if '2018-07-01'<=orders[r['order_id']]['order_purchase_timestamp']<'2018-08-01'),D(0)),
        repeat_customer_rate=D(repeats)/len(people),repeat_eligible=len(people),repeat_customers=repeats,
        average_review_score=sum(scores)/len(scores),review_eligible=len(scores),review_missing=len(july)-len(scores),
        late_rate=D(late)/len(eligible),late_eligible=len(eligible),late=late,late_missing=len(july)-len(eligible),
        sp_gmv=sum((totals[k] for k,r in july.items() if customers[r['customer_id']]['customer_state']=='SP'),D(0)),
        category_gmv=dict(categories),previous_gmv=previous,previous_count=len(june),previous_aov=previous/len(june),gmv_change=gmv-previous,gmv_growth=(gmv-previous)/previous)


def validate(csv_dir,manifest_path,output,source_root=None):
    manifest=json.loads(manifest_path.read_text(encoding='utf-8'))
    data,files=load_csv(csv_dir)
    assert files==manifest['files'], 'Source snapshot differs from imported manifest'
    oracle=independent_oracle(csv_dir); engine=Executor()
    assert engine.check_identity()['dataset_id']==manifest['dataset_id']
    checks=[]
    def query(name,metrics,dimensions=(),filters=(),comparison='none'):
        print('Validating '+name,flush=True)
        plan=QueryPlan(metric_ids=metrics,dimensions=list(dimensions),filters=list(filters),comparison=comparison,time_range={'start':'2018-07-01','end':'2018-08-01'})
        result=build_answer(plan,[engine.execute(q.sql,q.parameters) for q in compile_plan(plan)])
        assert result.status=='answered', (name,result.status)
        checks.append({'case':name,'plan':plan.model_dump(mode='json'),'status':'passed','table':result.table})
        return result
    for metric in ['gmv','merchandise_value','delivered_order_count','aov','payment_value','repeat_customer_rate','average_review_score','late_rate']:
        result=query(metric,[metric]); assert result.facts[0].value==oracle[metric], metric
        prefix={'repeat_customer_rate':'repeat','average_review_score':'review','late_rate':'late'}.get(metric)
        if prefix:
            assert result.table[0]['eligible']==oracle[prefix+'_eligible']
            if prefix!='repeat': assert result.table[0]['missing']==oracle[prefix+'_missing']
    assert query('sp_gmv',['gmv'],filters=[{'field':'state','value':'SP'}]).facts[0].value==oracle['sp_gmv']
    categories=query('category_gmv',['gmv'],dimensions=['category'])
    assert {r['category']:r['gmv'] for r in categories.table}==oracle['category_gmv']
    comparison=query('june_comparison',['gmv'],comparison='previous_period')
    assert comparison.table[0]['gmv_change']==oracle['gmv_change']
    assert comparison.table[0]['gmv_growth']==oracle['gmv_growth']
    keys={'customers':['customer_id'],'sellers':['seller_id'],'products':['product_id'],'orders':['order_id'],'order_items':['order_id','order_item_id'],'order_payments':['order_id','payment_sequential'],'order_reviews':['review_id','order_id'],'product_category_name_translation':['product_category_name']}
    duplicates={table:len(data[table])-len({tuple(r[c] for c in cols) for r in data[table]}) for table,cols in keys.items()}
    assert not any(duplicates.values())
    relationships={}
    for child,column,parent in [('orders','customer_id','customers'),('order_items','order_id','orders'),('order_items','product_id','products'),('order_items','seller_id','sellers'),('order_payments','order_id','orders'),('order_reviews','order_id','orders')]:
        parent_ids={r[column] for r in data[parent]}
        relationships[child+'.'+column]=sum(r[column] not in parent_ids for r in data[child])
    assert not any(relationships.values())
    quality={
        'orders_without_payments':len({r['order_id'] for r in data['orders']}-{r['order_id'] for r in data['order_payments']}),
        'orders_without_items':len({r['order_id'] for r in data['orders']}-{r['order_id'] for r in data['order_items']}),
        'orders_without_reviews':len({r['order_id'] for r in data['orders']}-{r['order_id'] for r in data['order_reviews']}),
        'geolocation_duplicate_rows_removed':next(f['source_rows']-f['loaded_rows'] for f in files if f['file']==FILES['geolocation']),
        'geolocation_points_excluded_from_zip_bbox':sum(not (D('-34')<=D(r['geolocation_lat'])<=D('6') and D('-74')<=D(r['geolocation_lng'])<=D('-28')) for r in data['geolocation']),
        'untranslated_nonnull_categories':sorted({r['product_category_name'] for r in data['products'] if r['product_category_name']} - {r['product_category_name'] for r in data['product_category_name_translation']}),
        'rejected_rows':0,
    }
    with engine.connect() as conn:
        with conn.cursor() as cur:
            for table,count in manifest['row_counts'].items():
                if table in ('geolocation','order_reviews'): continue # restricted raw tables
                cur.execute(f'SELECT COUNT(*) AS n FROM `{table}`'); assert cur.fetchone()['n']==count
    upstream=[]
    if source_root:
        with (source_root/'results/sql/01_monthly_revenue.csv').open(encoding='utf-8-sig',newline='') as f:
            for row in csv.DictReader(f):
                if row['ym'] not in ('2018-06','2018-07'): continue
                prefix='previous_' if row['ym']=='2018-06' else ''
                assert D(row['gmv'])==oracle[prefix+'gmv']
                assert int(row['n_orders'])==oracle['previous_count' if prefix else 'delivered_order_count']
                assert D(row['aov'])==oracle[prefix+'aov'].quantize(D('.01'))
                upstream.append({'month':row['ym'],'gmv':row['gmv'],'orders':row['n_orders'],'aov':row['aov'],'status':'passed'})
    report={'purpose':'public development data/engineering validation; not model performance or held-out evaluation','dataset_id':manifest['dataset_id'],'files':files,'row_counts':manifest['row_counts'],'period':{k:manifest[k] for k in ('purchase_min','purchase_max')},'duplicate_keys':duplicates,'orphan_relationships':relationships,'null_counts':{t:{c:sum(r[c] is None for r in rows) for c in rows[0]} for t,rows in data.items() if rows},'cleaning':['UTF-8 BOM accepted; whitespace stripped; empty fields NULL; product _lenght renamed _length; ZIP padded to five characters','Only exact geolocation duplicates removed; all other input rows retained; zero rejected rows','Coordinates stored as DECIMAL with eight fractional digits; derived ZIP uses bbox lat [-34,6], lon [-74,-28], medians and lexical-tie modes','Latest review: creation timestamp, answer timestamp, review_id descending; missing reviews/delivery dates excluded only from corresponding ratio denominator'],'oracle':oracle,'queries':checks,'query_count':len(checks),'upstream_monthly_checks':upstream,'upstream_reference':'results/SQL_REPORT.md links results/sql/01_monthly_revenue.csv; same delivered purchase-calendar-month GMV, rounded AOV'}
    report['data_quality']=quality
    output.parent.mkdir(parents=True,exist_ok=True); output.write_text(json.dumps(report,indent=2,default=str,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'dataset_id':manifest['dataset_id'],'queries_passed':len(checks),'upstream_months':len(upstream),'report':str(output)}))


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv-dir',required=True,type=Path)
    parser.add_argument('--manifest',required=True,type=Path)
    parser.add_argument('--output',type=Path,default=Path('reports/real-data-validation.json'))
    parser.add_argument('--source-root',type=Path)
    args=parser.parse_args(); validate(args.csv_dir,args.manifest,args.output,args.source_root)
