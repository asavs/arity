"""Compatibility entry point for ``python -m arity``; launches the Arity CLI."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
