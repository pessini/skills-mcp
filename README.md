# skills-mcp

Share your own skills with Codex, Claude Code, or another MCP client. The Python
server reads skills from this repository and serves them over Streamable HTTP.
Each deployment has its own catalog, URL, and bearer token. It needs one
container and no database.

## Add your skills

Create a copy of this repository under your account. Put each skill in its own
folder under `skills/`:

```text
skills/
  my-skill/
    SKILL.md
    references/
      guide.md
```

Each `SKILL.md` starts with YAML front matter containing a unique `name` and a
`description` that tells the client when to use it. Start with the
[example skill](skills/example/SKILL.md). You can remove it after adding your own
skill. Keep at least one valid skill in the catalog.

The Docker build copies `skills/` from your repository into the image. Rebuild
and redeploy after changing the catalog. Keep credentials and private files you
do not intend to serve outside `skills/`.

## Run a local test with Docker Compose

From the repository root, create a private `.env` file with a new token:

```sh
(umask 077; set -C; printf 'MCP_BEARER_TOKEN=%s\n' "$(openssl rand -hex 32)" > .env)
docker compose up --build -d
```

The command preserves an existing `.env`. Compose reads the token from that
file and binds the service to `127.0.0.1:8000`. Connect a client on the same
machine to `http://127.0.0.1:8000/mcp`. Keep `.env` out of version control.

## Deploy to Dokploy

Create a Dokploy **Application** from your Git repository. Choose
**Dockerfile** as the build type. Use `/` for the Git build path, `Dockerfile`
for the Dockerfile path, and `.` for the Docker context. Leave the build stage
empty.

In the application **Environment** tab, set these runtime variables:

| Variable | Value |
| --- | --- |
| `MCP_BEARER_TOKEN` | A secret generated with `openssl rand -hex 32` |
| `MCP_ALLOWED_HOSTS` | Your MCP hostname, for example `skills.example.com` |

Point your DNS record to the server and add the hostname under **Domains**.
Set the route path to `/`, leave the internal path empty, turn off **Strip
Path**, and set the container port to `8000`. Enable HTTPS and deploy. The
configured hostname must match the hostname in the client URL. For another
container host, build the same Dockerfile at the repository root and forward
HTTPS to container port `8000`.

[Dokploy build settings](https://docs.dokploy.com/docs/core/applications/build-type)
and [domain settings](https://docs.dokploy.com/docs/core/domains) show where to
enter these values.

## Connect a client

Use your own URL and the same token you set on the server. Use HTTPS for
connections over the network.

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

## Develop the server

Install Python 3.12 or later and uv, then run:

```sh
uv sync --locked
uv run --locked python -m skills_mcp
```

With uv, the server defaults to `127.0.0.1:8000` and uses the repository's
`skills/` directory. Set `SKILLS_ROOT` to use a catalog at another path. To test authentication outside Docker, set
`MCP_BEARER_TOKEN` before starting the server.
