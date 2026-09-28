"""kit_link.py mirrors the connections kit's fan-out-env.sh: declared names only, non-blank kit
values only, nothing removed, HOSPITABLE_TOKEN aliased, values never printed."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import kit_link as kl

EXAMPLE = "AIRROI_API_KEY=\nGEMINI_API_KEY=\nHOSPITABLE_TOKEN=\nRANKBREEZE_MCP_URL=\n# PMS_NAME=\n"


@pytest.fixture
def world(tmp_path, monkeypatch):
    lo = tmp_path / "Listing Optimizer"
    lo.mkdir()
    (lo / ".env.example").write_text(EXAMPLE, encoding="utf-8")
    kit = tmp_path / "str-secrets-connections"
    kit.mkdir()
    (kit / "CONNECTIONS.md").write_text("# kit\n", encoding="utf-8")
    (kit / "fan-out-env.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (kit / ".env").write_text("STACK_PMS=hospitable\nHOSPITABLE_API_KEY=hosp-secret\nAIRROI_API_KEY=air-secret\n"
                              "GEMINI_API_KEY=\nKIE_API_KEY=kie-secret\nSKILL_PATH_LISTING_OPTIMIZER=\n",
                              encoding="utf-8")
    monkeypatch.setattr(kl, "ROOT", lo)
    monkeypatch.setattr(kl, "ENV", lo / ".env")
    monkeypatch.setattr(kl, "EXAMPLE", lo / ".env.example")
    return lo, kit


def test_copies_declared_keys_and_aliases_hospitable(world):
    lo, kit = world
    filled, blank = kl.link(kit)
    assert set(filled) == {"AIRROI_API_KEY", "HOSPITABLE_TOKEN"}
    env = kl.read_env(lo / ".env")
    assert env["AIRROI_API_KEY"] == "air-secret" and env["HOSPITABLE_TOKEN"] == "hosp-secret"
    assert "KIE_API_KEY" not in env, "undeclared kit keys must not land here"
    assert blank == ["GEMINI_API_KEY", "RANKBREEZE_MCP_URL"]


def test_registers_this_folder_in_the_kit_as_a_posix_path(world):
    lo, kit = world
    kl.link(kit)
    kit_env = kl.read_env(kit / ".env")
    assert kit_env["SKILL_PATH_LISTING_OPTIMIZER"] == lo.resolve().as_posix()
    assert kit_env["HOSPITABLE_API_KEY"] == "hosp-secret", "nothing else in the kit's file may change"
    assert kl.read_env(kit / ".env")["KIE_API_KEY"] == "kie-secret"


def test_existing_lines_survive_and_blank_kit_values_never_overwrite(world):
    lo, kit = world
    (lo / ".env").write_text("# my note\nGEMINI_API_KEY=my-own-gemini\nAIRROI_API_KEY=old\nCUSTOM=1\n", encoding="utf-8")
    kl.link(kit)
    text = (lo / ".env").read_text(encoding="utf-8")
    assert "# my note" in text and "CUSTOM=1" in text
    env = kl.read_env(lo / ".env")
    assert env["GEMINI_API_KEY"] == "my-own-gemini", "a blank kit value must not erase a key already here"
    assert env["AIRROI_API_KEY"] == "air-secret", "a filled kit value wins, like fan-out-env.sh"
    assert env["HOSPITABLE_TOKEN"] == "hosp-secret", "missing declared names are appended"


def test_main_never_prints_a_value_and_reports_required_gaps(world, capsys):
    lo, kit = world
    rc = kl.main(["--kit", str(kit)])
    out = capsys.readouterr().out
    assert rc == 2, "GEMINI_API_KEY is required and blank in both places"
    for secret in ("hosp-secret", "air-secret", "kie-secret"):
        assert secret not in out
    assert "GEMINI_API_KEY" in out and "AIRROI_API_KEY" in out


def test_main_exit_zero_when_required_keys_present(world, capsys):
    lo, kit = world
    (kit / ".env").write_text("AIRROI_API_KEY=a\nGEMINI_API_KEY=g\n", encoding="utf-8")
    assert kl.main(["--kit", str(kit), "--no-register"]) == 0
    assert "SKILL_PATH" not in kl.read_env(kit / ".env")


def test_no_kit_is_exit_one(world, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(kl.Path, "home", staticmethod(lambda: tmp_path / "nowhere"))
    monkeypatch.delenv("STR_SECRETS_KIT", raising=False)
    monkeypatch.setattr(kl, "ROOT", tmp_path / "nowhere" / "lo")
    assert kl.main([]) == 1
    assert "no STR Secrets Connections folder" in capsys.readouterr().out


def test_find_kit_prefers_a_set_up_kit_on_the_desktop(tmp_path, monkeypatch):
    home = tmp_path / "home"
    fresh = home / "Downloads" / "str-secrets-connections"
    used = home / "Desktop" / "str-secrets-connections"
    for d in (fresh, used):
        d.mkdir(parents=True)
        (d / "CONNECTIONS.md").write_text("x", encoding="utf-8")
        (d / "fan-out-env.sh").write_text("x", encoding="utf-8")
    (used / ".env").write_text("AIRROI_API_KEY=a\n", encoding="utf-8")
    monkeypatch.setattr(kl.Path, "home", staticmethod(lambda: home))
    monkeypatch.setattr(kl, "ROOT", home / "Desktop" / "lo")
    monkeypatch.delenv("STR_SECRETS_KIT", raising=False)
    assert kl.find_kit(None) == used.resolve()
