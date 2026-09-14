"""Public deterministic development cases; never holdout/model quality evidence."""
import os
import pytest
from enterprise_query.planner import DEVELOPMENT_QUESTIONS

@pytest.mark.integration
@pytest.mark.skipif(not os.getenv('EQA_WEB_URL'),reason='set EQA_WEB_URL to running isolated MySQL-backed app')
def test_26_chinese_development_flows():
    import httpx
    import uuid
    import json
    from pathlib import Path
    outcomes=[]
    for index,question in enumerate(DEVELOPMENT_QUESTIONS):
        session=str(uuid.uuid4())
        payload={'question':question,'session_id':session,'request_id':str(uuid.uuid4()),'revision':1,'as_of_date':'2026-09-14'}
        response=httpx.post(os.environ['EQA_WEB_URL']+'/api/ask',json=payload,timeout=65).json()
        first=response['status']
        while response['status']=='needs_clarification':
            payload.update(request_id=str(uuid.uuid4()),revision=payload['revision']+1,clarification=response['options'][0])
            response=httpx.post(os.environ['EQA_WEB_URL']+'/api/ask',json=payload,timeout=65).json()
        expected='unsupported' if index in (15,16,17,24) else ('empty' if index in (13,14) else 'answered')
        outcomes.append({'question':question,'first_status':first,'expected_status':expected,'outcome':response})
    Path('.local').mkdir(exist_ok=True)
    from datetime import datetime
    output=Path('.local')/('development-mock-'+datetime.now().strftime('%Y%m%d-%H%M%S')+'.json')
    output.write_text(json.dumps(outcomes,ensure_ascii=False,indent=2),encoding='utf-8')
    assert all(o['outcome']['status']==o['expected_status'] for o in outcomes), [(o['question'],o['outcome']['status']) for o in outcomes if o['outcome']['status']!=o['expected_status']]
