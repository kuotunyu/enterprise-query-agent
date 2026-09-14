from fastapi.testclient import TestClient
from enterprise_query.api import create_app
from enterprise_query.service import Service
from tests.test_service import FakeExecutor

def test_http_flow_and_static_security():
    app=create_app(Service(FakeExecutor),check_startup=True)
    with TestClient(app) as client:
        assert client.get('/').status_code==200
        assert client.get('/api/meta').json()['mode']=='mock'
        js=client.get('/static/app.js').text
        assert 'innerHTML' not in js and 'textContent' in js
        r=client.post('/api/ask',json={'question':'2018年7月營收','session_id':'s','request_id':'1'})
        assert r.json()['status']=='needs_clarification'
        r=client.post('/api/ask',json={'question':'2018年7月營收','clarification':'含運 GMV','session_id':'s','request_id':'2','revision':2})
        assert r.json()['status']=='answered'
        assert client.post('/api/cancel/s').json()['cancelled']

import os
import pytest

@pytest.mark.ui
@pytest.mark.skipif(not os.getenv('EQA_WEB_URL'), reason='set EQA_WEB_URL to a running isolated app for browser checks')
def test_browser_six_examples_keyboard_and_evidence():
    from pathlib import Path
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser=p.chromium.launch()
        page=browser.new_page(viewport={'width':1280,'height':1000})
        page.goto(os.environ['EQA_WEB_URL'])
        expect(page.locator('#mode')).to_contain_text('MOCK')
        expect(page.locator('#dataset')).to_contain_text('synthetic-v1')
        examples=page.locator('#examples button')
        assert examples.count()==6
        for n in range(6):
            page.locator('#new').click()
            examples.nth(n).focus();page.keyboard.press('Enter')
            page.locator('#submit').focus();page.keyboard.press('Enter')
            if n==0:
                expect(page.locator('#clarification')).to_be_visible()
                page.locator('#options button').first.focus();page.keyboard.press('Enter')
            expect(page.locator('#result-status')).to_have_text('empty' if n==5 else 'answered')
            expect(page.locator('#evidence')).to_contain_text('query_id')
        page.locator('#new').click()
        page.locator('#question').fill('2018年7月營收')
        page.locator('#submit').click()
        expect(page.locator('#clarification')).to_be_visible()
        page.locator('#options button').first.click()
        expect(page.locator('#facts')).to_contain_text('242.00')
        page.locator('#question').fill('2018年7月不含運商品金額')
        page.locator('#submit').click()
        expect(page.locator('#facts')).to_contain_text('220.00')
        page.locator('#new').click()
        page.locator('#question').fill('2018年7月GMV按品類')
        page.locator('#submit').click()
        expect(page.locator('#result-status')).to_have_text('answered')
        page.locator('summary').click()
        Path('.local/ui').mkdir(parents=True,exist_ok=True)
        page.screenshot(path='.local/ui/desktop.png',full_page=True)
        page.set_viewport_size({'width':390,'height':844})
        page.screenshot(path='.local/ui/mobile.png',full_page=True)
        assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth')
        browser.close()

@pytest.mark.ui
@pytest.mark.skipif(not os.getenv('EQA_WEB_URL'), reason='set EQA_WEB_URL to a running isolated app for browser checks')
def test_browser_result_is_text_and_late_revision_cannot_replace():
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page()
        page.goto(os.environ['EQA_WEB_URL'])
        payload={'status':'answered','message':'<img src=x onerror="window.xss=1">','facts':[],'table':[{'category':'<script>window.xss=1</script>'}],'definition':[],'population':'','coverage':'','time_range':None,'limitations':[],'evidence':[],'usage':{},'options':[]}
        def respond(route):
            r=route.request.post_data_json
            route.fulfill(json={**payload,'revision':r['revision']-1})
        page.route('**/api/ask',respond)
        page.locator('#question').fill('2018年7月GMV');page.locator('#submit').click()
        page.wait_for_timeout(100)
        assert '<img' not in page.locator('#message').inner_text()
        page.unroute('**/api/ask')
        page.route('**/api/ask',lambda route:route.fulfill(json={**payload,'revision':route.request.post_data_json['revision']}))
        page.locator('#submit').click()
        expect(page.locator('#message')).to_have_text(payload['message'])
        assert page.locator('#message img').count()==0
        assert page.evaluate('window.xss') is None
        browser.close()

@pytest.mark.ui
@pytest.mark.skipif(not os.getenv('EQA_WEB_URL'), reason='set EQA_WEB_URL to a running isolated app for browser checks')
@pytest.mark.parametrize('cancel_response',['success','failure'])
def test_old_cancel_response_cannot_overwrite_new_task(cancel_response):
    from playwright.sync_api import sync_playwright, expect
    with sync_playwright() as p:
        browser=p.chromium.launch();page=browser.new_page()
        page.goto(os.environ['EQA_WEB_URL'])
        pending=[]
        page.route('**/api/cancel/*',lambda route:pending.append(route))
        page.locator('#cancel').click()
        expect(page.locator('#status')).to_be_visible()
        page.locator('#new').click()
        page.locator('#question').fill('2018年7月GMV')
        page.locator('#submit').click()
        expect(page.locator('#result-status')).to_have_text('answered')
        assert pending
        if cancel_response=='success': pending[0].fulfill(json={'cancelled':True})
        else: pending[0].abort('failed')
        page.wait_for_timeout(100)
        expect(page.locator('#result-status')).to_have_text('answered')
        expect(page.locator('#status')).to_have_text('查詢狀態：answered')
        browser.close()
