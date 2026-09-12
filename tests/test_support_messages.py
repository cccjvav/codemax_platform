"""Private messaging: durable history, ownership, administrator permissions and retry safety."""
from uuid import uuid4

import pytest
from sqlalchemy import select

from app.models import SupportMessage, User
from tests.conftest import TestSession


async def identity(client, name, admin=False):
    assert (await client.post('/auth/register', json={'username': name, 'password': 'secret123'})).status_code == 201
    async with TestSession() as db:
        user = await db.scalar(select(User).where(User.username == name))
        uid = user.id
        if admin:
            user.role = 1
            await db.commit()
    result = await client.post('/auth/login', data={'username': name, 'password': 'secret123'})
    return uid, {'Authorization': 'Bearer ' + result.json()['access_token']}


def message(body='想咨询定制开发与数字产品'):
    return {'body': body, 'client_nonce': str(uuid4())}


@pytest.mark.asyncio
async def test_private_customer_admin_roundtrip(client):
    uid, customer = await identity(client, 'customer')
    _, admin = await identity(client, 'operator', True)
    r = await client.post('/support/messages', headers=customer, json=message())
    assert r.status_code == 200
    customer_id = r.json()['id']
    inbox = (await client.get('/support/conversations', headers=admin)).json()
    assert inbox[0]['customer_id'] == uid and inbox[0]['awaiting_admin']
    r = await client.post(f'/support/conversations/{uid}/messages', headers=admin, json=message('请说明需求和期望交付时间'))
    assert r.status_code == 200 and r.json()['sender_role'] == 1
    rows = (await client.get(f'/support/messages?after={customer_id}', headers=customer)).json()
    assert len(rows) == 1 and rows[0]['body'] == '请说明需求和期望交付时间'
    assert not (await client.get('/support/conversations', headers=admin)).json()[0]['awaiting_admin']


@pytest.mark.asyncio
async def test_customer_cannot_read_or_write_other_conversation(client):
    uid, one = await identity(client, 'first_user')
    _, two = await identity(client, 'second_user')
    await client.post('/support/messages', headers=one, json=message('private'))
    assert (await client.get('/support/messages', headers=two)).json() == []
    for path in ['/support/conversations', f'/support/conversations/{uid}/messages']:
        assert (await client.get(path, headers=two)).status_code == 403
    assert (await client.post(f'/support/conversations/{uid}/messages', headers=two, json=message())).status_code == 403


@pytest.mark.asyncio
async def test_guest_apis_require_login_but_shell_has_login_controls(client):
    assert (await client.get('/support/messages')).status_code == 401
    assert (await client.post('/support/messages', json=message())).status_code == 401
    assert (await client.get('/support/conversations')).status_code == 401
    r = await client.get('/support/center')
    assert r.status_code == 200 and 'support-page.js' in r.text and 'auth-form' in r.text


@pytest.mark.asyncio
async def test_retry_deduplicates_and_cannot_change_content(client):
    _, headers = await identity(client, 'retry_user')
    data = message('原消息')
    a = await client.post('/support/messages', headers=headers, json=data)
    b = await client.post('/support/messages', headers=headers, json=data)
    assert a.status_code == b.status_code == 200 and a.json()['id'] == b.json()['id']
    data['body'] = '其他内容'
    assert (await client.post('/support/messages', headers=headers, json=data)).status_code == 409
    assert len((await client.get('/support/messages', headers=headers)).json()) == 1


@pytest.mark.asyncio
async def test_bounded_cursor_history(client):
    uid, headers = await identity(client, 'history_user')
    async with TestSession() as db:
        db.add_all([SupportMessage(customer_id=uid, sender_id=uid, sender_role=0,
                                   body=str(i), client_nonce=str(uuid4())) for i in range(70)])
        await db.commit()
    recent = (await client.get('/support/messages', headers=headers)).json()
    assert len(recent) == 50 and recent[0]['body'] == '20' and recent[-1]['body'] == '69'
    older = (await client.get(f'/support/messages?before={recent[0]["id"]}', headers=headers)).json()
    assert len(older) == 20 and older[0]['body'] == '0'
    assert (await client.get('/support/messages?after=0&before=1', headers=headers)).status_code == 422
    assert (await client.get('/support/messages?after=-1', headers=headers)).status_code == 422


@pytest.mark.asyncio
async def test_message_validation_and_role_is_not_client_controlled(client):
    _, headers = await identity(client, 'validation_user')
    for body in [' ', '\x00', 'x' * 4001]:
        assert (await client.post('/support/messages', headers=headers, json=message(body))).status_code == 422
    r = await client.post('/support/messages', headers=headers, json={**message('<b>纯文本</b>'), 'sender_role': 1})
    assert r.status_code == 200 and r.json()['sender_role'] == 0


@pytest.mark.asyncio
async def test_admin_demotion_takes_effect(client):
    uid, admin = await identity(client, 'demoted', True)
    async with TestSession() as db:
        (await db.get(User, uid)).role = 0
        await db.commit()
    assert (await client.get('/support/conversations', headers=admin)).status_code == 403


@pytest.mark.asyncio
async def test_handoff_does_not_claim_message_was_sent(client):
    r = await client.post('/support/ask', json={'text': '我要人工客服'})
    assert r.status_code == 200
    assert r.json()['human_support_url'] == '/support/center'
    assert '已为你转接' not in r.json()['answer']
