"""Deterministic development adapter. This is deliberately not a model evaluation."""
import calendar
import re
from datetime import date
from .contracts import PlannerDecision, QueryPlan, TimeRange, Filter

DEVELOPMENT_QUESTIONS = [
 '2018年7月GMV、訂單數與AOV', '2018年7月不含運商品金額', '2018年7月GMV按品類',
 '2018年7月GMV按訂單', '2018年7月全部狀態付款總額', '2018年7月回購率',
 '2018年7月最新評論平均', '2018年7月延遲率', '2018年7月GMV比上月',
 '2018年7月品類GMV比上月', '2018年7月AOV按品類', '2018年7月SP州GMV',
 '2018年7月營收多少', '上個月GMV', '2026年1月GMV', '2018年7月毛利',
 '2018年7月廣告ROI', '2018年7月配送下降原因', '2018年7月GMV按賣家',
 '2018年7月GMV按州', '2018年6月GMV', '2018年7月訂單數',
 '2018年7月平均客單價', 'GMV多少', '2018年7月刪除訂單', '2018年7月GMV前5名品類',
]

class MockPlanner:
    mode = 'mock'
    last_usage = {}

    def decide(self, request, memory):
        q = request.question
        c = request.clarification or ''
        text = q + ' ' + c
        # The mock only understands a single whole month; never silently discard
        # explicitly narrower dates or a multi-month span.
        if (re.search(r'\d+\s*(?:日|號)',text) or
                re.search(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',text) or
                re.search(r'月\s*(?:至|到|—|~|～|-)\s*(?:20\d{2}\s*年\s*)?\d',text)):
            return PlannerDecision(action='unsupported',message='Mock 示範只支援單一完整月份；此明示日期區間無法可靠解讀，未執行查詢。')
        if any(w.lower() in text.lower() for w in ['毛利','成本','ROI','廣告','原因','因果','刪除','DROP','UPDATE','INSERT','密碼','憑證']):
            return PlannerDecision(action='unsupported', message='此資料不支持成本、廣告、因果推論或資料修改；可查詢歷史交易描述性指標。')
        metric = None
        if '不含運' in c or '商品金額' in c: metric = 'merchandise_value'
        elif '付款' in c: metric = 'payment_value'
        elif 'GMV' in c.upper() or '含運' in c: metric = 'gmv'
        if metric: memory['revenue_metric'] = metric
        if '營收' in q and not metric and not any(x in q for x in ['不含運','含運','付款']):
            metric = memory.get('revenue_metric')
            if not metric:
                return PlannerDecision(action='clarify', message='營收要採用哪一種口徑？', options=['含運 GMV','不含運商品金額','全部狀態付款金額'])
        metrics = [metric] if metric else []
        if not metrics:
            mapping = [('回購','repeat_customer_rate'),('延遲','late_rate'),('評論','average_review_score'),('付款','payment_value'),('不含運','merchandise_value'),('商品金額','merchandise_value')]
            for word, mid in mapping:
                if word in text: metrics=[mid]; break
            if not metrics:
                if 'GMV' in text.upper() or '含運' in text or '營收' in text: metrics.append('gmv')
                if '訂單數' in text: metrics.append('delivered_order_count')
                if 'AOV' in text.upper() or '客單價' in text: metrics.append('aov')
        if not metrics:
            return PlannerDecision(action='unsupported',message='示範規則未識別此問題；請使用 GMV、商品金額、付款、訂單數、AOV、延遲率、回購率或評論平均。')
        # Explicit clarification overrides the original period, then stored period.
        match = re.search(r'(20\d{2})\s*年\s*(\d{1,2})\s*月', c) or re.search(r'(20\d{2})\s*年\s*(\d{1,2})\s*月', q)
        if match:
            year, month = map(int,match.groups())
            if not 1 <= month <= 12: return PlannerDecision(action='unsupported', message='月份必須介於 1 與 12。')
            start=date(year,month,1)
        elif '上個月' in text:
            d=request.as_of_date; start=date(d.year-(d.month==1),12 if d.month==1 else d.month-1,1)
        elif 'period' in memory:
            start=date.fromisoformat(memory['period'])
        else:
            return PlannerDecision(action='clarify',message='請指定查詢年月（依購買時間）。',options=['2018年7月','2018年6月'])
        memory['period'] = start.isoformat()
        end=date(start.year+(start.month==12),1 if start.month==12 else start.month+1,1)
        dimensions=[]
        for words, dim in [(['品類','類別'],'category'),(['按賣家'],'seller'),(['按州'],'state'),(['按訂單'],'order'),(['按月'],'month')]:
            if any(w in text for w in words): dimensions.append(dim)
        filters=[]
        states=re.findall(r'(?<![A-Z])([A-Z]{2})\s*州',text.upper())
        for state in ['SP','RJ']:
            if state in text.upper() and state not in states: states.append(state)
        if states: filters.append(Filter(field='state',operator='eq' if len(states)==1 else 'in',value=states[0] if len(states)==1 else states))
        top=re.search(r'前\s*(\d+)\s*名',text)
        try:
            plan=QueryPlan(metric_ids=metrics,dimensions=dimensions,filters=filters,time_range=TimeRange(start=start,end=end),comparison='previous_period' if '比上月' in text or '增長' in text or '貢獻' in text else 'none',sort='value_desc' if top else 'dimension',top_k=min(200,int(top.group(1))) if top else 200)
        except ValueError:
            return PlannerDecision(action='unsupported',message='此指標與分組組合不在 v1 支援範圍。')
        return PlannerDecision(action='query',message='已建立受限業務計畫。',plan=plan)
