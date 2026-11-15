import json
from pathlib import Path

def test_manifest_parity():
    p=Path(__file__).parent.parent/'workflow_manifest.json'
    manifest=json.loads(p.read_text())
    assert manifest['node_count']==67
    assert manifest['workflow_name']=='RECREATION AGENT — WAN 3.0 REFERENCE SEGMENTS — IDENTITY LOCKED'
    assert 'WaveSpeed WAN 3.0 - Recreate Next Segment' in [x['name'] for x in manifest['nodes']]
    assert set(manifest['generation_resolution_deltas'])=={'WaveSpeed WAN 3.0 - Recreate Chunk','WaveSpeed WAN 3.0 - Recreate Next Segment'}
