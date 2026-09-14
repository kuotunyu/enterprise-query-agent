"""Task limits over the real compiler and isolated runtime, no provider network."""
import asyncio
import os
import pytest
from enterprise_query.contracts import QuestionRequest
from enterprise_query.service import Service


@pytest.mark.integration
@pytest.mark.skipif(os.getenv('EQA_INTEGRATION')!='1',reason='requires isolated MySQL')
def test_three_business_selects_are_cumulative_across_revisions():
    async def run():
        service=Service()
        def request(revision,question):
            return QuestionRequest(question=question,session_id='sql-budget',request_id=f'sql-budget-{revision}',revision=revision)
        first=await service.ask(request(1,'2018年7月GMV比上月'))
        assert first.status=='answered' and first.usage['sql_selects']==2
        second=await service.ask(request(2,'2018年7月GMV'))
        assert second.status=='answered' and second.usage['sql_selects']==3
        third=await service.ask(request(3,'2018年7月GMV'))
        assert third.status=='rejected' and third.usage['sql_selects']==3
    asyncio.run(run())


def test_two_clarification_rounds_are_enforced():
    async def run():
        service=Service()
        for revision,status in [(1,'needs_clarification'),(2,'needs_clarification'),(3,'rejected')]:
            answer=await service.ask(QuestionRequest(question='營收',session_id='clarify-limit',request_id=f'clarify-{revision}',revision=revision,clarification='不確定' if revision>1 else None))
            assert answer.status==status
        assert answer.usage['clarification_rounds']==2 and answer.usage['model_calls']==3
    asyncio.run(run())
