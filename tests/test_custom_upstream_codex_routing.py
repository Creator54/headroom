"""Exercise both handler URL-selection blocks without opening upstream connections."""

import ast
from pathlib import Path
from types import SimpleNamespace

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "headroom/proxy/handlers/openai.py"


def routing_block(method: str) -> ast.Module:
    tree = ast.parse(SOURCE.read_text())
    handler = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.AsyncFunctionDef) and node.name == method
    )
    # Execute the production statements, not a reimplementation of the routing rule.
    output = "upstream_url" if method == "handle_openai_responses_ws" else "http_url"
    branch = next(
        i
        for i, node in enumerate(handler.body)
        if isinstance(node, ast.If)
        and any(
            isinstance(t, ast.Name) and t.id == output
            for statement in ast.walk(node)
            if isinstance(statement, ast.Assign)
            for t in statement.targets
        )
    )
    start = next(
        (
            i
            for i, node in enumerate(handler.body[:branch])
            if isinstance(node, ast.Assign)
            and any(isinstance(t, ast.Name) and t.id == "_raw_base" for t in node.targets)
        ),
        branch,
    )
    return ast.Module(body=handler.body[start : branch + 1], type_ignores=[])


@pytest.mark.parametrize(
    "method,output,websocket",
    [
        ("handle_openai_responses_ws", "upstream_url", True),
        ("_ws_http_fallback", "http_url", False),
    ],
)
@pytest.mark.parametrize("chatgpt", [False, True])
@pytest.mark.parametrize(
    "base",
    [
        "https://api.openai.com",
        "https://api.openai.com/v1/",
        "http://hub:10100",
        "http://hub:10100/v1/",
        "https://gateway.example/api/v1",
    ],
)
def test_custom_upstream_wins_over_chatgpt_hint(method, output, websocket, chatgpt, base):
    def build_url(root, path):
        return root.rstrip("/").removesuffix("/v1") + path

    namespace = {
        "self": SimpleNamespace(OPENAI_API_URL=base),
        "logger": SimpleNamespace(debug=lambda message: None),
        "request_id": "synthetic-routing-test",
        "is_chatgpt_auth": chatgpt,
        "is_chatgpt_fallback": chatgpt,
        "codex_responses_websocket_url": lambda: "wss://chatgpt.com/backend-api/codex/responses",
        "codex_responses_http_url": lambda: "https://chatgpt.com/backend-api/codex/responses",
        "build_copilot_upstream_url": build_url,
    }
    exec(compile(routing_block(method), str(SOURCE), "exec"), namespace)
    normalized = base.rstrip("/").removesuffix("/v1")
    if chatgpt and normalized == "https://api.openai.com":
        expected = "https://chatgpt.com/backend-api/codex/responses"
    else:
        expected = normalized + "/v1/responses"
    if websocket:
        expected = expected.replace("https://", "wss://").replace("http://", "ws://")
    assert namespace[output] == expected
