"""Tests for the dynamic skills system: install, load, list, config."""

import os
import sys
import textwrap
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "app"))

VALID_SKILL = textwrap.dedent("""\
    from langchain_core.tools import tool

    SKILL_META = {
        "name": "test_skill",
        "description": "A test skill for unit testing",
        "categories": ["tech"],
        "config_keys": ["api_key"],
    }

    @tool
    def test_skill_greet(name: str) -> str:
        \"\"\"Greet someone by name.\"\"\"
        return f"Hello, {name}!"
""")

SKILL_NO_META = textwrap.dedent("""\
    from langchain_core.tools import tool

    @tool
    def some_tool(x: str) -> str:
        \"\"\"Does something.\"\"\"
        return x
""")

SKILL_NO_TOOLS = textwrap.dedent("""\
    SKILL_META = {
        "name": "empty_skill",
        "description": "No tools here",
        "categories": ["hr"],
    }

    def not_a_tool():
        pass
""")

SKILL_SYNTAX_ERROR = "def broken(: invalid python syntax"


@pytest.fixture(autouse=True)
def setup_skills_env(env):
    """Ensure DB is ready and skills dir exists."""
    from tools import _get_db
    _get_db().close()


class TestInstallSkill:
    def test_install_valid_skill(self, env):
        from skills import install_skill
        result = install_skill.invoke({
            "skill_name": "test_skill",
            "description": "A test skill",
            "python_code": VALID_SKILL,
        })
        assert "test_skill" in result or "installed" in result.lower() or "success" in result.lower()

    def test_skill_file_written_to_disk(self, env):
        import skills
        from skills import install_skill
        install_skill.invoke({
            "skill_name": "disk_skill",
            "description": "Test disk write",
            "python_code": VALID_SKILL.replace("test_skill", "disk_skill"),
        })
        skill_file = os.path.join(skills.SKILLS_DIR, "disk_skill.py")
        assert os.path.isfile(skill_file)

    def test_reject_skill_without_skill_meta(self, env):
        from skills import install_skill
        result = install_skill.invoke({
            "skill_name": "bad_skill",
            "description": "Missing SKILL_META",
            "python_code": SKILL_NO_META,
        })
        assert "SKILL_META" in result or "invalid" in result.lower() or "error" in result.lower()

    def test_reject_skill_without_tools(self, env):
        from skills import install_skill
        result = install_skill.invoke({
            "skill_name": "no_tools",
            "description": "No @tool functions",
            "python_code": SKILL_NO_TOOLS,
        })
        assert "tool" in result.lower() or "invalid" in result.lower() or "error" in result.lower()

    def test_reject_skill_with_syntax_error(self, env):
        from skills import install_skill
        result = install_skill.invoke({
            "skill_name": "broken_skill",
            "description": "Syntax error",
            "python_code": SKILL_SYNTAX_ERROR,
        })
        assert "syntax" in result.lower() or "error" in result.lower() or "invalid" in result.lower()


class TestLoadSkills:
    def test_load_installed_skill(self, env):
        import skills
        from skills import install_skill
        install_skill.invoke({
            "skill_name": "load_test",
            "description": "Load test",
            "python_code": VALID_SKILL.replace("test_skill", "load_test").replace("test_skill_greet", "load_test_greet"),
        })
        skills.load_skill("load_test")
        assert "load_test" in skills._loaded_skills

    def test_load_all_skills_returns_count(self, env):
        import skills
        from skills import install_skill
        install_skill.invoke({
            "skill_name": "count_skill",
            "description": "Count test",
            "python_code": VALID_SKILL.replace("test_skill", "count_skill").replace("test_skill_greet", "count_skill_greet"),
        })
        count = skills.load_all_skills()
        assert count >= 1

    def test_get_skill_tools_returns_tools_for_category(self, env):
        import skills
        from skills import install_skill
        install_skill.invoke({
            "skill_name": "tech_skill",
            "description": "Tech skill",
            "python_code": VALID_SKILL.replace("test_skill", "tech_skill")
                                      .replace("test_skill_greet", "tech_skill_greet")
                                      .replace('"tech"', '"tech"'),
        })
        tools = skills.get_skill_tools("tech")
        assert len(tools) >= 1

    def test_get_skill_tools_empty_for_unknown_category(self, env):
        import skills
        tools = skills.get_skill_tools("nonexistent_category")
        assert tools == []


class TestListSkills:
    def test_list_shows_installed_skill(self, env):
        from skills import install_skill, list_skills
        install_skill.invoke({
            "skill_name": "listable_skill",
            "description": "Listable",
            "python_code": VALID_SKILL.replace("test_skill", "listable_skill")
                                      .replace("test_skill_greet", "listable_skill_greet"),
        })
        result = list_skills.invoke({})
        assert "listable_skill" in result or "listable" in result.lower()

    def test_list_empty_when_no_skills(self, env, tmp_path, monkeypatch):
        import skills
        # Point skills dir to empty temp dir
        empty_dir = str(tmp_path / "empty_skills")
        os.makedirs(empty_dir)
        monkeypatch.setattr(skills, "SKILLS_DIR", empty_dir)
        monkeypatch.setattr(skills, "_loaded_skills", {})
        from skills import list_skills
        result = list_skills.invoke({})
        assert "no skill" in result.lower() or result.strip() == "" or "0" in result


class TestSkillConfig:
    def test_set_and_get_plain_config(self, env):
        from skills import set_skill_config, get_skill_config, install_skill
        install_skill.invoke({
            "skill_name": "config_skill",
            "description": "Config test",
            "python_code": VALID_SKILL.replace("test_skill", "config_skill")
                                      .replace("test_skill_greet", "config_skill_greet"),
        })
        set_skill_config.invoke({
            "skill_name": "config_skill",
            "key": "base_url",
            "value": "https://api.example.com",
            "is_secret": False,
        })
        result = get_skill_config.invoke({"skill_name": "config_skill"})
        assert "https://api.example.com" in result or "base_url" in result

    def test_secret_config_is_masked_in_output(self, env):
        from skills import set_skill_config, get_skill_config, install_skill
        install_skill.invoke({
            "skill_name": "secret_skill",
            "description": "Secret test",
            "python_code": VALID_SKILL.replace("test_skill", "secret_skill")
                                      .replace("test_skill_greet", "secret_skill_greet"),
        })
        set_skill_config.invoke({
            "skill_name": "secret_skill",
            "key": "api_key",
            "value": "sk-supersecret123",
            "is_secret": True,
        })
        result = get_skill_config.invoke({"skill_name": "secret_skill"})
        assert "sk-supersecret123" not in result
        assert "***" in result or "encrypted" in result.lower() or "api_key" in result

    def test_skill_config_readable_at_runtime(self, env):
        from skills import set_skill_config, skill_config, install_skill
        install_skill.invoke({
            "skill_name": "runtime_skill",
            "description": "Runtime config test",
            "python_code": VALID_SKILL.replace("test_skill", "runtime_skill")
                                      .replace("test_skill_greet", "runtime_skill_greet"),
        })
        set_skill_config.invoke({
            "skill_name": "runtime_skill",
            "key": "endpoint",
            "value": "https://runtime.example.com",
            "is_secret": False,
        })
        value = skill_config("runtime_skill", "endpoint")
        assert value == "https://runtime.example.com"
