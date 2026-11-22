# skills-mcp

Run your own server to share reusable task instructions with Codex, Claude Code,
and other tools that support the Model Context Protocol (MCP). Keep the
instructions in one repository so each connected tool can read the same files.

Each set of instructions is called a skill. A skill has a `SKILL.md` file and
can include examples or reference files. The server supplies those files. The
connected tool uses them to do the task. The server needs no database.

## Try the example locally

The included skill explains how to draft a workspace handover and provides a
sample note. In this test, a Python client reads both files from the server.

Clone the repository, or use **Code → Download ZIP** on GitHub and open the
extracted folder. To start your own repository, choose **Use this template**
on GitHub.

```sh
git clone https://github.com/pessini/skills-mcp.git
cd skills-mcp
```

From the project folder, use Python 3.12 or later and uv to install the
dependencies and start the server.

```sh
uv sync --locked
uv run --locked python -m skills_mcp
```

The server listens on `127.0.0.1:8000` by default. Open a second terminal in
the same folder and run the test.

```sh
uv run --locked python - <<'PY'
import asyncio

from fastmcp import Client


async def main():
    async with Client("http://127.0.0.1:8000/mcp") as client:
        skill = await client.call_tool("load_skill", {"name": "example"})
        print(skill.data)
        reference = await client.call_tool(
            "read_skill_file",
            {"uri": "skill://example/references/handover.md"},
        )
        print(reference.data)


asyncio.run(main())
PY
```

The output includes **Draft a workspace handover** and **Sample handover**.
You can also connect a local MCP client to `http://127.0.0.1:8000/mcp`.
Leave the authorization header empty if you started the server without
`MCP_BEARER_TOKEN`. Press Ctrl+C in the server terminal to stop it.

Run the project's automated tests with `uv run --locked python -m pytest`.

## Add your skills

Put each skill in its own folder under `skills/`.

```text
skills/
  my-skill/
    SKILL.md
    references/
      guide.md
```

Between the `---` lines at the top of each `SKILL.md`, set a unique `name` and
write a `description` that tells the client when to use the skill. Start with the
[example skill](skills/example/SKILL.md). You can remove it after adding your own
skill. Keep at least one valid skill in `skills/`.

The server publishes Markdown, plain text, YAML, and JSON files, plus files
named `LICENSE` or `NOTICE`. Keep credentials and private files outside
`skills/`.

The server reads the skill files at startup. Restart it to make edits available.
Set `SKILLS_ROOT` to load skills from a different folder.

## Deploy your own copy

Use the host of your choice. For access over the network, configure HTTPS,
set `MCP_BEARER_TOKEN` to a secret, and set `MCP_ALLOWED_HOSTS` to your
hostname before exposing the service. The Dockerfile builds a container from
your copy of the repository, including its `skills/` folder. After you edit
the skill files, rebuild the image and start a new container from it.

### Try the container with Docker Compose

From the repository root, create a private `.env` file with a new token:

```sh
(umask 077; set -C; printf 'MCP_BEARER_TOKEN=%s\n' "$(openssl rand -hex 32)" > .env)
docker compose up --build -d
```

The command preserves an existing `.env`. Compose reads the token from that
file and binds the service to `127.0.0.1:8000`. Connect a client on the same
machine to `http://127.0.0.1:8000/mcp`. Keep `.env` out of version control.

## Connect a client

Choose Streamable HTTP as the connection type. Use your own `/mcp` URL and
the same token you set on the server. Use HTTPS for connections over the
network.

### Codex desktop

Open **Settings → MCP servers** and add a **Streamable HTTP** server. Enter
`https://skills.example.com/mcp` as the URL. Leave **Bearer token env var**
blank and add an HTTP header named `Authorization` with the value
`Bearer YOUR_TOKEN`. Replace `YOUR_TOKEN` with the token. Save the entry and
restart when prompted. Codex stores the header in its local configuration.

For the CLI, you can supply the token through an environment variable instead.
Set `SKILLS_MCP_TOKEN` in the environment that launches Codex, then run:

```sh
codex mcp add skills --url https://skills.example.com/mcp \
  --bearer-token-env-var SKILLS_MCP_TOKEN
```

See the [Codex MCP guide](https://learn.chatgpt.com/docs/extend/mcp?surface=cli).

### Claude Code

Set `SKILLS_MCP_TOKEN` to your token in your shell environment, then run:

```sh
claude mcp add --transport http --scope user skills \
  https://skills.example.com/mcp \
  --header "Authorization: Bearer ${SKILLS_MCP_TOKEN:?Set SKILLS_MCP_TOKEN first}"
```

This saves the header in your user configuration. Run `/mcp` in Claude Code
to check the connection. See the
[Claude Code MCP guide](https://code.claude.com/docs/en/mcp).

## Verify the deployment

Replace the hostname with yours. For local Compose, use
`http://127.0.0.1:8000` as the base URL.

```sh
curl -i https://skills.example.com/healthz
curl -i https://skills.example.com/mcp
```

The health endpoint should return `200` and show the number of loaded skills.
The MCP endpoint should return `401` without a token. In the connected client,
ask it to use the `load_skill` tool to load one of your skills by name. With
the example catalog, load `example` and ask it to read
`skill://example/references/handover.md` with `read_skill_file`. This confirms
that the client can retrieve both the skill and a supporting file.

## License

[MIT](LICENSE)
