"""Intercept the actual Anthropic SDK wire call, preserving native tool protocol."""
import json

from anthropic import AsyncAnthropic
import httpx

from agent_runtime.model import AnthropicModel
from agent_runtime.tools import schemas


async def test_native_tool_request_usage_and_no_hidden_sdk_retry():
    bodies = []
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200, json={"id":"msg-native", "type":"message", "role":"assistant", "model":"claude-sonnet-5-5",
             "content":[{"type":"tool_use", "id":"native-1", "name":"get_account", "input":{}}],
             "stop_reason":"tool_use", "usage":{"input_tokens":200,"output_tokens":30}})
    async with AsyncAnthropic(api_key="offline-test", base_url="http://model.test", max_retries=0,
                http_client=httpx.AsyncClient(transport=httpx.MockTransport(handler))) as client:
        model = AnthropicModel(client, "claude-sonnet-5-5")
        reply = await model.turn([{"role":"user","content":"Read account"}], schemas(), {})
        assert model.reservation([],schemas(),{}) > reply["cost_micro"]
        assert model.request([],schemas(),{},True)["tool_choice"] == {"type":"none"}
    assert len(bodies) == 1 and len(bodies[0]["tools"]) == 9
    assert "fallbacks" not in bodies[0]
    assert reply["content"][0]["id"] == "native-1" and reply["cost_micro"] == 700
