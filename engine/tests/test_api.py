import time

from fastapi.testclient import TestClient

from shengjing_engine.api.app import create_app
from shengjing_engine.audio.ffmpeg import write_wav

from .conftest import tone

AUTH = {"Authorization": "Bearer test-token"}


def client(services) -> TestClient:
    return TestClient(create_app(services, run_background=False))


def test_requires_token(services):
    with client(services) as c:
        assert c.get("/health").status_code == 200
        assert c.get("/system").status_code == 401
        assert c.get("/system", headers={"Authorization": "Bearer nope"}).status_code == 401
        assert c.get("/system?token=test-token").status_code == 200


def test_catalog_respects_edition(services):
    with client(services) as c:
        models = {m["id"]: m for m in c.get("/catalog", headers=AUTH).json()}
        assert models["fish-s2-pro"]["allowed"] and models["fish-s2-pro"]["default"]
        c.put("/settings", json={"edition": "commercial"}, headers=AUTH)
        models = {m["id"]: m for m in c.get("/catalog", headers=AUTH).json()}
        assert not models["fish-s2-pro"]["allowed"]
        assert models["dots-tts-soar"]["default"]
        assert not models["breeze-tts-2"]["allowed"]


def test_settings_and_secrets(services):
    with client(services) as c:
        patch = {"llm": {"provider": "cloud", "cloud_model": "x"}}
        r = c.put("/settings", json=patch, headers=AUTH)
        assert r.json()["settings"]["llm"]["provider"] == "cloud"
        assert r.json()["settings"]["llm"]["temperature"] == 0.7  # 未修改的字段保留
        assert c.put("/settings", json={"edition": "pirate"}, headers=AUTH).status_code == 422
        assert c.put("/secrets/llm_api_key", json={"value": "sk"}, headers=AUTH).json() == {
            "llm_api_key": True
        }
        assert c.get("/settings", headers=AUTH).json()["secrets"]["llm_api_key"] is True
        assert c.put("/secrets/other", json={"value": "x"}, headers=AUTH).status_code == 404


def test_voice_requires_consent_and_queues_transcription(services, tmp_path):
    ref = write_wav(tmp_path / "me.wav", tone(1.0), 24000)
    with client(services) as c:
        body = {"name": "我的声音", "audio_path": str(ref), "consent": {"confirmed": False}}
        assert c.post("/voices", json=body, headers=AUTH).status_code == 400
        body["consent"]["confirmed"] = True
        r = c.post("/voices", json=body, headers=AUTH).json()
        assert r["voice"]["consent"]["confirmed"] is True
        assert r["transcribe_task"]["kind"] == "voice.transcribe"
        assert len(c.get("/voices", headers=AUTH).json()) == 1


def test_files_endpoint_is_confined_to_data_dir(services, tmp_path):
    inside = write_wav(services.paths.outputs / "x.wav", tone(0.2), 24000)
    outside = write_wav(tmp_path / "outside.wav", tone(0.2), 24000)
    with client(services) as c:
        assert c.get(f"/files?path={inside}&token=test-token").status_code == 200
        assert c.get(f"/files?path={outside}&token=test-token").status_code == 404


def test_tasks_submit_and_cancel(services):
    with client(services) as c:
        assert c.post("/tasks", json={"kind": "nope"}, headers=AUTH).status_code == 404
        t = c.post("/tasks", json={"kind": "tts.quick", "params": {"text": "hi"}}, headers=AUTH)
        tid = t.json()["id"]
        assert c.post(f"/tasks/{tid}/cancel", headers=AUTH).json()["status"] == "cancelled"


def test_project_script_validation(services):
    with client(services) as c:
        bad = {"title": "t", "script": {"title": "t", "cast": [], "lines": [
            {"id": "l1", "speaker": "x", "text": "hi"}]}}
        assert c.post("/projects", json=bad, headers=AUTH).status_code == 422


def test_events_websocket_requires_token(services):
    with client(services) as c:
        with c.websocket_connect("/events?token=test-token") as ws:
            c.post("/tasks", json={"kind": "tts.quick", "params": {"text": "hi"}}, headers=AUTH)
            event = ws.receive_json()
            assert event["type"] == "task" and event["task"]["kind"] == "tts.quick"


def test_events_websocket_cleans_up_on_disconnect(services):
    # 用真实的 uvicorn 服务：窗口关掉后不能留下订阅，否则引擎退出时会一直等这条连接
    import socket
    import threading

    import uvicorn
    from websockets.sync.client import connect

    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    app = create_app(services, run_background=False)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    try:
        for _ in range(200):
            if server.started:
                break
            time.sleep(0.02)
        with connect(f"ws://127.0.0.1:{port}/events?token=test-token"):
            for _ in range(100):
                if services.events._subscribers:
                    break
                time.sleep(0.01)
            assert len(services.events._subscribers) == 1
        for _ in range(200):
            if not services.events._subscribers:
                break
            time.sleep(0.01)
        assert not services.events._subscribers
    finally:
        server.should_exit = True
        thread.join(timeout=5)
    assert not thread.is_alive()