import json

import httpx
import pytest

from shengjing_engine.workflows.music import AceStepClient, AceStepError


def fake_server(results):
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer k"
        if request.url.path == "/release_task":
            body = json.loads(request.content)
            assert body["prompt"] == "lofi"
            return httpx.Response(200, json={"code": 200, "error": None,
                                             "data": {"task_id": "t1", "status": "queued"}})
        if request.url.path == "/query_result":
            entry = results.pop(0)
            return httpx.Response(200, json={"code": 200, "error": None, "data": [entry]})
        if request.url.path == "/v1/audio":
            assert request.url.params["path"] == "/tmp/api_audio/a.wav"
            return httpx.Response(200, content=b"RIFF")
        return httpx.Response(404)

    return httpx.MockTransport(handler), calls


async def test_submit_poll_download(tmp_path):
    done = {"task_id": "t1", "status": 1,
            "result": json.dumps([{"file": "/v1/audio?path=%2Ftmp%2Fapi_audio%2Fa.wav"}])}
    transport, calls = fake_server([{"task_id": "t1", "status": 0}, done])
    client = AceStepClient("http://acestep", api_key="k", transport=transport)
    task_id = await client.release_task({"prompt": "lofi"})
    files = await client.wait(task_id, poll=0)
    out = await client.download(files[0], tmp_path / "a.wav")
    assert out.read_bytes() == b"RIFF"
    assert [c.url.path for c in calls].count("/query_result") == 2
    await client.aclose()


async def test_failed_task_raises():
    transport, _ = fake_server([{"task_id": "t1", "status": 2, "result": "oom"}])
    client = AceStepClient("http://acestep", api_key="k", transport=transport)
    with pytest.raises(AceStepError, match="生成失败"):
        await client.wait("t1", poll=0)
    await client.aclose()
