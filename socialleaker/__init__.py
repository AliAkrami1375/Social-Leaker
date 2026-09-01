"""Social Leaker — a self-hosted social-media OSINT & data-collection panel.

The package bundles:
  * a FastAPI web panel with user/password authentication,
  * an Instagram collection engine with rate-limit safeguards,
  * a Claude Code ACP bridge that lets an autonomous agent drive collection,
  * a SQLite-backed data layer, and
  * a Typer command-line interface.
"""

__version__ = "1.0.0"
__all__ = ["__version__"]
