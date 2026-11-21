import asyncio
import json
import os
import socket
import subprocess
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import pytest
from fastmcp import Client

from skills_mcp.server import PYTHON_ROOT


@contextmanager
def running_server(
    tmp_path: Path,
    root: Path | None = None,
    extra_env: dict[str, str] | None = None,
):
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    env = {
        k: v
        for k, v in os.environ.items()
        if k
        not in {
            "HOST",
            "PORT",
            "SKILLS_ROOT",
            "MCP_ALLOWED_HOSTS",
            "MCP_ALLOWED_ORIGINS",
            "MCP_BEARER_TOKEN",
        }
    }
    env.update(PORT=str(port), PYTHONPATH=str(PYTHON_ROOT), RUST_LOG="info")
    if root is not None:
        env["SKILLS_ROOT"] = str(root)
    env.update(extra_env or {})
    logfile = tmp_path / "server.log"
    with logfile.open("w") as log:
        process = subprocess.Popen(
            [sys.executable, "-m", "skills_mcp"],
            cwd=tmp_path,
            env=env,
            stdout=log,
            stderr=log,
        )
        try:
            deadline = time.monotonic() + 20
            while True:
                assert process.poll() is None, logfile.read_text()
                try:
                    with urlopen(
                        f"http://127.0.0.1:{port}/healthz", timeout=1
                    ) as response:
                        assert response.status == 200
                    break
                except URLError:
                    assert time.monotonic() < deadline, logfile.read_text()
                    time.sleep(0.05)
            yield f"http://127.0.0.1:{port}/mcp", logfile
        finally:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    root = tmp_path / "skills"
    skill = root / "fixture"
    (skill / "references").mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: fixture\ndescription: A fixture skill.\n---\nRead the guide.\n"
    )
    (skill / "references/guide.md").write_text("Fixture guide.\n")
    return root


def test_skills_and_references_over_stateless_http(tmp_path: Path, catalog: Path):
    async def check(url: str):
        async with Client(url) as client:
            tools = await client.list_tools()
            assert {tool.name for tool in tools} == {"load_skill", "read_skill_file"}
            skill = await client.call_tool("load_skill", {"name": "fixture"})
            assert skill.data == (catalog / "fixture/SKILL.md").read_text()
            uri = "skill://fixture/references/guide.md"
            reference = await client.call_tool("read_skill_file", {"uri": uri})
            assert (
                reference.data == (catalog / "fixture/references/guide.md").read_text()
            )

    with running_server(tmp_path, catalog) as (url, log):
        asyncio.run(check(url))
        assert "uri=skill://fixture/references/guide.md" in log.read_text()


def test_bearer_token_protects_mcp_and_preserves_health_check(
    tmp_path: Path, catalog: Path
):
    token = "fixture-token"

    async def check(url: str):
        async with Client(url, auth=token) as client:
            result = await client.call_tool("load_skill", {"name": "fixture"})
            assert "Read the guide." in result.data

    with running_server(tmp_path, catalog, {"MCP_BEARER_TOKEN": token}) as (url, log):
        for method in ("GET", "POST", "DELETE"):
            for headers in ({}, {"Authorization": "Bearer wrong-token"}):
                with pytest.raises(HTTPError) as rejected:
                    urlopen(Request(url, headers=headers, method=method), timeout=5)
                assert rejected.value.code == 401
                assert rejected.value.headers["WWW-Authenticate"] == "Bearer"
                rejected.value.close()
        asyncio.run(check(url))
        assert token not in log.read_text()


def test_empty_bearer_token_fails_at_startup(tmp_path: Path):
    env = {**os.environ, "PYTHONPATH": str(PYTHON_ROOT), "MCP_BEARER_TOKEN": ""}
    process = subprocess.run(
        [sys.executable, "-m", "skills_mcp"],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
    )
    assert process.returncode != 0
    assert "MCP_BEARER_TOKEN must be a non-empty bearer token" in process.stderr


def test_restart_publishes_edited_fixture_over_http(tmp_path: Path):
    root = tmp_path / "skills"
    folder = root / "sample"
    folder.mkdir(parents=True)
    entry = folder / "SKILL.md"
    original = "---\nname: sample\ndescription: Sample.\n---\nOriginal.\n"
    entry.write_text(original)

    async def read(url):
        async with Client(url) as client:
            return (await client.read_resource("skill://sample/SKILL.md"))[0].text

    with running_server(tmp_path, root) as (url, _):
        assert asyncio.run(read(url)) == original
        entry.write_text(original.replace("Original.", "Updated."))
        assert asyncio.run(read(url)) == original
    with running_server(tmp_path, root) as (url, _):
        assert "Updated." in asyncio.run(read(url))


@pytest.mark.parametrize("configured", [False, True])
def test_proxy_host_and_origin_allowlists(
    tmp_path: Path, configured: bool, catalog: Path
):
    settings = (
        {
            "MCP_ALLOWED_HOSTS": " skills.example.test , second.example.test ",
            "MCP_ALLOWED_ORIGINS": " https://client.example.test:443 ",
        }
        if configured
        else {}
    )
    body = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": "2025-11-25",
                "capabilities": {},
                "clientInfo": {"name": "proxy-acceptance", "version": "1"},
            },
        }
    ).encode()
    with running_server(tmp_path, catalog, settings) as (url, _):

        def status(host, origin=None):
            headers = {
                "Host": host,
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            }
            if origin is not None:
                headers["Origin"] = origin
            try:
                with urlopen(Request(url, body, headers), timeout=5) as response:
                    return response.status
            except HTTPError as error:
                error.close()
                return error.code

        assert status("untrusted.example.test") in {403, 421}
        if configured:
            assert status("skills.example.test") == 200
            assert (
                status("second.example.test", "https://client.example.test:443") == 200
            )
            assert (
                status("skills.example.test", "https://untrusted.example.test:443")
                == 403
            )
        else:
            assert status("skills.example.test") in {403, 421}
