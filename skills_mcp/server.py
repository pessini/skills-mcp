"""Build an immutable skill catalog before accepting client requests."""

import json
import logging
import os
import subprocess
from collections.abc import Generator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from fastmcp import Context, FastMCP, Skill
from fastmcp.exceptions import NotFoundError, ResourceError, ToolError
from fastmcp.server.middleware import Middleware
from fastmcp.skills.provider import SkillsDirectoryProvider
from starlette.requests import Request
from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)
PYTHON_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SKILLS_ROOT = PYTHON_ROOT / "skills"
EXCLUDED_DIRECTORIES = {"tests", "__pycache__", "node_modules", "venv", "dist", "build"}
PUBLISHED_SUFFIXES = {".md", ".txt", ".yaml", ".yml", ".json"}


def catalog_lines(descriptions: dict[str, str]) -> str:
    return "\n".join(
        f"- {name}: {description}" for name, description in sorted(descriptions.items())
    )


def publishable_files(folder: Path) -> dict[str, bytes]:
    """Read documentation and metadata, refusing links and special files."""
    files: dict[str, bytes] = {}

    def visit(directory: Path) -> None:
        for path in sorted(directory.iterdir()):
            if path.is_symlink():
                raise ValueError(f"{path}: symlinks are not publishable")
            if not path.is_dir() and not path.is_file():
                raise ValueError(f"{path}: expected a regular file or directory")
            if path.name.startswith("."):
                continue
            if path.is_dir():
                if path.name not in EXCLUDED_DIRECTORIES:
                    visit(path)
            elif path.suffix.lower() in PUBLISHED_SUFFIXES or path.name in {
                "LICENSE",
                "NOTICE",
            }:
                files[path.relative_to(folder).as_posix()] = path.read_bytes()

    visit(folder)
    return files


def source_revision(root: Path) -> dict[str, str | bool | None]:
    """Describe Git state without claiming a dirty tree is an immutable release."""

    def git(*arguments: str) -> str:
        return subprocess.check_output(
            ["git", "-C", str(root), *arguments],
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        ).strip()

    try:
        return {
            "commit": git("rev-parse", "HEAD"),
            "dirty": bool(git("status", "--porcelain", "--untracked-files=all")),
        }
    except (OSError, subprocess.SubprocessError):
        return {"commit": None, "dirty": None}


async def read_text(ctx: Context, uri: str) -> str:
    """Read one published text resource through the server's middleware chain."""
    try:
        result = await ctx.read_resource(uri)
    except (NotFoundError, ResourceError) as exc:
        raise ToolError(f"Cannot read {uri}: {exc}") from exc
    content = result.contents[0].content
    return content.decode("utf-8") if isinstance(content, bytes) else content


class ReadLogging(Middleware):
    async def on_read_resource(self, context, call_next):
        logger.info("operation=resources/read uri=%s", context.message.uri)
        return await call_next(context)

    async def on_call_tool(self, context, call_next):
        arguments = context.message.arguments or {}
        logger.info(
            "operation=tools/call tool=%s name=%s uri=%s",
            context.message.name,
            arguments.get("name", ""),
            arguments.get("uri", ""),
        )
        return await call_next(context)


@contextmanager
def create_server(skills_root: Path | None = None) -> Generator[FastMCP]:
    """Retain the temporary canonical tree until the server stops."""
    root = (
        (
            skills_root
            if skills_root is not None
            else Path(os.environ.get("SKILLS_ROOT", DEFAULT_SKILLS_ROOT))
        )
        .expanduser()
        .absolute()
    )
    if any(path.is_symlink() for path in (root, *root.parents)) or not root.is_dir():
        raise ValueError(
            f"{root}: skills root must be an existing directory without symlinks"
        )
    mapping: dict[str, str] = {}
    descriptions: dict[str, str] = {}
    with TemporaryDirectory(prefix="skills-mcp-") as temporary:
        canonical = Path(temporary)
        for folder in sorted(root.iterdir()):
            if folder.is_symlink():
                raise ValueError(f"{folder}: symlinks are not publishable")
            if folder.name.startswith("."):
                continue
            if not folder.is_dir():
                raise ValueError(f"{folder}: expected a skill directory")
            try:
                files = publishable_files(folder)
                skill = Skill.from_files(files)
            except (OSError, ValueError) as exc:
                raise ValueError(f"{folder}: {exc}") from exc
            if skill.name in mapping.values():
                raise ValueError(f"{folder}: duplicate canonical name {skill.name!r}")
            mapping[folder.name] = skill.name
            descriptions[skill.name] = skill.description
            target = canonical / skill.name
            for relative, content in files.items():
                destination = target / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(content)
        if not mapping:
            raise ValueError(f"{root}: no skill directories found")
        server = FastMCP(
            "Skills MCP",
            instructions="Before a task matching a catalog skill, call load_skill(name) "
            "and follow its instructions. Available skills: "
            + ", ".join(sorted(descriptions)),
        )
        server.add_provider(SkillsDirectoryProvider(canonical))
        server.add_middleware(ReadLogging())
        info = {
            "skill_count": len(mapping),
            "source_folders": mapping,
            "source_revision": source_revision(root),
        }

        @server.resource("catalog://info", mime_type="application/json")
        async def catalog_info() -> str:
            """Catalog snapshot metadata."""
            return json.dumps(info)

        def register_notice(filename: str, content: bytes) -> None:
            @server.resource(f"catalog://licenses/{filename}", mime_type="text/plain")
            async def notice() -> str:
                """Repository license notice preserved with the skill catalog."""
                return content.decode("utf-8")

        licenses = root.parent / "LICENSES"
        if licenses.is_symlink():
            raise ValueError(f"{licenses}: symlinks are not publishable")
        if licenses.is_dir():
            for filename, content in publishable_files(licenses).items():
                register_notice(filename, content)

        @server.custom_route("/healthz", methods=["GET"])
        async def healthz(request: Request) -> JSONResponse:
            return JSONResponse({"status": "ok", "skill_count": len(mapping)})

        # Explicit tools rather than the ResourcesAsTools transform: FastMCP 4.2.1's
        # Skills extension serves no skills while any server transform is present.
        # Both tools read through the server, so resource middleware, unknown-URI
        # and traversal rejection apply unchanged.
        skill_names = sorted(mapping.values())

        @server.tool(
            description="Load a catalog skill's SKILL.md instructions by name. Call it "
            "before starting a task that matches one of these skills:\n"
            + catalog_lines(descriptions)
        )
        async def load_skill(name: str, ctx: Context) -> str:
            if name not in skill_names:
                raise ToolError(
                    f"Unknown skill {name!r}. Known skills: {', '.join(skill_names)}"
                )
            return await read_text(ctx, f"skill://{name}/SKILL.md")

        @server.tool
        async def read_skill_file(uri: str, ctx: Context) -> str:
            """Read a skill supporting file or catalog resource by URI, for example
            skill://example/references/handover.md or catalog://info.
            Resolve relative references against the current file's directory."""
            return await read_text(ctx, uri)

        logger.info(
            "catalog_loaded root=%s skill_count=%d revision=%s",
            root,
            len(mapping),
            info["source_revision"],
        )
        logger.info("source_folders=%s", json.dumps(mapping, sort_keys=True))
        yield server
