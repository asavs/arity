"""Compatibility entry point for ``python -m gorkbot``; launches the Arity CLI."""
from .cli import main

if __name__ == "__main__":
    raise SystemExit(main())
