import asyncio
import json
from pathlib import Path

import pytest
from fastmcp import Client
from fastmcp.skills.client import SkillsClientExtension
from mcp.shared.exceptions import MCPError

from skills_mcp.server import create_server


def test_canonical_skill_and_nested_reference_are_readable(tmp_path: Path):
    source = tmp_path / "skills" / "legacy-folder"
    (source / "references").mkdir(parents=True)
    instructions = "---\nname: sample-skill\ndescription: A sample skill.\n---\nRead references/guide.md.\n"
    (source / "SKILL.md").write_text(instructions)
    (source / "references" / "guide.md").write_text("A nested reference.\n")

    async def check():
        with create_server(tmp_path / "skills") as server:
            async with Client(server, timeout=3) as client:
                resources = await client.list_resources()
                entries = [r for r in resources if str(r.uri).endswith("/SKILL.md")]
                assert [str(r.uri) for r in entries] == [
                    "skill://sample-skill/SKILL.md"
                ]
                assert entries[0].description == "A sample skill."
                result = await client.read_resource("skill://sample-skill/SKILL.md")
                assert result[0].text == instructions
                result = await client.read_resource(
                    "skill://sample-skill/references/guide.md"
                )
                assert result[0].text == "A nested reference.\n"
                assert {t.name for t in await client.list_tools()} == {
                    "load_skill",
                    "read_skill_file",
                }
                result = await client.call_tool("load_skill", {"name": "sample-skill"})
                assert result.data == instructions
                result = await client.call_tool(
                    "read_skill_file",
                    {"uri": "skill://sample-skill/references/guide.md"},
                )
                assert result.data == "A nested reference.\n"

    asyncio.run(check())


def test_skills_extension_lists_every_skill(tmp_path: Path):
    """Guard against FastMCP silently emptying skills/list (e.g. a transform)."""
    for name in ["alpha", "beta"]:
        folder = tmp_path / "skills" / name
        folder.mkdir(parents=True)
        (folder / "SKILL.md").write_text(
            f"---\nname: {name}\ndescription: The {name} skill.\n---\nBody.\n"
        )

    async def check():
        with create_server(tmp_path / "skills") as server:
            async with Client(
                server, extensions=[SkillsClientExtension()], timeout=3
            ) as client:
                skills = await client.list_skills()
                assert sorted(s.frontmatter.name for s in skills) == ["alpha", "beta"]
                assert {s.frontmatter.description for s in skills} == {
                    "The alpha skill.",
                    "The beta skill.",
                }

    asyncio.run(check())


@pytest.mark.parametrize(
    "problem",
    ["missing", "empty", "metadata", "duplicate", "symlink", "root-symlink", "fifo"],
)
def test_invalid_source_fails_before_serving(tmp_path: Path, problem: str):
    root = tmp_path / "skills"
    if problem != "missing":
        root.mkdir()
    if problem not in {"missing", "empty"}:
        skill = root / "sample"
        skill.mkdir()
        (skill / "SKILL.md").write_text(
            "---\nname: sample\ndescription: Sample.\n---\nBody.\n"
        )
        if problem == "metadata":
            (skill / "SKILL.md").write_text("No frontmatter.\n")
        elif problem == "duplicate":
            other = root / "alias"
            other.mkdir()
            (other / "SKILL.md").write_bytes((skill / "SKILL.md").read_bytes())
        elif problem == "symlink":
            (skill / "leak.md").symlink_to("/etc/passwd")
        elif problem == "root-symlink":
            alias = tmp_path / "alias"
            alias.symlink_to(root, target_is_directory=True)
            root = alias
        elif problem == "fifo":
            import os

            os.mkfifo(skill / "pipe.md")
    with pytest.raises(ValueError, match=str(root)), create_server(root):
        pytest.fail("Invalid source was published")


def test_snapshot_excludes_development_files_and_rejects_unlisted_paths(tmp_path: Path):
    root = tmp_path / "skills"
    skill = root / "sample"
    skill.mkdir(parents=True)
    (skill / "SKILL.md").write_text(
        "---\nname: sample\ndescription: Sample.\n---\nOriginal.\n"
    )
    for relative in [
        ".env",
        ".hidden/secret.md",
        "tests/fixture.md",
        "__pycache__/cache.pyc",
        "notes.bak",
    ]:
        file = skill / relative
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text("Unpublished.")

    async def check():
        with create_server(root) as server:
            (skill / "SKILL.md").write_text(
                "---\nname: sample\ndescription: Sample.\n---\nUpdated.\n"
            )
            async with Client(server, timeout=3) as client:
                resources = {str(r.uri) for r in await client.list_resources()}
                assert resources == {
                    "skill://sample/SKILL.md",
                    "skill://sample/_manifest",
                    "catalog://info",
                }
                assert (
                    "Original."
                    in (await client.read_resource("skill://sample/SKILL.md"))[0].text
                )
                for uri in [
                    "skill://unknown/SKILL.md",
                    "skill://sample/../../etc/passwd",
                    "skill://sample/%2e%2e/secret.md",
                    "file:///etc/passwd",
                    "skill://sample/.env",
                ]:
                    with pytest.raises(MCPError):
                        await client.read_resource(uri)
                    result = await client.call_tool(
                        "read_skill_file", {"uri": uri}, raise_on_error=False
                    )
                    assert result.is_error
                for name in ["unknown", "../sample", "sample/../../etc"]:
                    result = await client.call_tool(
                        "load_skill", {"name": name}, raise_on_error=False
                    )
                    assert result.is_error
                info = json.loads(
                    (await client.read_resource("catalog://info"))[0].text
                )
                assert info["skill_count"] == 1
                assert info["source_folders"] == {"sample": "sample"}
        with create_server(root) as restarted:
            async with Client(restarted) as client:
                assert (
                    "Updated."
                    in (await client.read_resource("skill://sample/SKILL.md"))[0].text
                )

    asyncio.run(check())


def test_discovery_describes_every_published_skill():
    """Advertised skills must match the catalog that clients can actually read."""

    async def check():
        with create_server() as server:
            async with Client(server, timeout=10) as client:
                instructions = client.instructions or ""
                resources = await client.list_resources()
                tool = next(
                    t for t in await client.list_tools() if t.name == "load_skill"
                )
                for resource in resources:
                    uri = str(resource.uri)
                    if not uri.endswith("/SKILL.md"):
                        continue
                    name = uri.removeprefix("skill://").removesuffix("/SKILL.md")
                    assert name in instructions
                    assert f"\n- {name}: {resource.description}" in tool.description

    asyncio.run(check())


@pytest.mark.parametrize(
    "description",
    [
        "Review code. Use when asked to inspect a diff; include security findings.",
        "Use this skill to review infrastructure changes involving "
        + "network access, permissions, deployment configuration, and state. " * 5
        + "Do not omit these final selection criteria.",
        "First line of guidance.\nSecond line; preserve this spacing:  exactly.",
    ],
)
def test_load_skill_preserves_complete_source_description(
    tmp_path: Path, description: str
):
    folder = tmp_path / "sample"
    folder.mkdir()
    (folder / "SKILL.md").write_text(
        "---\nname: sample\ndescription: " + json.dumps(description) + "\n---\nBody.\n"
    )

    async def check():
        with create_server(tmp_path) as server:
            async with Client(server, timeout=3) as client:
                tool = next(
                    t for t in await client.list_tools() if t.name == "load_skill"
                )
                assert f"\n- sample: {description}" in tool.description
                resource = next(
                    r
                    for r in await client.list_resources()
                    if str(r.uri) == "skill://sample/SKILL.md"
                )
                assert resource.description == description

    asyncio.run(check())
