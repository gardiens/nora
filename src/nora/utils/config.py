import re
import sys
import yaml
import copy
from pathlib import Path
from typing import Union
from omegaconf import OmegaConf


CONFIG_YAML = "config.yaml"
USER_YAML = "user.yaml"

# Fields expected in each section of the user config. The values are the
# aliases accepted when parsing a config file with `nora configure
# --from-file`, so that users may write their keys in the way that feels
# most natural to them
NOTION_ALIASES = {
    "token": (
        "token", "notion_token", "integration_token", "secret", "api_key",
        "api_token"),
    "papers_db_id": ("papers_db_id", "papers_db", "papers_database_id", "papers"),
    "people_db_id": ("people_db_id", "people_db", "people_database_id", "people"),
    "affiliations_db_id": (
        "affiliations_db_id", "affiliations_db", "affiliations_database_id",
        "affiliations"),
    "venues_db_id": (
        "venues_db_id", "venues_db", "venues_database_id", "venues",
        "conferences_db_id"),
    "topics_db_id": ("topics_db_id", "topics_db", "topics_database_id", "topics"),
}

ZOTERO_ALIASES = {
    "library_id": ("library_id", "library", "user_id", "userid", "zotero_library_id"),
    "api_token": (
        "api_token", "api_key", "token", "key", "zotero_api_token",
        "zotero_api_key"),
}

# Sections whose keys are normalized when parsing a config file
SECTION_ALIASES = {"notion": NOTION_ALIASES, "zotero": ZOTERO_ALIASES}

# Keys without which NoRA cannot talk to Notion
REQUIRED_NOTION_KEYS = tuple(NOTION_ALIASES.keys())

# Notion database IDs are 32 hexadecimal characters, possibly dash-separated
# and possibly embedded in a Notion URL
NOTION_ID_RE = re.compile(r"[0-9a-fA-F]{32}")


def load_yaml(path: Union[str, Path]):
    path = Path(path)
    if not path.exists():
        return {}
    with path.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f) or {}


def save_yaml(data: dict, path: Union[str, Path]):
    """Write a YAML file, preserving non-ASCII characters (emojis are
    used in the default Notion property names).
    """
    path = Path(path)
    with path.open("w", encoding="utf-8") as f:
        yaml.dump(data, f, allow_unicode=True, sort_keys=False)


def deep_merge(base: dict, overrides: dict) -> dict:
    """Recursively merge overrides into base (in place)."""
    for k, v in overrides.items():
        if isinstance(base.get(k), dict) and isinstance(v, dict):
            deep_merge(base[k], v)
        else:
            base[k] = copy.deepcopy(v)
    return base


def get_config_path():
    return Path(__file__).resolve().parent.parent / "configs" / CONFIG_YAML


def get_user_config_path():
    config_dir = Path.home() / ".nora"
    config_dir.mkdir(parents=True, exist_ok=True)
    return config_dir / USER_YAML


# -------------------------------------------------------------------------
#  Parsing a config file
# -------------------------------------------------------------------------

def normalize_key(key) -> str:
    """Normalize a config key: lowercase, no dash, no surrounding space."""
    return str(key).strip().lower().replace("-", "_").replace(" ", "_")


def normalize_notion_id(value) -> str:
    """Normalize a Notion database ID.

    Accepts a bare 32-character ID, a dash-separated UUID, or a full
    Notion URL such as
    `https://www.notion.so/workspace/94f7cec0...?v=1157cec0...` and
    returns the bare 32-character ID. Values which do not look like a
    Notion ID are returned stripped, unchanged.
    """
    value = str(value).strip()

    # Drop the query string, so that a `?v=<view_id>` in a Notion URL is
    # not mistaken for the database ID
    candidate = value.split("?")[0]

    # Keep the last path element of a URL, if any
    candidate = candidate.rstrip("/").split("/")[-1]

    # Notion URLs look like `Some-Page-Title-<id>`, and IDs may be
    # dash-separated UUIDs: removing dashes covers both
    match = NOTION_ID_RE.search(candidate.replace("-", ""))
    return match.group(0) if match else value


def normalize_value(key: str, value):
    """Cast a parsed value to the string NoRA expects."""
    if value is None:
        return None
    if isinstance(value, (dict, list)):
        return value
    value = str(value).strip()
    if value in ("", "???"):
        # `???` is the placeholder used in the default config for keys
        # the user has not set yet
        return None
    if key.endswith("_db_id"):
        return normalize_notion_id(value)
    return value


def parse_config_file(path: Union[str, Path]) -> dict:
    """Parse a YAML file holding NoRA keys and return the corresponding
    user config overrides.

    The parser is deliberately lenient. All of the following are
    understood and produce the same result:

    ```yaml
    notion:
        token: ntn_XXX
        papers_db_id: 5767cec05de48292b825017d21484519
    ```

    ```yaml
    notion:
        token: ntn_XXX
        papers_db: https://www.notion.so/ws/5767cec0-5de4-8292-b825-017d21484519?v=...
    ```

    ```yaml
    notion_token: ntn_XXX
    notion_papers_db_id: 5767cec05de48292b825017d21484519
    ```

    Unknown keys are preserved as-is, so that a config file may also
    carry settings NoRA does not know about yet.
    """
    path = Path(path).expanduser()
    if not path.exists():
        print(f"❌ No such config file: {path}")
        sys.exit(1)

    try:
        raw = load_yaml(path)
    except yaml.YAMLError as error:
        print(f"❌ Could not parse {path} as YAML:\n{error}")
        sys.exit(1)

    if not isinstance(raw, dict):
        print(f"❌ Expected {path} to hold a YAML mapping, got a {type(raw)}")
        sys.exit(1)

    cfg = {}
    unknown = []

    for raw_key, raw_value in raw.items():
        key = normalize_key(raw_key)

        # A `notion:` or `zotero:` section: normalize the field names it
        # holds using the aliases above
        if key in SECTION_ALIASES and isinstance(raw_value, dict):
            section = cfg.setdefault(key, {})
            for raw_sub_key, raw_sub_value in raw_value.items():
                sub_key = normalize_key(raw_sub_key)
                canonical = resolve_alias(sub_key, SECTION_ALIASES[key])
                if canonical is None:
                    canonical = sub_key
                    unknown.append(f"{key}.{raw_sub_key}")
                value = normalize_value(canonical, raw_sub_value)
                if value is not None:
                    section[canonical] = value
            continue

        # A flat `notion_token: XXX` style key
        routed = False
        for section_name, aliases in SECTION_ALIASES.items():
            stripped = key[len(section_name) + 1:] \
                if key.startswith(f"{section_name}_") else None
            for candidate in filter(None, (stripped, key)):
                canonical = resolve_alias(candidate, aliases)
                # Only route an unprefixed key when it is unambiguous
                if canonical is None or (
                        stripped is None and is_ambiguous(candidate)):
                    continue
                value = normalize_value(canonical, raw_value)
                if value is not None:
                    cfg.setdefault(section_name, {})[canonical] = value
                routed = True
                break
            if routed:
                break
        if routed:
            continue

        # Anything else (`verbose`, `venues`, ...) is passed through
        cfg[key] = raw_value

    if unknown:
        print(
            f"ℹ️ Ignoring key(s) NoRA does not use: {', '.join(unknown)} "
            f"(they will still be saved in your config)")

    return cfg


def resolve_alias(key: str, aliases: dict):
    """Return the canonical field name for a key, or None if unknown."""
    for canonical, candidates in aliases.items():
        if key == canonical or key in candidates:
            return canonical
    return None


def is_ambiguous(key: str) -> bool:
    """True if an unprefixed key could belong to several sections."""
    sections = [
        name for name, aliases in SECTION_ALIASES.items()
        if resolve_alias(key, aliases) is not None]
    return len(sections) > 1


def report_missing_keys(user_cfg: dict):
    """Warn about the Notion keys still missing from a user config."""
    notion = user_cfg.get("notion", {}) or {}
    missing = [
        k for k in REQUIRED_NOTION_KEYS
        if not notion.get(k) or notion.get(k) == "???"]
    if not missing:
        return
    print("\n⚠️ The following Notion keys are still missing:")
    for k in missing:
        print(f"  - notion.{k}")
    print(
        "👉 Add them to your config file and run `nora configure "
        "--from-file` again, or run `nora configure` to fill them in "
        "interactively.")


# -------------------------------------------------------------------------
#  Configuration
# -------------------------------------------------------------------------

def prompt_user_config() -> dict:
    """Interactively ask the user for their API keys."""
    print(f"Let's configure your NoRA keys:\n")

    notion_token = input("Enter your Notion integration token: ")
    notion_papers_db_id = input("Enter your Notion Papers database ID: ")
    notion_people_db_id = input("Enter your Notion People database ID: ")
    notion_affiliations_db_id = input("Enter your Notion Affiliations database ID: ")
    notion_venues_db_id = input("Enter your Notion Venues database ID: ")
    notion_topics_db_id = input("Enter your Notion Topics database ID: ")

    zotero_library_id = input("Enter your Zotero library ID (optional): ")
    zotero_api_token = input("Enter your Zotero API token (optional): ")

    return {
        "notion": {
            "token": notion_token,
            "papers_db_id": normalize_notion_id(notion_papers_db_id),
            "people_db_id": normalize_notion_id(notion_people_db_id),
            "affiliations_db_id": normalize_notion_id(notion_affiliations_db_id),
            "venues_db_id": normalize_notion_id(notion_venues_db_id),
            "topics_db_id": normalize_notion_id(notion_topics_db_id),
        },
        "zotero": {
            "library_id": zotero_library_id,
            "api_token": zotero_api_token,
        },
    }


def configure_user_config(from_file: Union[str, Path]=None):
    """Create ~/.nora/user.yaml with the user API keys.

    :param from_file: optional path to a YAML file holding the keys. When
        provided, the keys are parsed from that file instead of being
        asked interactively. Keys absent from the file keep the value
        they already have in ~/.nora/user.yaml, so a partial file may be
        used to update only some of the keys.
    """
    config_path = get_user_config_path()

    if from_file is None:
        user_cfg = prompt_user_config()
    else:
        user_cfg = parse_config_file(from_file)
        # Only update the keys the file actually carries, so a partial
        # file does not wipe out previously-configured keys
        user_cfg = deep_merge(load_yaml(config_path), user_cfg)
        print(f"ℹ️ Read your NoRA keys from {Path(from_file).expanduser()}")

    save_yaml(user_cfg, config_path)

    # Load the user-specific config along with static config it not
    # overwritten by the user config. Then save all into the new user
    # config file. This allows exposing explicitly in the user's config
    # all the configuration variables, while preserving any
    # already-defined keys before this configuration call
    cfg_full = OmegaConf.to_container(load_config(), resolve=True)
    save_yaml(cfg_full, config_path)

    print(f"✅ Configuration saved to {config_path}")
    report_missing_keys(cfg_full)


def load_user_config(depth: int=0):
    """Load user keys from ~/.nora/user.yaml"""
    config_path = get_user_config_path()
    if config_path.exists():
        return load_yaml(config_path)
    elif depth < 1:
        configure_user_config()
        return load_user_config(depth=1)
    else:
        print(f"⚠️ No {USER_YAML} file found. Run `nora configure` first.")
        raise SystemExit(1)


def load_config():
    """
    Loads NoRA configuration by merging:
      1. Static defaults from src/nora/configs/config.yaml
      2. User-specific overrides from ~/.nora/user.yaml
    """
    # Find base config directory (relative to installed package).
    # Normally this call could be bypassed if configure_user_config()
    # was properly called. But this allows for making up for users
    # potentially tampering with their private config file and deleting
    # essential keys
    cfg = load_yaml(get_config_path())

    # Get config holding user keys
    user_cfg = load_user_config()
    if user_cfg:
        for k, v in user_cfg.items():
            if k in cfg and isinstance(v, dict):
                deep_merge(cfg[k], v)
            else:
                cfg[k] = v

    return OmegaConf.create(cfg)
