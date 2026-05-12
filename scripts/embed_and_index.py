"""Embed all child chunks and index them into Qdrant.

Run with:
    uv run python -m scripts.embed_and_index
    uv run python -m scripts.embed_and_index --recreate   # wipe and re-index

Prerequisite: docker compose up -d (Qdrant must be running)
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from rich.console import Console

from src.config import settings
from src.logging_setup import get_logger, setup_logging
from src.retrieval.vector_store import (
    COLLECTION_NAME,
    ensure_collection,
    get_client,
    upsert_children,
)

console = Console()
log = get_logger(__name__)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--recreate", action="store_true", help="Delete and recreate collection")
    args = parser.parse_args()

    setup_logging("INFO")
    console.rule("[bold cyan]Munich RAG — Embed + Index")

    children_path = settings.processed_dir / "chunks_children.jsonl"
    if not children_path.exists():
        console.print(f"[red]Children file not found: {children_path}[/red]")
        console.print("Run [bold]uv run python -m scripts.chunk_corpus[/bold] first.")
        return

    children = [json.loads(line) for line in children_path.read_text().splitlines()]
    console.print(f"Loaded [bold]{len(children)}[/bold] child chunks")

    try:
        client = get_client()
        client.get_collections()  # ping
    except Exception as e:
        console.print(f"[red]Cannot connect to Qdrant: {e}[/red]")
        console.print("Is Docker running? [bold]docker compose up -d[/bold]")
        return

    ensure_collection(client, recreate=args.recreate)
    upsert_children(client, children)

    info = client.get_collection(COLLECTION_NAME)
    # Reconciliation check — protects against silent data loss
    if info.points_count != len(children):
        console.print(
            f"[bold red]⚠ Reconciliation mismatch:[/bold red] "
            f"input {len(children)} children → {info.points_count} indexed points"
        )
        console.print("[red]Possible chunk_id collisions. Investigate before continuing.[/red]")
    else:
        console.print(
            f"[green]✓ Reconciliation OK: {info.points_count} indexed (matches input)[/green]"
        )
    console.rule(f"[bold green]Indexed {info.points_count} children into '{COLLECTION_NAME}'")


if __name__ == "__main__":
    main()
