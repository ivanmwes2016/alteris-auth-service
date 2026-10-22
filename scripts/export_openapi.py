"""
Write the API spec to docs/alteris_auth_service.json, ready to import into Postman (or any
OpenAPI tool). Run with `make openapi` after changing endpoints.

FastAPI already describes every route; this adds what it can't know on its own:
which routes need a signed-in user (they read the Authorization header directly,
so FastAPI sees a plain header), and where the API runs.
"""

import json
import sys
from pathlib import Path
from typing import Any

from fastapi.dependencies.models import Dependant
from fastapi.routing import APIRoute

from app.api.v1.routes.auth import get_current_user, get_supabase_user
from app.main import app

OUTPUT = Path(__file__).resolve().parent.parent / "docs" / "alteris_auth_service.json"

SERVERS = [
    {"url": "http://localhost:8001", "description": "Local (make run)"},
    {"url": "https://alteris-auth-service.onrender.com", "description": "Production"},
]

AUTH_DEPENDENCIES = {get_current_user, get_supabase_user}


def _needs_sign_in(dependant: Dependant) -> bool:
    """True if the route, or anything it depends on, reads the caller's token."""
    if dependant.call in AUTH_DEPENDENCIES:
        return True
    if any(param.alias.lower() == "authorization" for param in dependant.header_params):
        return True
    return any(_needs_sign_in(sub) for sub in dependant.dependencies)


def build_spec() -> dict[str, Any]:
    spec = app.openapi()
    spec["info"]["description"] = (
        "Skolarly / Alteris API. Endpoints marked with a lock need a Supabase access "
        "token: `Authorization: Bearer <access_token>`. Get one from POST /auth/login."
    )
    spec["servers"] = SERVERS
    spec.setdefault("components", {})["securitySchemes"] = {
        "bearerAuth": {
            "type": "http",
            "scheme": "bearer",
            "bearerFormat": "JWT",
            "description": "Supabase access token (the access_token from POST /auth/login).",
        }
    }
    # Signed-in by default; public routes opt out below.
    spec["security"] = [{"bearerAuth": []}]

    for route in app.routes:
        if not isinstance(route, APIRoute):
            continue
        public = not _needs_sign_in(route.dependant)
        for method in route.methods:
            operation = spec["paths"].get(route.path_format, {}).get(method.lower())
            if operation is None:
                continue
            if public:
                operation["security"] = []
            # The token goes in via the security scheme, not as a loose header field.
            operation["parameters"] = [
                p
                for p in operation.get("parameters", [])
                if not (p.get("in") == "header" and p.get("name", "").lower() == "authorization")
            ]
            if not operation["parameters"]:
                del operation["parameters"]
    return spec


def main() -> None:
    spec = build_spec()
    OUTPUT.parent.mkdir(exist_ok=True)
    OUTPUT.write_text(json.dumps(spec, indent=2) + "\n")
    public = sorted(
        f"{method.upper()} {path}"
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if op.get("security") == []
    )
    total = sum(len(ops) for ops in spec["paths"].values())
    print(f"Wrote {OUTPUT.relative_to(Path.cwd())}: {total} operations, {len(public)} public:")
    for line in public:
        print("  ", line)


if __name__ == "__main__":
    sys.exit(main())
