"""Chunk all extracted documents into parent-child chunk hierarchy.

Run with:
    uv run python -m scripts.chunk_corpus
"""

from rich.console import Console
from rich.table import Table

from src.chunking.pipeline import chunk_corpus, save_chunks
from src.config import settings
from src.logging_setup import get_logger, setup_logging

console = Console()
log = get_logger(__name__)


def main() -> None:
    setup_logging("INFO")
    console.rule("[bold cyan]Munich RAG — Hierarchical Chunking")

    if not settings.processed_dir.exists() or not list(settings.processed_dir.glob("*.json")):
        console.print(f"[red]No documents found in {settings.processed_dir}[/red]")
        console.print("Run [bold]uv run python -m scripts.extract_corpus[/bold] first.")
        return

    parents, children, stats = chunk_corpus(settings.processed_dir)

    if not parents and not children:
        console.print("[red]No chunks produced. Check input documents.[/red]")
        return

    paths = save_chunks(parents, children, stats, settings.processed_dir)

    # Summary table
    table = Table(title="Chunking Summary", show_lines=True)
    table.add_column("Metric", style="cyan")
    table.add_column("Value", justify="right")

    table.add_row("Documents processed", str(stats.documents_processed))
    table.add_row("Parent chunks", str(stats.parent_chunks_created))
    table.add_row("Child chunks", str(stats.child_chunks_created))
    table.add_row(
        "Children per parent (avg)",
        f"{stats.child_chunks_created / max(stats.parent_chunks_created, 1):.1f}",
    )
    table.add_row("Avg parent tokens", f"{stats.avg_parent_tokens:.0f}")
    table.add_row("Avg child tokens", f"{stats.avg_child_tokens:.0f}")
    table.add_row("Max child tokens", str(stats.max_child_tokens))
    table.add_row("Sections split into multiple parents", str(stats.sections_split))
    table.add_row("Sections kept as one parent", str(stats.sections_with_no_split))
    table.add_row("Documents fallen back to text chunking", str(stats.fallback_text_chunked))

    console.print(table)
    console.print(f"\n[green]Saved:[/green]")
    for name, path in paths.items():
        size_kb = path.stat().st_size / 1024
        console.print(f"  {name:8s}  {path}  [dim]({size_kb:.1f} KB)[/dim]")


if __name__ == "__main__":
    main()
