"""Real ASGI run routes with temporary identity/storage and an offline model."""
from types import SimpleNamespace

from fastapi import FastAPI
import httpx

from api.agent_routes import create_agent_router
from tests.agent_fixtures import finish, runtime


async def test_scoped_create_replay_read_cancel_and_input_validation(tmp_path):
    engine, session, sessions = await runtime(tmp_path, lambda *_: finish())
    app = FastAPI()
    enabled=[True]
    app.include_router(create_agent_router(lambda: engine, lambda: SimpleNamespace(sessions=sessions),lambda:enabled[0]))
    # The browser normally receives a token from session creation. This test creates
    # another synthetic identity rather than recovering any stored raw credential.
    created = sessions.create("p_duplicate", "api-test")
    other = sessions.create("p_cancel", "api-test")
    headers = {"Authorization": "Bearer " + created["token"]}
    body = {"conv_id": created["conv_id"], "request_id": "stable", "message": "Read my payments only"}
    try:
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://offline") as client:
            assert (await client.post('/customer/agent/runs', json=body)).status_code == 401
            first = await client.post('/customer/agent/runs', headers=headers, json=body)
            assert first.status_code == 200
            run = first.json()["id"]
            replay = await client.post('/customer/agent/runs', headers=headers, json=body)
            assert replay.json()["id"] == run
            assert (await client.post('/customer/agent/runs', headers=headers, json={**body, "message": "Different"})).status_code == 409
            foreign = {"Authorization": "Bearer " + other["token"]}
            assert (await client.get(f'/customer/agent/runs/{run}', headers=foreign)).status_code == 404
            assert (await client.get(f'/customer/agent/runs/{run}/events', headers=foreign)).status_code == 404
            assert (await client.post(f'/customer/agent/runs/{run}/cancel', headers=foreign, json={})).status_code == 404
            # A fake approval property is rejected at schema validation, before any graph wakeup.
            assert (await client.post(f'/customer/agent/runs/{run}/input', headers=headers,
                    json={"request_id":"x", "version":0, "message":"approved", "approved":True})).status_code == 422
            await engine.drive(run)
            value = (await client.get(f'/customer/agent/runs/{run}', headers=headers)).json()
            assert value["status"] == "completed" and "token_hash" not in value
            events = (await client.get(f'/customer/agent/runs/{run}/events?after=0', headers=headers)).json()["items"]
            last = events[-1]["seq"]
            assert (await client.get(f'/customer/agent/runs/{run}/events?after={last}', headers=headers)).json()["items"] == []
            # Feature rollback blocks new work but preserves scoped saved results.
            enabled[0]=False
            assert (await client.get(f'/customer/agent/runs/{run}',headers=headers)).status_code==200
            assert (await client.post('/customer/agent/runs',headers=headers,json={**body,'request_id':'new'})).status_code==404
    finally:
        await engine.close()
