from fastapi.testclient import TestClient

from shengjing_worker_kit import BackendError, create_app


class Fake:
    name = "fake"

    def __init__(self):
        self.models = {}

    def load(self, model, source, options):
        if source == "bad":
            raise BackendError("加载失败")
        self.models[model] = source

    def unload(self, model):
        self.models.pop(model, None)

    def loaded(self):
        return sorted(self.models)

    def run(self, op, model, params):
        if op == "boom":
            raise RuntimeError("unexpected")
        return {"op": op, "model": model, "params": params}

    def memory(self):
        return {"active_gb": 1.0}


H = {"Authorization": "Bearer t"}


def test_protocol_roundtrip():
    c = TestClient(create_app(Fake(), "t"))
    assert c.get("/health").status_code == 401
    assert c.post("/load", json={"model": "m", "source": "s"}, headers=H).json()["ok"]
    health = c.get("/health", headers=H).json()
    assert health["loaded"] == ["m"] and health["memory"] == {"active_gb": 1.0}
    r = c.post("/run", json={"op": "x", "model": "m", "params": {"a": 1}}, headers=H).json()
    assert r == {"ok": True, "result": {"op": "x", "model": "m", "params": {"a": 1}}}
    assert c.post("/unload", json={"model": "m"}, headers=H).json()["ok"]
    assert c.get("/health", headers=H).json()["loaded"] == []


def test_errors_are_structured():
    c = TestClient(create_app(Fake(), "t"))
    r = c.post("/load", json={"model": "m", "source": "bad"}, headers=H)
    assert r.status_code == 400 and r.json()["error"]["message"] == "加载失败"
    r = c.post("/run", json={"op": "boom", "model": "m"}, headers=H)
    assert r.status_code == 500 and r.json()["error"]["type"] == "RuntimeError"
