"""CLI entry points."""
from __future__ import annotations

import asyncio
import logging

import typer
import uvicorn
from rich.console import Console
from rich.table import Table

from tanktrack.ais_stream import run_forever
from tanktrack.config import API_HOST, API_PORT, DB_PATH, setup_logging
from tanktrack.counters import (
    by_vessel_type_inside,
    count_anchored,
    count_inside,
    counts_window,
    recent_transitions,
)
from tanktrack.db import healthcheck, init_schema

setup_logging()
log = logging.getLogger("tanktrack.cli")
console = Console()

app = typer.Typer(no_args_is_help=True, add_completion=False, help="tanktrack CLI — vessel geofence + anchor detection")


@app.command("init-db")
def init_db_cmd() -> None:
    """Create the SQLite file + schema. Safe to run repeatedly."""
    init_schema()
    console.print(f"[green]ready[/green]  {DB_PATH}")


@app.command("db-check")
def db_check() -> None:
    """Verify the database is reachable."""
    if not healthcheck():
        console.print(f"[red]DB unreachable[/red] — run `hormuz init-db` first? ({DB_PATH})")
        raise typer.Exit(1)
    console.print(f"[green]db OK[/green]  {DB_PATH}")


@app.command("stream")
def stream_cmd() -> None:
    """Run the AIS consumer forever. Typically launched via tmux or systemd."""
    asyncio.run(run_forever())


@app.command("state")
def state_cmd() -> None:
    """Print headline counters."""
    last24 = counts_window(hours=24)
    last1 = counts_window(hours=1)
    types = by_vessel_type_inside()

    t = Table(title="Strait of Hormuz — live state", show_header=True)
    t.add_column("metric"); t.add_column("value", justify="right")
    t.add_row("in_transit_now", str(count_inside()))
    t.add_row("anchored_now", str(count_anchored()))
    t.add_row("entered (24h)", str(last24["entered"]))
    t.add_row("exited  (24h)", str(last24["exited"]))
    t.add_row("anchored (24h)", str(last24["anchored"]))
    t.add_row("entered (1h)",  str(last1["entered"]))
    t.add_row("exited  (1h)",  str(last1["exited"]))
    console.print(t)

    if types:
        tt = Table(title="by vessel type (inside now)", show_header=True)
        tt.add_column("kind"); tt.add_column("count", justify="right")
        for k, v in types.items():
            tt.add_row(k, str(v))
        console.print(tt)


@app.command("transitions")
def transitions_cmd(limit: int = typer.Option(20)) -> None:
    rows = recent_transitions(limit=limit)
    t = Table(title=f"last {limit} transitions", show_header=True)
    t.add_column("at"); t.add_column("event"); t.add_column("dir")
    t.add_column("mmsi"); t.add_column("name"); t.add_column("type")
    t.add_column("lat", justify="right"); t.add_column("lon", justify="right")
    t.add_column("sog_kt", justify="right")
    for r in rows:
        t.add_row(
            (r["at"] or "")[:19],
            r["event_type"],
            r.get("direction") or "",
            str(r["mmsi"]),
            (r.get("name") or "")[:24],
            r.get("vessel_type_str") or "",
            f"{r['lat']:.3f}" if r.get("lat") is not None else "",
            f"{r['lon']:.3f}" if r.get("lon") is not None else "",
            f"{r['sog_kt']:.1f}" if r.get("sog_kt") is not None else "",
        )
    console.print(t)


@app.command("serve")
def serve_cmd() -> None:
    """Run the FastAPI service."""
    uvicorn.run("hormuz.api:app", host=API_HOST, port=API_PORT, log_level="info")


if __name__ == "__main__":
    app()
