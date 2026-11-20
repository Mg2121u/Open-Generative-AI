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
