import asyncio
import copy
import json
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI

from src import codebuddy_router
from src.chat.request import ChatRequestError, prepare_chat_payload
from src.codebuddy_token_manager import CodeBuddyTokenManager
from src.request_log_manager import RequestLogManager


def tool(name):
    return {'type': 'function', 'function': {'name': name, 'parameters': {'type': 'object'}}}


def named_request():
    return {
        'model': 'test', 'messages': [{'role': 'user', 'content': 'Report status'}],
        'tools': [tool('other_tool'), tool('report_status')],
        'tool_choice': {'type': 'function', 'function': {'name': 'report_status'}},
    }


def test_named_choice_keeps_exact_function_schema_and_preserves_caller_body():
    body = named_request()
    original = copy.deepcopy(body)
    result = prepare_chat_payload(body)
    assert result['tool_choice'] == 'required'
    assert result['tools'] == [body['tools'][1]]
    assert result['stream'] is True
    assert body == original


@pytest.mark.parametrize('choice', ['auto', 'none', 'required'])
def test_string_choices_are_preserved(choice):
    body = named_request()
    body['tool_choice'] = choice
    result = prepare_chat_payload(body)
    assert result['tool_choice'] == choice
    assert len(result['tools']) == 2


@pytest.mark.parametrize('choice', [
    {}, True, 'unknown', {'type': 'function'},
    {'type': 'function', 'function': {'name': 'missing'}},
    {'type': 'custom', 'custom': {'name': 'report_status'}},
])
def test_invalid_or_unrepresentable_choice_is_rejected_before_upstream(choice):
    body = named_request()
    body['tool_choice'] = choice
    with pytest.raises(ChatRequestError):
        prepare_chat_payload(body)


@pytest.mark.parametrize('stream', [False, True])
def test_http_endpoint_sends_string_choice_and_keeps_tool_response(tmp_path, monkeypatch, stream):
    directory = tmp_path / 'credentials'
    directory.mkdir()
    (directory / 'account.json').write_text(json.dumps({'bearer_token': 'fake', 'user_id': 'fake'}))
    manager = CodeBuddyTokenManager(str(directory))
    logs = RequestLogManager(str(tmp_path / 'audit.db'))
    monkeypatch.setattr(codebuddy_router, 'codebuddy_token_manager', manager)
    monkeypatch.setattr(codebuddy_router, 'request_log_manager', logs)
    monkeypatch.setattr(codebuddy_router.codebuddy_credential_refresher, 'ensure_fresh', AsyncMock(side_effect=lambda selected: selected))
    seen = []
    def upstream(request):
        body = json.loads(request.content)
        assert body['tool_choice'] == 'required'
        assert [item['function']['name'] for item in body['tools']] == ['report_status']
        seen.append(body)
        chunk = {'choices': [{'delta': {'tool_calls': [{'id': 'tooluse_1', 'type': 'function', 'function': {'name': 'report_status', 'arguments': '{"status":"ok"}'}}]}, 'finish_reason': 'tool_calls'}]}
        return httpx.Response(200, text='data: ' + json.dumps(chunk) + '\n\ndata: [DONE]\n\n')
    app = FastAPI()
    app.include_router(codebuddy_router.router, prefix='/codebuddy')
    async def authenticated():
        return 'test'
    app.dependency_overrides[codebuddy_router.authenticate] = authenticated
    async def exercise():
        async with httpx.AsyncClient(transport=httpx.MockTransport(upstream)) as upstream_client:
            monkeypatch.setattr(codebuddy_router, 'get_http_client', AsyncMock(return_value=upstream_client))
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url='http://test') as client:
                body = named_request()
                body['stream'] = stream
                response = await client.post('/codebuddy/v1/chat/completions', json=body)
                assert response.status_code == 200
                if stream:
                    assert 'report_status' in response.text
                    assert '[DONE]' in response.text
                else:
                    call = response.json()['choices'][0]['message']['tool_calls'][0]
                    assert call['function']['name'] == 'report_status'
                    assert json.loads(call['function']['arguments']) == {'status': 'ok'}
    asyncio.run(exercise())
    assert len(seen) == 1
    assert logs.list_requests()['items'][0]['upstream_attempts'] == 1
