"""
Turn docs/alteris_auth_service.json (OpenAPI) into a ready-to-import Postman
collection, docs/alteris_auth_service.postman_collection.json. Run via `make openapi`.

Uses Postman's own converter (openapi-to-postmanv2, via npx), then:
- public endpoints get "No Auth" (the rest inherit the collection's Bearer token);
- POST /auth/login saves the access token into {{bearerToken}} for every request.
"""

import json
import re
import subprocess
from pathlib import Path
from typing import Any

DOCS = Path(__file__).resolve().parent.parent / "docs"
SPEC = DOCS / "alteris_auth_service.json"
COLLECTION = DOCS / "alteris_auth_service.postman_collection.json"

LOGIN_SCRIPT = [
    "// Saves the access token so every signed-in request can use it.",
    "if (pm.response.code === 200) {",
    "  pm.collectionVariables.set('bearerToken', pm.response.json().access_token);",
    "}",
]


def _requests(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for item in items:
        found.extend(_requests(item["item"]) if "item" in item else [item])
    return found


def main() -> None:
    # Fixed command and arguments (no user input); a local dev tool, not app code.
    subprocess.run(  # noqa: S603
        [  # noqa: S607
            "npx",
            "--yes",
            "openapi-to-postmanv2",
            "-s",
            str(SPEC),
            "-o",
            str(COLLECTION),
            "-p",
            "-O",
            "folderStrategy=Tags,requestParametersResolution=Example",
        ],
        check=True,
        capture_output=True,
    )

    spec = json.loads(SPEC.read_text())
    public = {
        (method.upper(), re.sub(r"\{(\w+)\}", r":\1", path))
        for path, ops in spec["paths"].items()
        for method, op in ops.items()
        if op.get("security") == []
    }

    collection = json.loads(COLLECTION.read_text())
    for item in _requests(collection["item"]):
        request = item["request"]
        key = (request["method"], "/" + "/".join(request["url"].get("path", [])))
        if key in public:
            request["auth"] = {"type": "noauth"}
        if key == ("POST", "/auth/login"):
            item["event"] = [
                {"listen": "test", "script": {"type": "text/javascript", "exec": LOGIN_SCRIPT}}
            ]

    variables = {v["key"]: v for v in collection.get("variable", [])}
    variables.setdefault("bearerToken", {"key": "bearerToken", "value": "", "type": "string"})
    collection["variable"] = list(variables.values())
    collection["info"]["name"] = "Skolarly API"
    collection["info"]["description"] = (
        "Set baseUrl to http://localhost:8001 (local) or "
        "https://alteris-auth-service.onrender.com (production). Send Auth → POST /auth/login "
        "first: it saves the access token for every other request (tokens last an hour)."
    )
    COLLECTION.write_text(json.dumps(collection, indent=2) + "\n")
    print(f"Wrote {COLLECTION.name}: {len(_requests(collection['item']))} requests")


if __name__ == "__main__":
    main()
