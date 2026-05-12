"""Quick smoke-test of retrieval. Run after indexing.

Run with:
    uv run python -m scripts.test_query "Welche Strafen drohen bei einem Datenschutzverstoß?"
"""

import sys

from rich.console import Console
from rich.panel import Panel

from src.embeddings.encoder import encode_query_dense
from src.retrieval.vector_store import (
    COLLECTION_NAME,
    DENSE_VECTOR_NAME,
    get_client,
)

console = Console()


def main() -> None:
    if len(sys.argv) < 2:
        console.print('[red]Usage: python -m scripts.test_query "<your question>"[/red]')
        return

    query = sys.argv[1]
    console.rule(f"[bold cyan]Query: {query}")

    client = get_client()

    # Dense-only search for the smoke test (we'll add hybrid Day 5)
    qvec = encode_query_dense(query)
    results = client.query_points(
        collection_name=COLLECTION_NAME,
        query=qvec.tolist(),
        using=DENSE_VECTOR_NAME,
        limit=5,
        with_payload=True,
    ).points

    for i, r in enumerate(results, 1):
        meta = r.payload["metadata"]
        section = meta.get("section_heading") or "—"
        console.print(
            Panel(
                f"[dim]Score: {r.score:.4f}  |  Section: {section}  "
                f"|  Lang: {meta.get('language')}  |  Pages: {meta.get('page_start')}-{meta.get('page_end')}[/dim]\n\n"
                f"{r.payload['text_raw'][:500]}...",
                title=f"[bold yellow]Result {i}[/bold yellow]",
                border_style="cyan",
            )
        )


if __name__ == "__main__":
    main()
