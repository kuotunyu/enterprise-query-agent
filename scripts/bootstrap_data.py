"""Bootstrap only the isolated, marked EQA MySQL; never load source SQL directly."""
import argparse
from collections import Counter, defaultdict
import csv
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import os
from pathlib import Path
from statistics import median

ROOT = Path(__file__).resolve().parents[1]
TABLES = ['product_category_name_translation','customers','sellers','geolocation','products','orders','order_items','order_payments','order_reviews']
FILES = {t: ('product_category_name_translation.csv' if t=='product_category_name_translation' else f'olist_{t}_dataset.csv') for t in TABLES}

# Fixed schema allow-list; CSV headers never authorize SQL identifiers.
COLUMNS = {'product_category_name_translation': ('product_category_name',
                                       'product_category_name_english'),
 'customers': ('customer_id',
               'customer_unique_id',
               'customer_zip_code_prefix',
               'customer_city',
               'customer_state'),
 'sellers': ('seller_id',
             'seller_zip_code_prefix',
             'seller_city',
             'seller_state'),
 'geolocation': ('geolocation_zip_code_prefix',
                 'geolocation_lat',
                 'geolocation_lng',
                 'geolocation_city',
                 'geolocation_state'),
 'products': ('product_id',
              'product_category_name',
              'product_name_length',
              'product_description_length',
              'product_photos_qty',
              'product_weight_g',
              'product_length_cm',
              'product_height_cm',
              'product_width_cm'),
 'orders': ('order_id',
            'customer_id',
            'order_status',
            'order_purchase_timestamp',
            'order_approved_at',
            'order_delivered_carrier_date',
            'order_delivered_customer_date',
            'order_estimated_delivery_date'),
 'order_items': ('order_id',
                 'order_item_id',
                 'product_id',
                 'seller_id',
                 'shipping_limit_date',
                 'price',
                 'freight_value'),
 'order_payments': ('order_id',
                    'payment_sequential',
                    'payment_type',
                    'payment_installments',
                    'payment_value'),
 'order_reviews': ('review_id',
                   'order_id',
                   'review_score',
                   'review_comment_title',
                   'review_comment_message',
                   'review_creation_date',
                   'review_answer_timestamp')}

def identifier(number):
    return f'{number:032x}'

def synthetic_data(variant='base'):
    data={t:[] for t in TABLES}
    data['product_category_name_translation']=[dict(product_category_name=x,product_category_name_english=x) for x in ['A','B']]
    data['sellers']=[dict(seller_id=identifier(301),seller_zip_code_prefix='01000',seller_city='Sao Paulo',seller_state='SP')]
    for i,person in enumerate([1,1,2,3,4],1):
        data['customers'].append(dict(customer_id=identifier(100+i),customer_unique_id=identifier(200+person),customer_zip_code_prefix='02000' if i==4 else '01000',customer_city='Rio' if i==4 else 'Sao Paulo',customer_state='RJ' if i==4 else 'SP'))
    for i,category in enumerate(['A','B'],1):
        data['products'].append(dict(product_id=identifier(400+i),product_category_name=category,product_name_length=1,product_description_length=1,product_photos_qty=1,product_weight_g=100,product_length_cm=1,product_height_cm=1,product_width_cm=1))
    for zip_code,lat,lng,city,state in [('01000','-23.5','-46.6','Sao Paulo','SP'),('01000','-23.6','-46.7','Sao Paulo','SP'),('02000','-22.9','-43.2','Rio','RJ')]:
        data['geolocation'].append(dict(geolocation_zip_code_prefix=zip_code,geolocation_lat=lat,geolocation_lng=lng,geolocation_city=city,geolocation_state=state))
    for i,date,actual,expected in [(1,'07-10','07-16','07-15'),(2,'07-20','07-25','07-25'),(3,'07-21',None,'07-26'),(4,'07-22',None,'07-27'),(5,'06-10','06-15','06-15')]:
        data['orders'].append(dict(order_id=identifier(i),customer_id=identifier(100+i),order_status='canceled' if i==3 else 'delivered',order_purchase_timestamp=f'2018-{date} 12:00:00',order_approved_at=f'2018-{date} 12:00:00',order_delivered_carrier_date=None,order_delivered_customer_date=f'2018-{actual} 12:00:00' if actual else None,order_estimated_delivery_date=f'2018-{expected} 12:00:00'))
    for oid,item,product,price,freight in [(1,1,1,'100','10'),(1,2,2,'50','5'),(2,1,1,'40','4'),(3,1,1,'200','20'),(4,1,2,'30','3'),(5,1,1,'100','10')]:
        data['order_items'].append(dict(order_id=identifier(oid),order_item_id=item,product_id=identifier(400+product),seller_id=identifier(301),shipping_limit_date='2018-07-30 12:00:00',price=price,freight_value=freight))
    for oid,seq,value in [(1,1,'100'),(1,2,'65'),(2,1,'44'),(3,1,'220'),(4,1,'30'),(5,1,'110')]:
        data['order_payments'].append(dict(order_id=identifier(oid),payment_sequential=seq,payment_type='credit_card',payment_installments=1,payment_value=value))
    for rid,oid,score,date in [(501,1,1,'07-17'),(502,1,5,'07-18'),(503,2,3,'07-26')]:
        data['order_reviews'].append(dict(review_id=identifier(rid),order_id=identifier(oid),review_score=score,review_comment_title=None,review_comment_message=None,review_creation_date=f'2018-{date} 12:00:00',review_answer_timestamp=f'2018-{date} 13:00:00'))
    if variant=='split_payment':
        data['order_payments'][0]['payment_value']='40'
        data['order_payments'].append({**data['order_payments'][0],'payment_sequential':3,'payment_value':'60'})
    elif variant=='older_review':
        data['order_reviews'].append({**data['order_reviews'][0],'review_id':identifier(504),'review_creation_date':'2018-07-11 12:00:00'})
    elif variant!='base': raise ValueError('Unknown synthetic variant')
    return data

def derive_geolocation(rows):
    groups=defaultdict(list)
    for r in rows:
        if Decimal('-34')<=Decimal(r['geolocation_lat'])<=Decimal('6') and Decimal('-74')<=Decimal(r['geolocation_lng'])<=Decimal('-28'):
            groups[r['geolocation_zip_code_prefix']].append(r)
    def mode(values):
        counts=Counter(values)
        return min(counts,key=lambda x:(-counts[x],x))
    return [dict(zip_code_prefix=z,lat=median([Decimal(r['geolocation_lat']) for r in rs]),lng=median([Decimal(r['geolocation_lng']) for r in rs]),city=mode([r['geolocation_city'] for r in rs]),state=mode([r['geolocation_state'] for r in rs]),n_points=len(rs)) for z,rs in sorted(groups.items())]

def load_csv(directory):
    data={}; manifest=[]
    expected = {table:set(columns) for table,columns in COLUMNS.items()}
    for table in TABLES:
        path=directory/FILES[table]
        with path.open(encoding='utf-8-sig',newline='') as handle:
            rows=[]; seen=set(); raw_count=0
            reader=csv.DictReader(handle)
            headers=[k.replace('_lenght','_length') for k in (reader.fieldnames or [])]
            if len(headers)!=len(set(headers)) or set(headers)!=expected[table]:
                raise ValueError(f'CSV columns do not match {table}')
            for raw in reader:
                raw_count+=1
                if None in raw or any(v is None for v in raw.values()): raise ValueError('Malformed CSV row')
                row={k.replace('_lenght','_length'):(v.strip() or None) for k,v in raw.items()}
                if set(row)!=expected[table]: raise ValueError(f'CSV columns do not match {table}')
                for k,v in row.items():
                    if v is not None and ('_date' in k or '_timestamp' in k or k=='order_approved_at'): datetime.strptime(v,'%Y-%m-%d %H:%M:%S')
                    if v is not None and 'zip_code_prefix' in k: row[k]=v.zfill(5)
                if table=='geolocation':
                    key=tuple(row.items())
                    if key in seen: continue
                    seen.add(key)
                rows.append(row)
        data[table]=rows
        with path.open('rb') as handle: digest=hashlib.file_digest(handle,'sha256').hexdigest()
        manifest.append(dict(file=path.name,sha256=digest,source_rows=raw_count,loaded_rows=len(rows)))
    return data,manifest

def verify_destination(cursor):
    cursor.execute('SELECT DATABASE(), @@hostname, @@port, @@version')
    database,hostname,port,version=cursor.fetchone()
    if database!='eqa_v1' or not version.startswith('8.4.'):
        raise RuntimeError('Refusing non-EQA database or non-MySQL 8.4 destination')
    cursor.execute("SELECT value_text FROM eqa_metadata WHERE key_name='project_id'")
    if cursor.fetchone()!=('eqa_v1',): raise RuntimeError('Destination EQA marker mismatch')
    return dict(database=database,server_hostname=hostname,server_port=port,mysql_version=version)

def bootstrap(csv_dir=None,variant='base'):
    import pymysql
    host=os.getenv('EQA_DB_HOST','127.0.0.1'); port=int(os.getenv('EQA_DB_PORT','3307'))
    if host not in ('127.0.0.1','localhost') or port==3306: raise RuntimeError('Bootstrap requires a separate loopback MySQL port (default 3307)')
    password=os.environ['EQA_BOOTSTRAP_PASSWORD']; runtime_password=os.environ['EQA_DB_PASSWORD']
    if password==runtime_password: raise RuntimeError('Bootstrap and runtime passwords must differ')
    data,files=load_csv(Path(csv_dir)) if csv_dir else (synthetic_data(variant),[])
    dataset='olist-local-'+hashlib.sha256(json.dumps(files,sort_keys=True).encode()).hexdigest()[:16] if csv_dir else ('synthetic-v1' if variant=='base' else 'synthetic-v1-'+variant)
    conn=pymysql.connect(host=host,port=port,user='root',password=password,database='eqa_v1',charset='utf8mb4',local_infile=False,autocommit=False)
    try:
        with conn.cursor() as cur:
            identity=verify_destination(cur)
            cur.execute("SELECT COUNT(*) FROM information_schema.tables WHERE table_schema='eqa_v1' AND table_name='orders'")
            if cur.fetchone()[0]==0:
                for statement in (ROOT/'db/schema.sql').read_text(encoding='utf-8-sig').split(';'):
                    if statement.strip(): cur.execute(statement)
            for statement in (ROOT/'db/views.sql').read_text(encoding='utf-8-sig').split(';'):
                if statement.strip(): cur.execute(statement)
            # Mark loading before changing data; a failed import stays unusable.
            cur.execute("REPLACE INTO eqa_metadata VALUES ('dataset_id','loading')")
            conn.commit()
            cur.execute('DELETE FROM geolocation_zip')
            for table in reversed(TABLES): cur.execute(f'DELETE FROM `{table}`')
            data['geolocation_zip']=derive_geolocation(data['geolocation'])
            for table,rows in data.items():
                if not rows: continue
                columns=list(COLUMNS[table] if table in COLUMNS else ('zip_code_prefix','lat','lng','city','state','n_points')); query=f"INSERT INTO `{table}` ({','.join('`'+c+'`' for c in columns)}) VALUES ({','.join(['%s']*len(columns))})"
                for offset in range(0,len(rows),1000): cur.executemany(query,[tuple(row[c] for c in columns) for row in rows[offset:offset+1000]])
            cur.execute("SELECT MIN(order_purchase_timestamp),MAX(order_purchase_timestamp) FROM orders")
            bounds=cur.fetchone()
            metadata={'project_id':'eqa_v1','dataset_id':dataset,'schema_version':'schema-v1','etl_version':'etl-v1','purchase_min':str(bounds[0]),'purchase_max':str(bounds[1])}
            for key,value in metadata.items(): cur.execute('REPLACE INTO eqa_metadata VALUES (%s,%s)',(key,value))
            cur.execute("CREATE USER IF NOT EXISTS 'eqa_reader'@'%%' IDENTIFIED BY %s",(runtime_password,))
            cur.execute("ALTER USER 'eqa_reader'@'%%' IDENTIFIED BY %s",(runtime_password,))
            cur.execute("REVOKE ALL PRIVILEGES, GRANT OPTION FROM 'eqa_reader'@'%'")
            for table in TABLES+['geolocation_zip','v_order_gmv','v_order_review','eqa_metadata']:
                if table in ('order_reviews','geolocation'):
                    continue
                else: cur.execute(f"GRANT SELECT ON eqa_v1.`{table}` TO 'eqa_reader'@'%'")
            conn.commit()
            counts={}
            for table in data:
                cur.execute(f'SELECT COUNT(*) FROM `{table}`'); counts[table]=cur.fetchone()[0]
    finally: conn.close()
    manifest={**metadata,'kind':'local_olist' if csv_dir else 'synthetic','variant':variant,'files':files,'row_counts':counts,'identity':identity,'fixture_sha256':hashlib.sha256(json.dumps(data,sort_keys=True,default=str).encode()).hexdigest()}
    target=ROOT/'.local/dataset-manifest.json'; target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(manifest,indent=2,default=str),encoding='utf-8')
    print(json.dumps({'dataset_id':dataset,'row_counts':counts,'manifest':'.local/dataset-manifest.json'},indent=2))
    return manifest

if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--csv-dir',type=Path)
    parser.add_argument('--variant',choices=['base','split_payment','older_review'],default='base')
    args=parser.parse_args()
    bootstrap(args.csv_dir,args.variant)
