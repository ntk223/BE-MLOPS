"""ASGI entrypoint for the LearnGuard backend.

Run with:

    uvicorn learnguard_backend.main:app --reload

or directly:

    python -m learnguard_backend.main
"""

import uvicorn

from learnguard_backend.api.app import create_app

app = create_app()


if __name__ == "__main__":
    uvicorn.run(
        "learnguard_backend.main:app",
        host="127.0.0.1",
        port=8000,
    )
