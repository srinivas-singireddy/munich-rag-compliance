"""Evaluate retrieval quality across three strategies on the golden test set.

Metrics:
    Precision@1  — was the top-1 result in expected_section_ids?
    Recall@5     — was any expected section in the top-5?
    MRR          — Mean Reciprocal Rank (1/rank of first correct result)

Run with:
    uv run python -m scripts.eval_retrieval
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from src.config import settings
from src.logging_setup import get_logger, setup_logging
from src.retrieval.hybrid_search import retrieve
from src.retrieval.vector_store import get_client

console = Console()
log = get_logger(__name__)

GOLDEN_PATH = Path("data/eval/golden.jsonl")
STRATEGIES = ["dense", "hybrid", "hybrid+rerank"]


def load_golden() -> list[dict]:
    if not GOLDEN_PATH.exists():
        raise FileNotFoundError(f"Golden set not found: {GOLDEN_PATH}")
    return [json.loads(line) for line in GOLDEN_PATH.read_text().splitlines() if line.strip()]


def is_hit(
    results: list, expected_section_ids: list[str], expected_doc_id: str | None = None
) -> bool:
    """True if any expected section is in the results, optionally scoped by doc."""
    for r in results:
        sid = r.metadata.get("section_id", "")
        doc_id = r.metadata.get("doc_id", "")
        if sid in expected_section_ids:
            if expected_doc_id is None or doc_id == expected_doc_id:
                return True
    return False


def reciprocal_rank(
    results: list, expected_section_ids: list[str], expected_doc_id: str | None = None
) -> float:
    expected_set = set(expected_section_ids)
    for rank, r in enumerate(results, start=1):
        sid = r.metadata.get("section_id", "")
        doc_id = r.metadata.get("doc_id", "")
        match = sid in expected_set
        if expected_doc_id:
            match = match and (doc_id == expected_doc_id)
        if match:
            return 1.0 / rank
    return 0.0


def evaluate_strategy(
    client,
    golden: list[dict],
    strategy: str,
) -> dict:
    p_at_1_hits = 0
    r_at_5_hits = 0
    mrr_sum = 0.0
    details = []

    for item in golden:
        question = item["question"]
        expected = item["expected_section_ids"]
        expected_doc = item.get("expected_doc_id", None)  # optional doc scoping

        results = retrieve(client, question, strategy=strategy, top_k=40, top_n=10)

        # Deduplicate by parent_id — keep first child per parent
        # This reflects what the LLM actually sees (one context per parent)
        seen_parents: set[str] = set()
        deduped_results = []
        for r in results:
            if r.parent_id not in seen_parents:
                seen_parents.add(r.parent_id)
                deduped_results.append(r)
            if len(deduped_results) >= 5:
                break

        # Precision@1 — doc-scoped when expected_doc_id is set
        p1 = False
        if deduped_results:
            top = deduped_results[0]
            sid = top.metadata.get("section_id", "")
            doc = top.metadata.get("doc_id", "")
            p1 = sid in expected
            if expected_doc:
                p1 = p1 and (doc == expected_doc)
        if p1:
            p_at_1_hits += 1

        # Recall@5 — doc-scoped when expected_doc_id is set
        r5 = False
        for r in deduped_results:
            sid = r.metadata.get("section_id", "")
            doc = r.metadata.get("doc_id", "")
            match = sid in expected
            if expected_doc:
                match = match and (doc == expected_doc)
            if match:
                r5 = True
                break
        if r5:
            r_at_5_hits += 1

        # MRR — doc-scoped when expected_doc_id is set
        rr = 0.0
        for rank, r in enumerate(deduped_results, start=1):
            sid = r.metadata.get("section_id", "")
            doc = r.metadata.get("doc_id", "")
            match = sid in expected
            if expected_doc:
                match = match and (doc == expected_doc)
            if match:
                rr = 1.0 / rank
                break
        mrr_sum += rr

        result_section_ids = [r.metadata.get("section_id", "") for r in deduped_results]

        details.append(
            {
                "question": question[:60],
                "expected": expected,
                "expected_doc": expected_doc,
                "got": result_section_ids,
                "p@1": p1,
                "r@5": r5,
                "rr": round(rr, 3),
            }
        )

    n = len(golden)
    return {
        "strategy": strategy,
        "p_at_1": p_at_1_hits / n,
        "r_at_5": r_at_5_hits / n,
        "mrr": mrr_sum / n,
        "n": n,
        "details": details,
    }


def main() -> None:
    setup_logging("WARNING")  # suppress info noise during eval
    console.rule("[bold cyan]Munich RAG — Retrieval Evaluation")

    golden = load_golden()
    console.print(f"Golden set: [bold]{len(golden)}[/bold] questions\n")

    client = get_client()

    # Run all strategies
    all_results = {}
    for strategy in STRATEGIES:
        console.print(f"Evaluating [bold]{strategy}[/bold]...")
        metrics = evaluate_strategy(client, golden, strategy)
        all_results[strategy] = metrics
        console.print(
            f"  P@1={metrics['p_at_1']:.0%}  R@5={metrics['r_at_5']:.0%}  MRR={metrics['mrr']:.3f}\n"
        )

    # Summary table
    table = Table(title="Retrieval Quality Comparison", show_lines=True)
    table.add_column("Strategy", style="cyan")
    table.add_column("Precision@1", justify="right")
    table.add_column("Recall@5", justify="right")
    table.add_column("MRR", justify="right")
    table.add_column("Improvement vs Dense", justify="right", style="green")

    dense_p1 = all_results["dense"]["p_at_1"]
    for strategy in STRATEGIES:
        m = all_results[strategy]
        delta = f"+{(m['p_at_1'] - dense_p1):.0%}" if strategy != "dense" else "—"
        table.add_row(
            strategy,
            f"{m['p_at_1']:.0%}",
            f"{m['r_at_5']:.0%}",
            f"{m['mrr']:.3f}",
            delta,
        )
    console.print(table)

    # Per-question breakdown for the best strategy
    best_strategy = max(all_results, key=lambda s: all_results[s]["p_at_1"])
    console.print(f"\n[bold]Per-question breakdown ({best_strategy}):[/bold]")
    detail_table = Table(show_lines=True)
    detail_table.add_column("Question", max_width=45)
    detail_table.add_column("Expected", max_width=25)
    detail_table.add_column("Got (top-1)")
    detail_table.add_column("P@1", justify="center")
    detail_table.add_column("R@5", justify="center")

    for d in all_results[best_strategy]["details"]:
        p1_str = "[green]✓[/green]" if d["p@1"] else "[red]✗[/red]"
        r5_str = "[green]✓[/green]" if d["r@5"] else "[red]✗[/red]"
        detail_table.add_row(
            d["question"],
            ", ".join(d["expected"]),
            d["got"][0] if d["got"] else "—",
            p1_str,
            r5_str,
        )
    console.print(detail_table)

    # Save results to file for reference
    output_path = Path("data/eval/retrieval_eval_results.json")
    output_path.write_text(
        json.dumps(
            {s: {k: v for k, v in m.items() if k != "details"} for s, m in all_results.items()},
            indent=2,
        )
    )
    console.print(f"\n[green]Results saved:[/green] {output_path}")


if __name__ == "__main__":
    main()
