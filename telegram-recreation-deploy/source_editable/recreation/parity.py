"""Run original frozen n8n JS transforms in Node without n8n.

Exact copy of provider-request prompt source and node transformation code is
stored in parity_assets.json. No uploaded or user-supplied JavaScript is executed.
"""
import json
import os
import subprocess
from pathlib import Path

RUNNER=Path(__file__).resolve().with_name('parity_runner.js')

class ParityError(RuntimeError):
    pass


def run(script, input=None, named=None):
    result=subprocess.run([os.getenv('NODE_BIN','node'),str(RUNNER)],
                          input=json.dumps({'script':script,'input':input or {},'named':named or {}}),
                          text=True,capture_output=True,timeout=25,check=False)
    try:
        response=json.loads(result.stdout)
    except ValueError as e:
        raise ParityError(f'JS compatibility execution failed: {result.stderr[-300:]}') from e
    if not result.returncode and response.get('ok'):
        return response['result']
    raise ParityError((response.get('error') or result.stderr)[-1400:])


def wan_payload(*, first: bool, prepared: dict, resolution='480p'):
    data=run('wan_initial_payload' if first else 'wan_next_payload', input=prepared)
    if data.get('resolution') != '720p':
        raise ParityError('Original Wan payload resolution unexpectedly changed')
    if resolution not in ('480p','720p'):
        raise ValueError('Unsupported resolution')
    # Deliberate one-field change requested by user, no prompt/seed alterations.
    data['resolution']=resolution
    return data
