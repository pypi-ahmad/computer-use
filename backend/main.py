"""Entry point for the backend server."""

import uvicorn

from backend.config import config


def main():
    """Launch the FastAPI backend via Uvicorn."""
    uvicorn.run(
        "backend.server:app",
        host=config.host,
        port=config.port,
        reload=config.debug,
        log_level="debug" if config.debug else "info",
    )


if __name__ == "__main__":
    main()
