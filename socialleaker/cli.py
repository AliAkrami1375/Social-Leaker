"""Social Leaker command-line interface (Typer)."""
from __future__ import annotations

import sys

# Windows consoles default to a legacy code page (cp1252) that cannot encode the
# UI glyphs used below; force UTF-8 so rich output never raises.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
    except Exception:
        pass

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from . import __version__
from .config import settings
from .database import SessionLocal, init_db
from .models import Role, TaskStatus, User
from .security import hash_password

app = typer.Typer(help="Social Leaker — social-media OSINT & collection panel.", add_completion=False)
console = Console()


@app.command()
def version():
    """Print the version."""
    console.print(f"[bold cyan]Social Leaker[/] v{__version__}")


@app.command()
def init(
    admin_user: str = typer.Option(None, help="Admin username (default from .env)"),
    admin_pass: str = typer.Option(None, help="Admin password (default from .env)"),
):
    """Create the database schema and the first admin user."""
    init_db()
    console.print("[green]✓[/] Database schema created.")

    username = admin_user or settings.admin_username
    password = admin_pass or settings.admin_password

    db = SessionLocal()
    try:
        existing = db.query(User).filter(User.username == username).first()
        if existing:
            console.print(f"[yellow]![/] Admin user '{username}' already exists.")
            return
        user = User(username=username, password_hash=hash_password(password), role=Role.admin)
        db.add(user)
        db.commit()
        console.print(f"[green]✓[/] Admin user created: [bold]{username}[/]")
        if password == "admin":
            console.print("[red]⚠  Default password in use — change it after first login![/]")
    finally:
        db.close()


@app.command()
def adduser(
    username: str = typer.Argument(...),
    password: str = typer.Option(..., prompt=True, hide_input=True, confirmation_prompt=True),
    role: str = typer.Option("operator", help="admin | operator | viewer"),
):
    """Add a panel user."""
    init_db()
    db = SessionLocal()
    try:
        if db.query(User).filter(User.username == username).first():
            console.print(f"[red]✗[/] User '{username}' already exists.")
            raise typer.Exit(1)
        user = User(username=username, password_hash=hash_password(password), role=Role(role))
        db.add(user)
        db.commit()
        console.print(f"[green]✓[/] User '{username}' created with role [bold]{role}[/].")
    finally:
        db.close()


@app.command()
def users():
    """List panel users."""
    db = SessionLocal()
    try:
        table = Table(title="Panel Users")
        table.add_column("ID", justify="right")
        table.add_column("Username")
        table.add_column("Role")
        table.add_column("Active")
        table.add_column("Last login")
        for u in db.query(User).order_by(User.id).all():
            table.add_row(
                str(u.id), u.username, u.role.value,
                "yes" if u.is_active else "no",
                u.last_login.strftime("%Y-%m-%d %H:%M") if u.last_login else "—",
            )
        console.print(table)
    finally:
        db.close()


@app.command()
def collect(
    handles: list[str] = typer.Argument(..., help="One or more handles to collect."),
    goal: int = typer.Option(25, help="Goal: number of profiles to collect."),
):
    """Run a one-off task from the CLI (mirrors the web panel loop engine)."""
    from .models import Task
    from .services import task_runner

    init_db()
    db = SessionLocal()
    try:
        task = Task(
            title=f"CLI: {', '.join(handles)}"[:120],
            prompt=f"Collect {goal} profiles related to: " + ", ".join("@" + h for h in handles),
            goal_target=goal,
            status=TaskStatus.queued,
        )
        db.add(task)
        db.commit()
        tid = task.id
    finally:
        db.close()

    console.print(f"[cyan]▶[/] Running task #{tid} …")
    task_runner.run_task(tid)

    db = SessionLocal()
    try:
        task = db.get(Task, tid)
        console.print(
            f"[green]✓[/] Done — status=[bold]{task.status.value}[/], "
            f"collected=[bold]{task.collected_count}[/] profile(s) in "
            f"[bold]{task.iterations}[/] iteration(s)."
        )
    finally:
        db.close()


@app.command()
def serve(
    host: str = typer.Option(None, help="Bind host (default from .env)."),
    port: int = typer.Option(None, help="Bind port (default from .env)."),
    reload: bool = typer.Option(False, help="Auto-reload for development."),
):
    """Start the web panel."""
    init_db()
    h = host or settings.app_host
    p = port or settings.app_port
    console.print(f"[bold green]Social Leaker[/] running at [underline]http://{h}:{p}[/]")
    uvicorn.run("socialleaker.main:app", host=h, port=p, reload=reload)


if __name__ == "__main__":
    app()
