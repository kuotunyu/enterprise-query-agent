from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator

Metric = Literal['gmv', 'merchandise_value', 'payment_value', 'delivered_order_count', 'aov', 'late_rate', 'repeat_customer_rate', 'average_review_score']
Dimension = Literal['month', 'state', 'seller', 'category', 'order']


class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')


class TimeRange(StrictModel):
    start: date
    end: date

    @model_validator(mode='after')
    def valid(self):
        if self.start >= self.end:
            raise ValueError('start must precede exclusive end')
        return self


class Filter(StrictModel):
    field: Literal['state', 'category', 'seller', 'order']
    operator: Literal['eq', 'in'] = 'eq'
    value: str | list[str]

    @model_validator(mode='after')
    def valid(self):
        if (self.operator == 'eq') != isinstance(self.value, str):
            raise ValueError('filter value type disagrees with operator')
        if isinstance(self.value, list) and not 1 <= len(self.value) <= 50:
            raise ValueError('in requires 1..50 values')
        return self


class QueryPlan(StrictModel):
    metric_ids: list[Metric] = Field(default_factory=lambda: ['gmv'], min_length=1, max_length=8)
    dimensions: list[Dimension] = Field(default_factory=list, max_length=2)
    filters: list[Filter] = Field(default_factory=list, max_length=8)
    time_range: TimeRange
    comparison: Literal['none', 'previous_period'] = 'none'
    sort: Literal['dimension', 'value_desc'] = 'dimension'
    top_k: int = Field(default=200, ge=1, le=200)
    catalog_version: Literal['v1'] = 'v1'

    @model_validator(mode='after')
    def compatible(self):
        metrics = set(self.metric_ids)
        if len(metrics) != len(self.metric_ids) or len(set(self.dimensions)) != len(self.dimensions):
            raise ValueError('duplicate metric or dimension')
        item_scope = bool({'category', 'seller'} & (set(self.dimensions) | {f.field for f in self.filters}))
        if 'payment_value' in metrics and (len(metrics) != 1 or item_scope):
            raise ValueError('payment_value uses all statuses and cannot allocate to category/seller or mix populations')
        if metrics & {'late_rate', 'repeat_customer_rate', 'average_review_score'}:
            if item_scope or len(metrics) != 1:
                raise ValueError('ratio/review metrics require their own order/customer population')
        if 'repeat_customer_rate' in metrics and self.dimensions:
            raise ValueError('repeat_customer_rate v1 is period-wide')
        return self


class QuestionRequest(StrictModel):
    question: str = Field(min_length=1, max_length=2000)
    request_id: str = Field(min_length=1, max_length=100)
    session_id: str = Field(min_length=1, max_length=100)
    revision: int = Field(default=1, ge=1)
    as_of_date: date = Field(default_factory=date.today)
    clarification: str | None = None


class PlannerDecision(StrictModel):
    action: Literal['clarify', 'query', 'unsupported']
    message: str
    options: list[str] = Field(default_factory=list)
    plan: QueryPlan | None = None

    @model_validator(mode='after')
    def valid(self):
        if self.action == 'query' and self.plan is None:
            raise ValueError('query needs plan')
        return self


class QueryResult(StrictModel):
    query_id: str
    sql: str
    parameters: list[Any]
    columns: list[str]
    column_types: list[str]
    rows: list[dict[str, Any]]
    row_count: int
    truncated: bool
    elapsed_ms: float
    dataset_id: str
    result_hash: str


class AnswerFact(StrictModel):
    metric_id: str
    value: Decimal | int | None
    unit: str
    display: str
    result_reference: list[str]
    calculation: str


class AnswerEnvelope(StrictModel):
    status: Literal['answered', 'needs_clarification', 'unsupported', 'empty', 'rejected', 'timeout', 'provider_error', 'execution_error']
    message: str
    request_id: str = ''
    session_id: str = ''
    revision: int = 1
    mode: str = 'mock'
    facts: list[AnswerFact] = Field(default_factory=list)
    table: list[dict[str, Any]] = Field(default_factory=list)
    definition: list[str] = Field(default_factory=list)
    population: str = ''
    time_range: TimeRange | None = None
    coverage: str = ''
    limitations: list[str] = Field(default_factory=list)
    evidence: list[QueryResult] = Field(default_factory=list)
    options: list[str] = Field(default_factory=list)
    usage: dict[str, Any] = Field(default_factory=dict)
