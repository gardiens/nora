import click
from nora.utils.config import load_config, configure_user_config
from nora.parsers.notion import NotionLibrary
from nora.parsers.zotero import ZoteroLibrary, ZoteroItem


@click.group()
def cli():
    """NoRA – Notion Research Assistant"""
    pass


# -------------------------------------------------------------------------
#  nora configure
# -------------------------------------------------------------------------
@cli.command()
def configure():
    """Set up your API keys and Notion/Zotero configuration."""
    configure_user_config()


# -------------------------------------------------------------------------
#  nora url ...
# -------------------------------------------------------------------------
@cli.command("url")
@click.argument("url")
def url_command(url: str):
    """Process a paper from its URL (e.g., arXiv, DOI)."""
    cfg = load_config()

    # Load from url
    item = ZoteroItem.from_url(url, cfg_venues=cfg.venues)

    # Upload data to NoRA
    if item is not None:
        item.to_notion(cfg.notion, verbose=cfg.verbose)


# -------------------------------------------------------------------------
#  nora id ...
# -------------------------------------------------------------------------
@cli.command("id")
@click.argument("id")
def id_command(id: str):
    """Process a Notion item by its ID."""
    cfg = load_config()

    # Load from url
    item = ZoteroItem.from_identifier(id, cfg_venues=cfg.venues)

    # Upload data to NoRA
    if item is not None:
        item.to_notion(cfg.notion, verbose=cfg.verbose)


# -------------------------------------------------------------------------
#  nora bibtex
# -------------------------------------------------------------------------
@cli.command("bibtex")
@click.option(
    "-o", "--output",
    default="nora_papers_from_notion.bib",
    show_default=True,
    help="Path of the .bib file to write.")
@click.option(
    "--report",
    default=None,
    help="Path of the export report. Defaults to <output>.report.txt.")
@click.option(
    "--property",
    "bibtex_property",
    default="Bibtex",
    show_default=True,
    help="Name of the Notion Papers property containing BibTeX.")
def bibtex_command(output: str, report: str, bibtex_property: str):
    """Export all BibTeX entries from the Notion Papers database."""
    cfg = load_config()
    stats = NotionLibrary(cfg.notion).export_papers_bibtex(
        output_path=output,
        report_path=report,
        bibtex_property=bibtex_property)

    click.echo(f"✅ BibTeX written to {stats['output_path']}")
    click.echo(f"ℹ️ Report written to {stats['report_path']}")
    click.echo(f"ℹ️ Total Notion pages: {stats['total_pages']}")
    click.echo(f"ℹ️ Valid BibTeX entries written: {stats['valid']}")
    click.echo(f"ℹ️ Missing BibTeX: {stats['missing']}")
    click.echo(f"ℹ️ Invalid BibTeX skipped: {stats['invalid']}")
    click.echo(f"ℹ️ Duplicate BibTeX keys skipped: {stats['duplicates']}")


# -------------------------------------------------------------------------
#  nora zotero-upload
# -------------------------------------------------------------------------
@cli.command("zotero-upload")
def zotero_upload_command():
    """Upload items to Zotero."""
    click.echo("📚 Uploading Zotero to NoRA")

    cfg = load_config()

    # Load from url
    item = ZoteroLibrary(cfg.zotero, cfg_venues=cfg.venues, verbose=cfg.verbose)

    # Upload data to NoRA
    if item is not None:
        item.to_notion(cfg.notion, verbose=cfg.verbose)
