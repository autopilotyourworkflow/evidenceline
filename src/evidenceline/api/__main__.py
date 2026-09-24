"""Run the web API: ``python -m evidenceline.api`` or the ``evidenceline-api`` console script. HOST and PORT come
from the environment (default 127.0.0.1:8000); on a hosting platform set HOST=0.0.0.0."""

from __future__ import annotations

import importlib.util
import os
import sys

API_PACKAGES = ("fastapi", "uvicorn", "anthropic")


def missing_packages() -> list[str]:
    """The web API's packages (the ``api`` extra) that are not installed."""
    return [name for name in API_PACKAGES if importlib.util.find_spec(name) is None]


def main() -> None:
    missing = missing_packages()
    if missing:
        sys.exit(
            f"The web API needs {', '.join(missing)}, which is not installed. Install the api extra: "
            'pip install ".[api]" (the stdio MCP server does not need it).'
        )
    import uvicorn  # noqa: PLC0415 - checked above, so a missing package gives the plain message instead

    uvicorn.run(
        "evidenceline.api.app:create_app",
        factory=True,
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8000")),
        proxy_headers=False,
    )


if __name__ == "__main__":
    main()
