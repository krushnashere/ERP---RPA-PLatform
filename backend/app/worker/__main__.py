"""Entry point so the worker can be started with `python -m app.worker`."""
from app.worker.main import main

if __name__ == "__main__":
    raise SystemExit(main())
