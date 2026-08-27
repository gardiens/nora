import sys
import click
from nora.utils.config import load_config, configure_user_config
from nora.parsers.notion import NotionLibrary
from nora.parsers.zotero import ZoteroLibrary, ZoteroItem


def enable_utf8_output():
    """Make sure the console can print the emojis NoRA uses.

    On Windows, the console defaults to a legacy code page (cp1252),
    which raises UnicodeEncodeError when printing emojis.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, OSError):
            pass


@click.group()
def cli():
    """NoRA – Notion Research Assistant"""
    enable_utf8_output()


# -------------------------------------------------------------------------
#  nora configure
# -------------------------------------------------------------------------
@cli.command()
@click.option(
    "-f", "--from-file",
    "from_file",
    type=click.Path(dir_okay=False),
    default=None,
    help="Read the keys from a YAML file instead of prompting for them.")
def configure(from_file: str):
    """Set up your API keys and Notion/Zotero configuration.

    Without argument, the keys are asked interactively. With
    `--from-file config.yaml`, they are parsed from a YAML file such as:

    \b
        notion:
            token: ntn_XXX
            papers_db_id: 5767cec05de48292b825017d21484519
            people_db_id: ba87cec05de483baab0a8181377894b6
            affiliations_db_id: cb07cec05de482ae81d08141fc43d7f2
            venues_db_id: 1157cec05de4834fa3db011a8b1c7a76
            topics_db_id: 88e7cec05de483d58b3281e55c529c0e
    """
    configure_user_config(from_file=from_file)


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
