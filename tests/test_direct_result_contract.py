import asyncio
from decimal import Decimal
import pytest
from enterprise_query.comparison import DirectDecision,OpenAIMethod
from enterprise_query.contracts import QueryResult
from enterprise_query.service import Service
from tests.test_service import FakeExecutor,req


def decision(sql):
 return DirectDecision.model_validate({'action':'query','message':'','metadata':{'metric_ids':['average_review_score'],'time_range':{'start':'2018-07-01','end':'2018-08-01'}},'statements':[{'sql':sql,'parameters':[],'label':'current'}]})


def test_unescaped_percent_is_rejected_before_database():
 from enterprise_query.comparison import ModelOutputError
 provider=object.__new__(OpenAIMethod);provider.method='B'
 with pytest.raises(ModelOutputError): provider.prepare(decision("SELECT DATE_FORMAT(order_purchase_timestamp,'%Y-%m') AS month FROM orders"))


def test_missing_denominator_retains_result_evidence():
 class Provider:
  mode='openai';method='B';record_decision=True
  decision_schema=DirectDecision;last_usage={}
  prepare=OpenAIMethod.prepare
  validate_results=OpenAIMethod.validate_results
  def decide(self,*args):return decision('SELECT SUM(review_score) AS score_sum FROM v_order_review')
 class Database(FakeExecutor):
  def execute(self,sql,parameters=(),timeout_ms=5000):
   return QueryResult(query_id='q',sql=sql,parameters=[],columns=['score_sum'],column_types=['DECIMAL'],rows=[{'score_sum':Decimal(10)}],row_count=1,truncated=False,elapsed_ms=1,dataset_id='synthetic-v1',result_hash='hash')
 answer=asyncio.run(Service(Database,Provider()).ask(req('2018年7月評論平均')))
 assert answer.status=='rejected'
 assert answer.usage['errors'][-1]['error_type']=='ModelOutputError'
 assert answer.evidence[0].rows==[{'score_sum':Decimal(10)}]
 assert answer.facts==[]

@pytest.mark.parametrize('code,wanted',[(1054,'rejected'),(2003,'execution_error')])
def test_sql_authoring_error_is_distinct_from_connection_failure(code,wanted):
 import pymysql
 class Provider:
  mode='openai';method='B';record_decision=True
  decision_schema=DirectDecision;last_usage={}
  prepare=OpenAIMethod.prepare
  validate_results=OpenAIMethod.validate_results
  def decide(self,*args):return decision('SELECT SUM(review_score) AS score_sum FROM v_order_review')
 class Database(FakeExecutor):
  def execute(self,*args,**kwargs):raise pymysql.OperationalError(code,'private database message')
 answer=asyncio.run(Service(Database,Provider()).ask(req('2018年7月評論平均')))
 assert answer.status==wanted
 assert 'private database message' not in answer.model_dump_json()
