from datetime import date
from decimal import Decimal, ROUND_HALF_UP
from enterprise_query.contracts import AnswerEnvelope, AnswerFact, QueryPlan
from enterprise_query.compiler import previous_range
from enterprise_query.catalog import DEFINITIONS


def divide(a,b):
    return None if a is None or not b else Decimal(a)/Decimal(b)


def coverage(period):
    return ('source-policy complete months' if period.start.day == period.end.day == 1 and
            date(2017,1,1) <= period.start < period.end <= date(2018,9,1) else 'partial/unknown')


def derived(row):
    row = dict(row)
    if 'delivered_order_count' in row:
        row['aov'] = divide(row.get('gmv'),row['delivered_order_count'])
    if 'late' in row:
        row['late_rate'] = divide(row['late'],row['eligible'])
    if 'repeat_customers' in row:
        row['repeat_customer_rate'] = divide(row['repeat_customers'],row['eligible'])
    if 'score_sum' in row:
        row['average_review_score'] = divide(row['score_sum'],row['eligible'])
    return row


def unit(metric):
    if metric in ('gmv','aov','merchandise_value','payment_value'):
        return 'BRL'
    return 'ratio' if metric.endswith('_rate') else ('orders' if metric=='delivered_order_count' else 'score')


def display(value, units):
    if value is None:
        return 'undefined（分母為零或資料不足）'
    if units == 'BRL':
        return f'{Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)} BRL'
    if units == 'ratio':
        return f'{(Decimal(value)*100).quantize(Decimal("0.01"))}%'
    return str(value)


def build_answer(plan: QueryPlan, results):
    result = results[0]
    rows = [derived(r) for r in result.rows]
    nonempty = bool(rows) and any(r.get('population_count',r.get('eligible',0)) for r in rows)
    limitations = ['合成資料，僅供工程示範；mock 不代表模型能力。' if result.dataset_id.startswith('synthetic') else '歷史資料，不代表即時營運。',
                   '來源 DATETIME 不作時區換算；日期區間含起日、不含迄日。',
                   '完整月範圍是前作分析採用的 coverage policy，非獨立證實的完整性。']
    if {'category','seller'} & set(plan.dimensions):
        limitations.append('同訂單可跨品類或賣家；分組訂單數不可相加成總訂單數。')
    if any(r.truncated for r in results):
        limitations.append('truncated：結果超過 200 列；不從截斷結果計算全體總數或完整排名。')
    cov = coverage(plan.time_range)
    key = lambda row: tuple(row.get(d) for d in plan.dimensions)
    references = {key(row): [f'{result.query_id}:rows[{i}]'] for i, row in enumerate(result.rows)}
    if len(results)>1:
        if any(r.truncated for r in results):
            return AnswerEnvelope(status='rejected',
                message='比較期結果已截斷，無法核對完整分組變化；請縮小期間或篩選範圍。',
                time_range=plan.time_range, coverage=cov, limitations=limitations,
                definition=[DEFINITIONS[m] for m in plan.metric_ids], evidence=results)
        previous = previous_range(plan.time_range)
        month_shift = (plan.time_range.start.year-previous.start.year)*12+plan.time_range.start.month-previous.start.month
        prev = {}
        for i, raw in enumerate(results[1].rows):
            row = derived(raw)
            if 'month' in plan.dimensions:
                year, month = map(int, row['month'].split('-'))
                year, month = divmod(year*12+month-1+month_shift, 12)
                row['month'] = f'{year:04d}-{month+1:02d}'
            k = key(row)
            prev[k] = row
            references.setdefault(k, []).append(f'{results[1].query_id}:rows[{i}]')
        curr = {key(row):row for row in rows}
        single_month = (plan.time_range.end.year-plan.time_range.start.year)*12+plan.time_range.end.month-plan.time_range.start.month == 1
        allowed = single_month and cov != 'partial/unknown' and coverage(previous) != 'partial/unknown'
        if not allowed:
            limitations.append('僅相鄰且 coverage policy 允許的完整單月產生月增率；本次僅顯示絕對差。')
        rows=[]
        for k in sorted(set(curr)|set(prev),key=str):
            current=curr.get(k)
            before=prev.get(k)
            row=dict(current or {d:v for d,v in zip(plan.dimensions,k)})
            for metric in plan.metric_ids:
                additive = metric in ('gmv','merchandise_value','payment_value','delivered_order_count')
                cv = current.get(metric) if current else (Decimal(0) if additive else None)
                pv = before.get(metric) if before else (Decimal(0) if additive else None)
                row[metric]=cv
                row[metric+'_previous']=pv
                row[metric+'_change']=None if cv is None or pv is None else cv-pv
                row[metric+'_growth']=divide(row[metric+'_change'],pv) if allowed else None
            rows.append(row)
    if plan.sort == 'value_desc':
        rows.sort(key=lambda r:(r.get(plan.metric_ids[0]) is None, -(r.get(plan.metric_ids[0]) or 0), str([r.get(d) for d in plan.dimensions])))
    if len(rows)>plan.top_k:
        limitations.append(f'僅展示前 {plan.top_k} 組；不據此推算全體總數。')
        rows=rows[:plan.top_k]
    facts=[]
    for row in rows:
        # Pair source indices before sorting/display truncation and month relabeling.
        refs = references.get(key(row), [])
        for metric in plan.metric_ids:
            val=row.get(metric)
            facts.append(AnswerFact(metric_id=metric,value=val,unit=unit(metric),display=display(val,unit(metric)),
                                    result_reference=refs,calculation=DEFINITIONS[metric]))
            if len(results)>1:
                for suffix, units, calculation in (
                    ('previous', unit(metric), f'{metric}: previous = 前期相同 population、口徑與相對維度結果'),
                    ('change', unit(metric), f'{metric}: change = current - previous'),
                    ('growth', 'ratio', f'{metric}: growth = change / previous；前期為零或非允許完整單月時 undefined'),
                ):
                    value = row[metric+'_'+suffix]
                    facts.append(AnswerFact(metric_id=metric+'_'+suffix, value=value, unit=units,
                                            display=display(value, units), result_reference=refs,
                                            calculation=calculation))
    return AnswerEnvelope(status='answered' if nonempty else 'empty',
        message=('已依指定口徑查得結果。' if nonempty else '指定期間／篩選條件沒有符合資料；不是金額為零。'),
        facts=facts if nonempty else [],table=rows if nonempty else [],definition=[DEFINITIONS[m] for m in plan.metric_ids],
        population='全部訂單狀態' if 'payment_value' in plan.metric_ids else 'delivered',time_range=plan.time_range,
        coverage=cov,limitations=limitations,evidence=results)
