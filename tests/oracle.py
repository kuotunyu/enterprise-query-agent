from collections import Counter, defaultdict
from decimal import Decimal as D

def calculate(data):
    orders = {o['order_id']:o for o in data['orders']}
    customers = {c['customer_id']:c for c in data['customers']}
    products = {p['product_id']:p for p in data['products']}
    translations = {p['product_category_name']:p['product_category_name_english'] for p in data['product_category_name_translation']}
    july = {i:o for i,o in orders.items() if '2018-07-01' <= o['order_purchase_timestamp'] < '2018-08-01' and o['order_status']=='delivered'}
    june = {i:o for i,o in orders.items() if '2018-06-01' <= o['order_purchase_timestamp'] < '2018-07-01' and o['order_status']=='delivered'}
    totals = defaultdict(lambda:D(0)); cats = defaultdict(lambda:D(0)); prev = defaultdict(lambda:D(0)); cat_orders=defaultdict(set)
    merchandise=D(0)
    for item in data['order_items']:
        oid=item['order_id']; value=D(item['price'])+D(item['freight_value'])
        totals[oid]+=value
        category=products[item['product_id']]['product_category_name']; category=translations.get(category,category or 'unknown')
        if oid in july:
            cats[category]+=value; cat_orders[category].add(oid); merchandise+=D(item['price'])
        if oid in june: prev[category]+=value
    gmv=sum((totals[i] for i in july),D(0)); previous=sum((totals[i] for i in june),D(0))
    reviews=[]
    for oid in july:
        candidates=[r for r in data['order_reviews'] if r['order_id']==oid]
        if candidates: reviews.append(D(str(max(candidates,key=lambda r:(r['review_creation_date'],r['review_answer_timestamp'],r['review_id']))['review_score'])))
    eligible=[o for o in july.values() if o['order_delivered_customer_date'] is not None and o['order_estimated_delivery_date'] is not None]
    people=Counter(customers[o['customer_id']]['customer_unique_id'] for o in july.values())
    bad=D(0)
    for oid in july:
        payments=[p for p in data['order_payments'] if p['order_id']==oid]
        rs=[r for r in data['order_reviews'] if r['order_id']==oid]
        bad+=totals[oid]*max(1,len(payments))*max(1,len(rs))
    return dict(gmv=gmv,order_count=len(july),aov=gmv/len(july),merchandise=merchandise,category_gmv=dict(cats),payment=sum((D(p['payment_value']) for p in data['order_payments'] if '2018-07-01' <= orders[p['order_id']]['order_purchase_timestamp'] < '2018-08-01'),D(0)),repeat_rate=D(sum(v>=2 for v in people.values()))/len(people),review_average=sum(reviews)/len(reviews),review_valid=len(reviews),review_missing=len(july)-len(reviews),late_rate=D(sum(o['order_delivered_customer_date']>o['order_estimated_delivery_date'] for o in eligible))/len(eligible),late_eligible=len(eligible),late_missing=len(july)-len(eligible),difference=gmv-previous,growth=(gmv-previous)/previous if previous else None,category_difference={c:cats[c]-prev[c] for c in cats.keys()|prev.keys()},category_aov={c:v/len(cat_orders[c]) for c,v in cats.items()},sp_gmv=sum((totals[i] for i,o in july.items() if customers[o['customer_id']]['customer_state']=='SP'),D(0)),bad_join_gmv=bad)
