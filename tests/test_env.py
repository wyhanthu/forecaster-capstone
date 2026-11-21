import os

from forecaster.env import load_env, parse_env


def test_parse_env_handles_comments_quotes_and_export():
    text = '# comment\n\nFC_TEST_A=plain\nexport FC_TEST_B="quoted value"\nFC_TEST_C=\'single\'\nFC_TEST_D=val # trailing\nbadline\n'
    assert parse_env(text) == {"FC_TEST_A": "plain", "FC_TEST_B": "quoted value", "FC_TEST_C": "single", "FC_TEST_D": "val"}


def test_load_env_sets_missing_variables_but_never_overrides(tmp_path, monkeypatch):
    f = tmp_path / ".env"
    f.write_text("FC_TEST_NEW=from_file\nFC_TEST_KEEP=from_file\n")
    monkeypatch.delenv("FC_TEST_NEW", raising=False)
    monkeypatch.setenv("FC_TEST_KEEP", "from_shell")
    assert load_env([f]) == f
    assert os.environ["FC_TEST_NEW"] == "from_file" and os.environ["FC_TEST_KEEP"] == "from_shell"
    monkeypatch.delenv("FC_TEST_NEW", raising=False)


def test_load_env_without_a_file_is_a_noop(tmp_path):
    assert load_env([tmp_path / "missing.env"]) is None


def test_gitignore_keeps_the_key_file_out_of_git():
    from pathlib import Path
    lines = (Path(__file__).resolve().parent.parent / ".gitignore").read_text().splitlines()
    assert ".env" in lines and "!.env.example" in lines


def test_env_is_also_found_in_the_folder_above_the_project(tmp_path, monkeypatch):
    import forecaster.env as envmod
    from pathlib import Path
    root = tmp_path / "top"
    (root / "FinalProject" / "forecaster").mkdir(parents=True)
    (root / ".env").write_text("FC_TEST_PARENT=found\n")
    monkeypatch.delenv("FC_TEST_PARENT", raising=False)
    monkeypatch.chdir(root / "FinalProject")
    monkeypatch.setattr(envmod, "__file__", str(root / "FinalProject" / "forecaster" / "env.py"))
    assert envmod.load_env() == root / ".env" and os.environ["FC_TEST_PARENT"] == "found"
    monkeypatch.delenv("FC_TEST_PARENT", raising=False)


def test_empty_values_in_env_count_as_not_set(monkeypatch):
    from forecaster.llm import LLMClient
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setenv("OPENAI_BASE_URL", "")
    monkeypatch.setenv("OPENAI_MODEL", "")
    c = LLMClient()
    assert c.enabled and c.model_name == "gpt-4.1-mini"
    assert "enabled" in c.status()
    monkeypatch.delenv("OPENAI_API_KEY")
    assert "disabled" in LLMClient().status()


def test_temperature_is_not_sent_unless_requested(monkeypatch):
    from forecaster.llm import LLMClient
    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.delenv("OPENAI_TEMPERATURE", raising=False)
    assert LLMClient()._model.temperature is None          # some models reject the parameter (HTTP 400)
    monkeypatch.setenv("OPENAI_TEMPERATURE", "0")
    assert LLMClient()._model.temperature == 0.0


def test_llm_check_reports_versions_and_a_disabled_client(monkeypatch, capsys, tmp_path):
    from forecaster.cli import main
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("forecaster.cli.load_env", lambda *a, **k: None)
    assert main(["llm-check"]) == 1
    out = capsys.readouterr().out
    assert "LLM: disabled" in out and "langchain-core" in out and "python " in out
