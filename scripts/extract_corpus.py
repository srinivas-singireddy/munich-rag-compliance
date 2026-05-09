"""Extract all PDFs in data/raw/ into structured JSON in data/processed/.

Run with:
    uv run python -m scripts.extract_corpus
"""

from rich.console import Console
from rich.table import Table

from src.config import settings
from src.ingestion.pdf_loader import extract_pdf, save_document
from src.logging_setup import get_logger, setup_logging

console = Console()
log = get_logger(__name__)


def main() -> None:
    setup_logging("INFO")
    console.rule("[bold cyan]Munich RAG — PDF Extraction")

    pdfs = sorted(settings.raw_dir.glob("*.pdf"))
    if not pdfs:
        console.print(f"[red]No PDFs found in {settings.raw_dir}[/red]")
        return

    console.print(f"Found [bold]{len(pdfs)}[/bold] PDFs\n")

    results = []
    for pdf_path in pdfs:
        try:
            document = extract_pdf(pdf_path)
            output_path = save_document(document, settings.processed_dir)
            results.append((document, output_path, None))
        except Exception as e:
            log.error("extraction_failed", file=pdf_path.name, error=str(e))
            results.append((None, None, f"{pdf_path.name}: {e}"))

    # Summary table
    table = Table(title="Extraction Summary", show_lines=True)
    table.add_column("Document", style="cyan")
    table.add_column("Pages", justify="right")
    table.add_column("Chars", justify="right")
    table.add_column("Sections", justify="right")
    table.add_column("Lang", justify="center")
    table.add_column("Type")
    table.add_column("Warnings", style="yellow")

    for doc, _, error in results:
        if error or doc is None:
            table.add_row(error or "?", "—", "—", "—", "—", "—", "[red]FAILED[/red]")
            continue
        table.add_row(
            doc.doc_id,
            str(doc.page_count),
            f"{doc.total_chars:,}",
            str(len(doc.sections)),
            doc.language.value,
            doc.doc_type.value,
            "\n".join(doc.extraction_warnings) or "—",
        )

    console.print(table)
    console.print(f"\n[green]Output saved to:[/green] {settings.processed_dir}")


if __name__ == "__main__":
    main()
