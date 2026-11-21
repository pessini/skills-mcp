"""Run one stateless Streamable HTTP process."""

import logging
import os
import sys

from starlette.middleware import Middleware

from skills_mcp.auth import RequireBearerToken, bearer_token_from_env
from skills_mcp.server import create_server


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    try:
        port = int(os.environ.get("PORT", "8000"))
        if not 1 <= port <= 65535:
            raise ValueError("PORT must be between 1 and 65535")
        host = os.environ.get("HOST", "127.0.0.1")
        allowed_hosts = [
            value.strip()
            for value in os.environ.get("MCP_ALLOWED_HOSTS", "").split(",")
            if value.strip()
        ]
        allowed_origins = [
            value.strip()
            for value in os.environ.get("MCP_ALLOWED_ORIGINS", "").split(",")
            if value.strip()
        ]
        token = bearer_token_from_env()
        middleware = [Middleware(RequireBearerToken, token=token)] if token else []
        with create_server() as server:
            logging.getLogger(__name__).info(
                "starting endpoint=http://%s:%d/mcp stateless=true", host, port
            )
            server.run(
                transport="http",
                host=host,
                port=port,
                path="/mcp",
                stateless_http=True,
                json_response=True,
                middleware=middleware,
                show_banner=False,
                host_origin_protection=True,
                allowed_hosts=allowed_hosts or None,
                allowed_origins=allowed_origins or None,
            )
    except (OSError, ValueError) as exc:
        logging.getLogger(__name__).error("Startup failed: %s", exc)
        sys.exit(1)


if __name__ == "__main__":
    main()
