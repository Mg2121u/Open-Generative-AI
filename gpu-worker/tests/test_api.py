import os
import io
import time
os.environ["ENVIRONMENT"]="test"
os.environ["MOCK_INFERENCE"]="1"
os.environ["WORKER_API_TOKEN"]="a_test_token_longer_than_32_bytes_123456"
os.environ["S3_BUCKET"]="test"
os.environ["S3_ENDPOINT"]="https://example.test"
os.environ["S3_ACCESS_KEY"]="test"
os.environ["S3_SECRET_KEY"]="test"

import app as worker
from fastapi.testclient import TestClient
from PIL import Image

class FakeS3:
    def __init__(self): self.data={}
    def put_object(self, Bucket, Key, Body, **kwargs): self.data[Key]=Body
    def get_object(self, Bucket, Key): return {"Body":io.BytesIO(self.data[Key])}
    def generate_presigned_url(self, operation, Params, ExpiresIn): return "https://example.test/"+Params["Key"]

def test_pilot_flow():
    worker.s3=FakeS3()
    worker.model="mock"
    worker.ready.set()
    with TestClient(worker.app) as client:
        assert client.get("/healthz").status_code==200
        assert client.get("/readyz").status_code==200
        assert client.post("/jobs",json={"character":"Aria","prompt":"Studio portrait"}).status_code==401
        headers={"Authorization":"Bearer a_test_token_longer_than_32_bytes_123456"}
        r=client.post("/jobs",headers=headers,json={"character":"Aria","prompt":"Studio portrait"})
        assert r.status_code==202, r.text
        for _ in range(100):
            data=client.get("/jobs/"+r.json()["id"],headers=headers).json()
            if data["status"]=="COMPLETED": break
            time.sleep(.05)
        assert data["status"]=="COMPLETED"
        assert data["assets"][0].startswith("https://example.test/")
        assert client.post("/jobs",headers=headers,json={"character":"Nicole","prompt":"portrait","mode":"video"}).status_code==422

def test_ephemeral_output_signed_url(tmp_path):
    from local_store import EphemeralStore
    worker.s3=EphemeralStore(tmp_path)
    worker.model="mock"
    worker.ready.set()
    token={"Authorization":"Bearer a_test_token_longer_than_32_bytes_123456"}
    with TestClient(worker.app) as client:
        # startup may run concurrently: assign the test store after startup begins.
        worker.s3=EphemeralStore(tmp_path)
        worker.model="mock"
        worker.ready.set()
        created=client.post("/jobs",headers=token,json={"character":"Nicole","prompt":"A sunrise portrait"})
        assert created.status_code==202,created.text
        job_id=created.json()["id"]
        for _ in range(100):
            result=client.get("/jobs/"+job_id,headers=token).json()
            if result["status"] in ("COMPLETED","ERROR"): break
            time.sleep(.05)
        assert result["status"]=="COMPLETED",result
        signed=result["assets"][0]
        assert signed.startswith("https://")
        from urllib.parse import urlsplit
        path=urlsplit(signed)
        resp=client.get(path.path+"?"+path.query)
        assert resp.status_code==200,resp.text
        assert resp.headers["content-type"]=="image/png"
        assert resp.content.startswith(b"\x89PNG")
        assert client.get(path.path+"?expires=100&sig="+("0"*64)).status_code==403

def test_salad_portal_legacy_environment_names(monkeypatch):
    """Salad's UI transformed WORKER_API_TOKEN to per-letter underscores."""
    for normal in ("WORKER_API_TOKEN", "STORAGE_DRIVER", "ENVIRONMENT", "MAX_REPLICAS"):
        monkeypatch.delenv(normal, raising=False)
    monkeypatch.setenv("_w_o_r_k_e_r__a_p_i__t_o_k_e_n", "test-secret-that-has-40-characters-1234567890")
    monkeypatch.setenv("_s_t_o_r_a_g_e__d_r_i_v_e_r", "ephemeral")
    monkeypatch.setenv("_e_n_v_i_r_o_n_m_e_n_t", "production")
    applied = worker.normalize_salad_environment()
    assert "WORKER_API_TOKEN" in applied
    assert os.environ["WORKER_API_TOKEN"] == "test-secret-that-has-40-characters-1234567890"
    assert os.environ["STORAGE_DRIVER"] == "ephemeral"
    assert os.environ["ENVIRONMENT"] == "production"
    assert os.environ.get("MAX_REPLICAS") is None
    from fastapi import HTTPException
    worker.authorize("Bearer test-secret-that-has-40-characters-1234567890")
    try:
        worker.authorize("Bearer invalid")
        assert False, "Expected 401 for bad credential"
    except HTTPException as exc:
        assert exc.status_code == 401


def test_canonical_salad_env_name_preferred(monkeypatch):
    monkeypatch.setenv("STORAGE_DRIVER", "ephemeral")
    monkeypatch.setenv("_s_t_o_r_a_g_e__d_r_i_v_e_r", "wrong")
    applied = worker.normalize_salad_environment()
    assert "STORAGE_DRIVER" not in applied
    assert os.environ["STORAGE_DRIVER"] == "ephemeral"

def test_inline_reference_validation():
    from PIL import Image
    import base64
    img = Image.new("RGB", (48, 48), (200, 160, 130))
    dest = io.BytesIO()
    img.save(dest, format="JPEG")
    encoded = "data:image/jpeg;base64," + base64.b64encode(dest.getvalue()).decode()
    reference = worker.decode_private_reference(encoded)
    assert reference.size == (48, 48)
    assert worker.reference_role_order(worker.Job(character="Nicole", prompt="Portrait", reference_images={"identity": encoded}))[0][0] == "identity"
    with TestClient(worker.app) as client:
        headers = {"Authorization": "Bearer a_test_token_longer_than_32_bytes_123456"}
        worker.ready.set()
        worker.s3 = FakeS3()
        worker.model = "mock"
        payload = {"character": "Nicole", "prompt": "Casual portrait", "reference_images": {"identity": encoded}}
        assert client.post("/jobs", headers=headers, json=payload).status_code == 202
        bad = {**payload, "reference_images": {"identity": "http://127.0.0.1/private"}}
        assert client.post("/jobs", headers=headers, json=bad).status_code == 422
        bad["reference_images"] = {"unknown": encoded}
        assert client.post("/jobs", headers=headers, json=bad).status_code == 422
        bad["reference_images"] = {"identity": "data:image/jpeg;base64," + "A" * 1_850_001}
        assert client.post("/jobs", headers=headers, json=bad).status_code == 422


def test_reference_roles_order_independent_of_client_dict_order():
    from PIL import Image
    import base64
    image = Image.new("RGB", (8, 8), "white")
    stream = io.BytesIO()
    image.save(stream, format="JPEG")
    encoded = "data:image/jpeg;base64," + base64.b64encode(stream.getvalue()).decode()
    payload = worker.Job(character="Aria", prompt="A portrait", reference_images={"scenery": encoded, "identity": encoded})
    assert [role for role, _ in worker.reference_role_order(payload)] == ["identity", "scenery"]
