"""Download German regulatory documents: DSGVO, BDSG, and BaFin Rundschreiben.

Sources verified live April 2026.

Run with:
    uv run python -m scripts.download_corpus
"""

import time
from pathlib import Path
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup
from rich.console import Console
from rich.progress import Progress

from src.config import settings
from src.logging_setup import get_logger, setup_logging

console = Console()
log = get_logger(__name__)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Apple Silicon Mac OS X 14_0) "
    "MunichRAGResearch/0.1 (educational portfolio project)"
)

# ---------------------------------------------------------------------------
# Direct-download PDFs: stable, official, single-shot
# ---------------------------------------------------------------------------
DIRECT_PDFS: list[tuple[str, str]] = [
    # Consolidated DSGVO text (Berlin Data Protection Authority, 2025 edition)
    (
        "dsgvo_official_de.pdf",
        "https://www.datenschutz-berlin.de/fileadmin/user_upload/pdf/gesetzestexte/2025-BlnBDI_DSGVO.pdf",
    ),
    # German BDSG (English version, useful for evaluation later)
    (
        "bdsg_official_en.pdf",
        "https://www.gesetze-im-internet.de/englisch_bdsg/englisch_bdsg.pdf",
    ),
]

# ---------------------------------------------------------------------------
# BaFin Rundschreiben hub: we crawl 1 hop deep and grab any PDFs we find
# ---------------------------------------------------------------------------
BAFIN_LISTING_URLS = [
    "https://www.bafin.de/DE/RechtRegelungen/Verwaltungspraxis/Rundschreiben/rundschreiben_node.html",
    "https://www.bafin.de/DE/unternehmen-maerkte/recht-regelungen/verwaltungspraxis/rundschreiben/rundschreiben_node.html",
]


def is_real_pdf(response: httpx.Response) -> bool:
    """Validate the response is actually a PDF, not an HTML error page."""
    content_type = response.headers.get("content-type", "").lower()
    return "pdf" in content_type or "octet-stream" in content_type


def download_file(client: httpx.Client, url: str, dest: Path) -> bool:
    """Download a single file. Idempotent: skips if already present."""
    if dest.exists() and dest.stat().st_size > 1024:
        log.debug("skipping_existing", file=dest.name)
        return True
    try:
        with client.stream("GET", url, follow_redirects=True) as response:
            response.raise_for_status()
            if not is_real_pdf(response):
                log.warning(
                    "not_a_pdf",
                    url=url,
                    content_type=response.headers.get("content-type"),
                )
                return False
            with dest.open("wb") as f:
                for chunk in response.iter_bytes(chunk_size=8192):
                    f.write(chunk)
        log.info("downloaded", file=dest.name, size_kb=f"{dest.stat().st_size / 1024:.1f}")
        return True
    except Exception as e:
        log.warning("download_failed", url=url, error=str(e))
        if dest.exists():
            dest.unlink()
        return False


def extract_pdf_links_from_page(html: str, base_url: str) -> list[str]:
    """Extract every absolute PDF URL from an HTML page."""
    soup = BeautifulSoup(html, "html.parser")
    pdf_urls: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if ".pdf" in href.lower():
            absolute = urljoin(base_url, href)
            # Strip URL fragments and query strings for cleaner filenames
            absolute = absolute.split("#")[0]
            if absolute not in pdf_urls:
                pdf_urls.append(absolute)
    return pdf_urls


def discover_bafin_pdfs(client: httpx.Client, max_pdfs: int) -> list[tuple[str, str]]:
    """Crawl BaFin's Rundschreiben hub and harvest any PDF links found on sub-pages."""
    listing_html: str | None = None
    used_url: str | None = None

    for url in BAFIN_LISTING_URLS:
        try:
            log.info("trying_bafin_listing", url=url)
            response = client.get(url, follow_redirects=True)
            response.raise_for_status()
            listing_html = response.text
            used_url = str(response.url)  # final URL after redirects
            break
        except Exception as e:
            log.warning("bafin_listing_url_failed", url=url, error=str(e))

    if not listing_html or not used_url:
        log.error("all_bafin_urls_failed")
        return []

    soup = BeautifulSoup(listing_html, "html.parser")

    # Collect candidate sub-page links — anything in the Rundschreiben section
    sub_pages: list[str] = []
    for a in soup.find_all("a", href=True):
        href: str = a["href"]
        # Heuristic: BaFin Rundschreiben pages live under /Rundschreiben/ paths
        if "/Rundschreiben/" in href and href.endswith(".html"):
            absolute = urljoin(used_url, href).split("#")[0]
            if absolute not in sub_pages:
                sub_pages.append(absolute)

    sub_pages = sub_pages[: max_pdfs * 2]  # don't visit hundreds
    log.info("found_subpages", count=len(sub_pages))

    pdf_links: list[tuple[str, str]] = []
    seen_pdfs: set[str] = set()

    for sub_url in sub_pages:
        if len(pdf_links) >= max_pdfs:
            break
        try:
            r = client.get(sub_url, follow_redirects=True)
            if r.status_code != 200:
                continue
            for pdf_url in extract_pdf_links_from_page(r.text, str(r.url)):
                if pdf_url in seen_pdfs:
                    continue
                seen_pdfs.add(pdf_url)
                filename = Path(urlparse(pdf_url).path).name
                if filename and filename.lower().endswith(".pdf"):
                    pdf_links.append((f"bafin_{filename}", pdf_url))
                    if len(pdf_links) >= max_pdfs:
                        break
            time.sleep(settings.request_delay_seconds)
        except Exception as e:
            log.debug("subpage_failed", url=sub_url, error=str(e))

    log.info("bafin_pdf_links_found", count=len(pdf_links))
    return pdf_links


def main() -> None:
    setup_logging("INFO")
    settings.raw_dir.mkdir(parents=True, exist_ok=True)
    console.rule("[bold cyan]Munich RAG — Corpus Download")
    console.print(f"Target directory: [green]{settings.raw_dir}[/green]\n")

    with httpx.Client(
        headers={"User-Agent": USER_AGENT},
        timeout=settings.download_timeout_seconds,
    ) as client:
        # Step 1 — Direct downloads
        console.print("[bold]Step 1 — Direct downloads (DSGVO, BDSG)[/bold]")
        direct_success = 0
        for filename, url in DIRECT_PDFS:
            dest = settings.raw_dir / filename
            ok = download_file(client, url, dest)
            mark = "[green]✓[/green]" if ok else "[red]✗[/red]"
            console.print(f"  {mark} {filename}")
            if ok:
                direct_success += 1
            time.sleep(settings.request_delay_seconds)
        console.print(f"  [dim]{direct_success}/{len(DIRECT_PDFS)} succeeded[/dim]\n")

        # Step 2 — BaFin scrape
        console.print("[bold]Step 2 — BaFin Rundschreiben (best-effort scrape)[/bold]")
        try:
            links = discover_bafin_pdfs(client, settings.max_pdfs_to_download)
        except Exception as e:
            log.error("bafin_discovery_failed", error=str(e))
            links = []

        if not links:
            console.print(
                "  [yellow]No BaFin PDFs found — that's OK, the DSGVO/BDSG corpus is enough.[/yellow]"
            )
        else:
            console.print(f"  Discovered [bold]{len(links)}[/bold] PDFs, downloading...")
            success = 0
            with Progress(console=console, transient=False) as progress:
                task = progress.add_task("Downloading BaFin", total=len(links))
                for filename, url in links:
                    dest = settings.raw_dir / filename
                    if download_file(client, url, dest):
                        success += 1
                    progress.advance(task)
                    time.sleep(settings.request_delay_seconds)
            console.print(f"  [dim]{success}/{len(links)} BaFin PDFs downloaded[/dim]\n")

    # Final inventory
    pdfs = sorted(settings.raw_dir.glob("*.pdf"))
    total_mb = sum(p.stat().st_size for p in pdfs) / 1e6
    console.rule(f"[bold green]Done — {len(pdfs)} PDFs total ({total_mb:.1f} MB)")
    for p in pdfs[:20]:
        console.print(f"  [dim]{p.stat().st_size / 1024:>8.1f} KB[/dim]  {p.name}")
    if len(pdfs) > 20:
        console.print(f"  [dim]... and {len(pdfs) - 20} more[/dim]")


if __name__ == "__main__":
    main()
