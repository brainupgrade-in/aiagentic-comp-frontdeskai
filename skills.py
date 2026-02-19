"""Dynamic skill installation and management for FrontDesk AI.

Skills are Python files in SKILLS_DIR that define SKILL_META and @tool functions.
Admin users can install new skills at runtime via chat, making them immediately
available to domain workers without restart.
"""

import ast
import importlib.util
import os
import sys
import urllib.request
import urllib.parse
from html.parser import HTMLParser
from langchain_core.tools import tool

# Derive skills directory from SQLITE_DIR parent (e.g. /shared/.frontdeskai/skills/)
_SQLITE_DIR = os.getenv("SQLITE_DIR", "/shared/.sqlite")
SKILLS_DIR = os.path.join(os.path.dirname(_SQLITE_DIR), ".frontdeskai", "skills")

# Registry: {name: {"meta": {...}, "tools": [tool1, tool2, ...]}}
_loaded_skills: dict = {}

# Validation pattern for skill config keys
import re
_CONFIG_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")


def skill_config(skill_name: str, key: str) -> str | None:
    """Read a skill config value. Skills import this to get their config at runtime.

    Tries encrypted key first (skill.{name}.{key}_enc), falls back to plain
    (skill.{name}.{key}). Returns None if not found or decryption fails.
    """
    from tools import _get_system_config, _decrypt_value

    # Try encrypted version first
    enc_val = _get_system_config(f"skill.{skill_name}.{key}_enc")
    if enc_val:
        try:
            return _decrypt_value(enc_val)
        except Exception:
            return None  # SECRET_KEY rotated — skill should handle gracefully

    # Fall back to plain value
    plain_val = _get_system_config(f"skill.{skill_name}.{key}")
    return plain_val if plain_val else None


# ========== HTML Text Extractor ==========

class _HTMLTextExtractor(HTMLParser):
    """Simple HTML-to-text extractor using stdlib html.parser."""

    def __init__(self):
        super().__init__()
        self._text_parts: list[str] = []
        self._skip = False

    def handle_starttag(self, tag, attrs):
        if tag in ("script", "style", "noscript"):
            self._skip = True

    def handle_endtag(self, tag):
        if tag in ("script", "style", "noscript"):
            self._skip = False
        if tag in ("p", "br", "div", "li", "h1", "h2", "h3", "h4", "h5", "h6", "tr"):
            self._text_parts.append("\n")

    def handle_data(self, data):
        if not self._skip:
            self._text_parts.append(data)

    def get_text(self) -> str:
        return "".join(self._text_parts).strip()


def _html_to_text(html: str) -> str:
    """Convert HTML to plain text."""
    extractor = _HTMLTextExtractor()
    extractor.feed(html)
    return extractor.get_text()


# ========== Web Tools (for skill_admin worker) ==========

@tool
def search_web(query: str) -> str:
    """Search the web using DuckDuckGo Lite. Returns search result snippets."""
    try:
        encoded = urllib.parse.urlencode({"q": query})
        url = f"https://lite.duckduckgo.com/lite/?{encoded}"
        req = urllib.request.Request(url, headers={
            "User-Agent": "FrontDeskAI/1.0 (skill-research)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            html = resp.read().decode("utf-8", errors="replace")

        text = _html_to_text(html)
        # Truncate to keep context manageable
        if len(text) > 3000:
            text = text[:3000] + "\n... (truncated)"
        return f"Search results for '{query}':\n{text}" if text else f"No results found for '{query}'."
    except Exception as e:
        return f"Search failed: {e}"


@tool
def fetch_webpage(url: str) -> str:
    """Fetch a webpage and return its text content. Useful for reading API docs."""
    # Basic URL validation
    if not url.startswith(("http://", "https://")):
        return "Invalid URL. Must start with http:// or https://"

    try:
        req = urllib.request.Request(url, headers={
            "User-Agent": "FrontDeskAI/1.0 (skill-research)"
        })
        with urllib.request.urlopen(req, timeout=15) as resp:
            content_type = resp.headers.get("Content-Type", "")
            raw = resp.read(500_000)  # limit to 500KB
            encoding = "utf-8"
            if "charset=" in content_type:
                encoding = content_type.split("charset=")[-1].split(";")[0].strip()
            html = raw.decode(encoding, errors="replace")

        text = _html_to_text(html)
        if len(text) > 4000:
            text = text[:4000] + "\n... (truncated)"
        return text if text else "Page returned no readable text content."
    except Exception as e:
        return f"Failed to fetch {url}: {e}"


# ========== Skill Configuration Tools ==========

@tool
def set_skill_config(skill_name: str, key: str, value: str, is_secret: bool = False) -> str:
    """Set a configuration value for an installed skill. Admin only.
    skill_name: lowercase skill identifier (e.g. 'weather').
    key: config key name (lowercase, e.g. 'api_key', 'base_url').
    value: the config value to store.
    is_secret: if true, the value is encrypted at rest (use for API keys, tokens)."""
    from auth import current_user_email
    from tools import _set_system_config, _encrypt_value

    try:
        email = current_user_email.get()
    except LookupError:
        email = "unknown"

    # Validate skill_name
    if not skill_name.isidentifier() or not skill_name.islower():
        return f"Invalid skill name '{skill_name}'. Must be a lowercase Python identifier."

    # Validate key
    if not _CONFIG_KEY_RE.match(key):
        return f"Invalid config key '{key}'. Must match pattern: lowercase letter followed by lowercase letters, digits, or underscores."

    if not value:
        return "Config value cannot be empty."

    if is_secret:
        encrypted = _encrypt_value(value)
        _set_system_config(f"skill.{skill_name}.{key}_enc", encrypted, email)
        return f"Skill config set: {skill_name}.{key} = *** (encrypted)"
    else:
        _set_system_config(f"skill.{skill_name}.{key}", value, email)
        return f"Skill config set: {skill_name}.{key} = {value}"


@tool
def get_skill_config(skill_name: str) -> str:
    """Show all configuration values for a skill. Encrypted values are masked.
    Also shows declared config_keys from SKILL_META with MISSING status if not set."""
    from tools import _get_db

    # Validate skill_name
    if not skill_name.isidentifier() or not skill_name.islower():
        return f"Invalid skill name '{skill_name}'. Must be a lowercase Python identifier."

    # Query all config keys for this skill
    prefix = f"skill.{skill_name}."
    conn = _get_db()
    try:
        rows = conn.execute(
            "SELECT key, value FROM system_config WHERE key LIKE ?",
            (f"{prefix}%",),
        ).fetchall()
    finally:
        conn.close()

    # Build a map of configured keys
    configured = {}
    for row in rows:
        raw_key = row["key"][len(prefix):]  # strip prefix
        if raw_key.endswith("_enc"):
            display_key = raw_key[:-4]  # remove _enc suffix
            configured[display_key] = "*** (encrypted)"
        else:
            configured[raw_key] = row["value"]

    # Check SKILL_META for declared config_keys
    skill_info = _loaded_skills.get(skill_name)
    declared_keys = []
    if skill_info:
        declared_keys = skill_info["meta"].get("config_keys", [])

    lines = [f"Configuration for skill '{skill_name}':"]

    if not configured and not declared_keys:
        lines.append("  No configuration found or declared.")
        return "\n".join(lines)

    # Show declared keys with status
    if declared_keys:
        lines.append("  Declared config_keys:")
        for dk in declared_keys:
            if dk in configured:
                lines.append(f"    {dk}: {configured[dk]}")
            else:
                lines.append(f"    {dk}: MISSING — use set_skill_config to configure")

    # Show any extra configured keys not in declared list
    extra = {k: v for k, v in configured.items() if k not in declared_keys}
    if extra:
        if declared_keys:
            lines.append("  Additional config:")
        for k, v in sorted(extra.items()):
            lines.append(f"    {k}: {v}")

    return "\n".join(lines)


# ========== Skill Management Tools ==========

@tool
def install_skill(skill_name: str, description: str, python_code: str) -> str:
    """Install a new skill. skill_name: short lowercase name (e.g. 'weather').
    description: what the skill does. python_code: complete Python source code
    that must define SKILL_META dict and at least one @tool function."""

    # Validate skill name
    if not skill_name.isidentifier() or not skill_name.islower():
        return f"Invalid skill name '{skill_name}'. Must be a lowercase Python identifier (e.g. 'weather', 'stock_price')."

    # Validate Python syntax
    try:
        tree = ast.parse(python_code)
    except SyntaxError as e:
        return f"Syntax error in skill code: {e}"

    # Check for SKILL_META assignment
    has_meta = False
    has_tool = False
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id == "SKILL_META":
                    has_meta = True
        if isinstance(node, ast.FunctionDef):
            for decorator in node.decorator_list:
                if isinstance(decorator, ast.Name) and decorator.id == "tool":
                    has_tool = True
                elif isinstance(decorator, ast.Attribute) and decorator.attr == "tool":
                    has_tool = True

    if not has_meta:
        return "Skill code must define a SKILL_META dict (e.g. SKILL_META = {'name': '...', 'description': '...', 'categories': ['facilities'], 'config_keys': ['api_key']})."
    if not has_tool:
        return "Skill code must define at least one function decorated with @tool."

    # Ensure skills directory exists
    os.makedirs(SKILLS_DIR, exist_ok=True)

    # Save skill file
    filepath = os.path.join(SKILLS_DIR, f"{skill_name}.py")
    with open(filepath, "w") as f:
        f.write(python_code)

    # Load the skill dynamically
    try:
        load_skill(skill_name)
    except Exception as e:
        # Remove the file if loading fails
        os.remove(filepath)
        return f"Skill file saved but failed to load: {e}. File removed."

    meta = _loaded_skills[skill_name]["meta"]
    tool_names = [t.name for t in _loaded_skills[skill_name]["tools"]]
    return (
        f"Skill '{skill_name}' installed successfully!\n"
        f"  Description: {meta.get('description', description)}\n"
        f"  Categories: {', '.join(meta.get('categories', []))}\n"
        f"  Tools: {', '.join(tool_names)}\n"
        f"  File: {filepath}"
    )


@tool
def list_skills() -> str:
    """List all installed skills, refreshing from disk first."""
    load_all_skills()
    if not _loaded_skills:
        return "No skills installed yet. Use install_skill to add new capabilities."

    lines = [f"Installed skills ({len(_loaded_skills)}):"]
    for name, info in sorted(_loaded_skills.items()):
        meta = info["meta"]
        tool_names = [t.name for t in info["tools"]]
        entry = (
            f"  {name}: {meta.get('description', 'No description')}\n"
            f"    Categories: {', '.join(meta.get('categories', []))}\n"
            f"    Tools: {', '.join(tool_names)}"
        )
        config_keys = meta.get("config_keys", [])
        if config_keys:
            entry += f"\n    Config keys: {', '.join(config_keys)}"
        lines.append(entry)
    return "\n".join(lines)


# ========== Skill Loading Functions ==========

def load_skill(name: str) -> None:
    """Load a single skill module from disk, extract tools, and register it."""
    filepath = os.path.join(SKILLS_DIR, f"{name}.py")
    if not os.path.isfile(filepath):
        raise FileNotFoundError(f"Skill file not found: {filepath}")

    spec = importlib.util.spec_from_file_location(f"frontdeskai_skill_{name}", filepath)
    if spec is None or spec.loader is None:
        raise ImportError(f"Cannot create module spec for {filepath}")

    module = importlib.util.module_from_spec(spec)
    # Make langchain_core.tools available for the skill's @tool decorator
    sys.modules[f"frontdeskai_skill_{name}"] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        sys.modules.pop(f"frontdeskai_skill_{name}", None)
        raise

    # Extract SKILL_META
    meta = getattr(module, "SKILL_META", None)
    if not isinstance(meta, dict):
        sys.modules.pop(f"frontdeskai_skill_{name}", None)
        raise ValueError(f"Skill '{name}' missing valid SKILL_META dict")

    # Extract @tool decorated functions (they are BaseTool instances)
    from langchain_core.tools import BaseTool
    tools = []
    for attr_name in dir(module):
        attr = getattr(module, attr_name)
        if isinstance(attr, BaseTool):
            tools.append(attr)

    if not tools:
        sys.modules.pop(f"frontdeskai_skill_{name}", None)
        raise ValueError(f"Skill '{name}' has no @tool functions")

    _loaded_skills[name] = {"meta": meta, "tools": tools}


def load_all_skills() -> int:
    """Scan skills directory, load all valid skills. Returns count loaded."""
    if not os.path.isdir(SKILLS_DIR):
        return 0

    loaded = 0
    for filename in os.listdir(SKILLS_DIR):
        if not filename.endswith(".py") or filename.startswith("_"):
            continue
        name = filename[:-3]
        try:
            load_skill(name)
            loaded += 1
        except Exception:
            pass  # Skip invalid skills silently
    return loaded


def get_skill_tools(category: str) -> list:
    """Get all skill tools that target a given category."""
    tools = []
    for info in _loaded_skills.values():
        categories = info["meta"].get("categories", [])
        if category in categories:
            tools.extend(info["tools"])
    return tools


# ========== Exported Tool Lists ==========

SKILL_ADMIN_TOOLS = [search_web, fetch_webpage, install_skill, list_skills, set_skill_config, get_skill_config]
