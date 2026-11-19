"""Read-only SaladCloud connection check for GitHub Actions.

Never prints API keys or raw response bodies, never launches billable GPU resources.
"""
from __future__ import annotations
import json
import os
import sys
from urllib.error import HTTPError, URLError
from urllib.request import HTTPSHandler, HTTPRedirectHandler, Request, build_opener

API_ROOT = "https://api.salad.com/api/public"
ORGANIZATION = "mgg1981"
PROJECT = "aria-nicole-studio"

class NoRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        raise RuntimeError("Unexpected Salad API redirect; refusing to forward credentials")

def api_get(path: str, key: str) -> tuple[int, dict]:
    req = Request(API_ROOT + path,
        headers={"Salad-Api-Key": key, "Accept": "application/json",
                 "User-Agent": "aria-nicole-studio-preflight/1.0"},
        method="GET")
    try:
        with build_opener(HTTPSHandler(), NoRedirects()).open(req, timeout=20) as response:
            body = response.read(1_000_000)
            try:
                data = json.loads(body.decode("utf-8")) if body else {}
            except (ValueError, UnicodeDecodeError):
                data = {}
            return response.status, data if isinstance(data, dict) else {}
    except HTTPError as error:
        return error.code, {}
    except URLError as error:
        raise RuntimeError("Unable to reach Salad API over HTTPS") from error

def summary(lines: list[str]) -> None:
    filepath = os.getenv("GITHUB_STEP_SUMMARY")
    if filepath:
        with open(filepath, "a", encoding="utf-8") as stream:
            stream.write("\n".join(lines) + "\n")

def main() -> int:
    key = os.getenv("SALAD_API_KEY", "").strip()
    if not key:
        print("BLOCKED: SALAD_API_KEY is missing from GitHub Actions secrets.")
        summary(["## SaladCloud connection blocked", "Add the SALAD_API_KEY repository Actions secret."])
        return 2
    # Use two independently documented, read-only public-API routes.
    # The older fallback to /v1/organizations was not a valid endpoint.
    endpoints = [
        ("organization quotas", f"/organizations/{ORGANIZATION}/quotas"),
        ("GPU class catalog", f"/organizations/{ORGANIZATION}/gpu-classes"),
    ]
    statuses = {}
    for label, path in endpoints:
        try:
            code, _ = api_get(path, key)
        except RuntimeError as exc:
            print(f"Connectivity problem at {label}: {exc}")
            return 3
        statuses[label] = code
        print(f"Public API {label} returned HTTP {code}.")
    if any(code == 200 for code in statuses.values()):
        print("Salad public API credentials verified against a real organization endpoint.")
    else:
        # Only print non-sensitive shape diagnostics. Never print any portion
        # of the secret, its exact length, or an identifying checksum.
        raw_key = os.getenv("SALAD_API_KEY", "")
        wrapped = raw_key.startswith(("Bearer ", "Salad-Api-Key:", "'", '"'))
        looks_like_placeholder = raw_key.lower() in {
            "your-api-key", "your_api_key", "api_key", "paste_your_api_key_here"
        }
        print("Secret is set:", bool(raw_key))
        print("Secret is at least 20 characters:", len(raw_key.strip()) >= 20)
        print("Secret appears to include a header prefix or quote wrapper:", wrapped)
        print("Secret resembles a placeholder:", looks_like_placeholder)
        if all(c == 401 for c in statuses.values()):
            reason = "Both documented API endpoints rejected the saved credentials (HTTP 401)"
        elif any(c == 403 for c in statuses.values()):
            reason = "Credentials may lack permission for organization mgg1981 (HTTP 403)"
        elif any(c == 404 for c in statuses.values()):
            reason = "Organization name or endpoint accessibility needs review (HTTP 404)"
        else:
            reason = "Unexpected API responses: " + str(statuses)
        print("BLOCKED:", reason)
        summary([
            "## SaladCloud read-only verification blocked",
            "- Organization: mgg1981",
            "- " + reason,
            "- No GPU instances created, started, or billed",
            "- The API key itself was never printed",
        ])
        return 4

    print(f"Salad API authentication succeeded for organization {ORGANIZATION}.")
    try:
        status, groups = api_get(
            f"/organizations/{ORGANIZATION}/projects/{PROJECT}/containers", key)
    except RuntimeError as exc:
        print(f"Project probe failed: {exc}")
        return 5
    if status == 200:
        print(f"Project {PROJECT} is accessible; container inventory queried read-only.")
        project_status = "Project accessible"
        project_ok = "true"
        existing = groups.get("items", groups.get("container_groups", []))
        if isinstance(existing, list):
            print(f"Existing container group count: {len(existing)}")
    elif status == 404:
        print(f"Project {PROJECT} is not yet accessible; create it in Salad Portal.")
        project_status = "Project missing or inaccessible"
        project_ok = "false"
    else:
        print(f"Project probe returned HTTP {status}. No compute resources touched.")
        project_status = f"Project probe HTTP {status}"
        project_ok = "false"

    output = os.getenv("GITHUB_OUTPUT")
    if output:
        with open(output, "a", encoding="utf-8") as stream:
            stream.write(f"salad_authenticated=true\nproject_accessible={project_ok}\n")
    summary([
        "## SaladCloud connection verified (read-only)",
        f"- Organization: {ORGANIZATION}",
        "- API key: authenticated; never printed",
        f"- Project: {PROJECT} — {project_status}",
        "- GPU instances created or started: 0",
        "- Chargeable generation requests submitted: 0",
        "- Prepaid balance: not checked",
        "- Next: private storage, container build, stopped container group, secure Vercel preview."
    ])
    return 0 if status in (200, 404) else 6

if __name__ == "__main__":
    sys.exit(main())
