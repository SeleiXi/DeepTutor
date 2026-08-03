from __future__ import annotations

import importlib.util
import json
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace
from unittest import mock

import pytest


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "feature_lab.py"
    spec = importlib.util.spec_from_file_location("feature_lab_under_test", module_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def feature_lab():
    return _load_module()


def test_manifest_has_unique_keys_branches_and_ports(feature_lab) -> None:
    features = feature_lab.FEATURES

    assert len(features) == 8
    assert len({feature.key for feature in features}) == len(features)
    assert len({feature.branch for feature in features}) == len(features)
    assert len({feature.backend_port for feature in features}) == len(features)
    assert len({feature.frontend_port for feature in features}) == len(features)
    assert all(feature.branch.startswith("feat/") for feature in features)

    guided = next(feature for feature in features if feature.key == "guided-question")
    assert guided.branch == "feat/ask-questions-capability"
    assert guided.label == "Ask Questions capability"
    codebuddy = next(feature for feature in features if feature.key == "codebuddy")
    assert codebuddy.branch == "feat/codebuddy-provider"
    assert (codebuddy.backend_port, codebuddy.frontend_port) == (8817, 3817)


def test_select_features_accepts_keys_branches_and_all(feature_lab) -> None:
    first, second = feature_lab.FEATURES[:2]

    assert feature_lab.select_features(None) == list(feature_lab.FEATURES)
    assert feature_lab.select_features(["all"]) == list(feature_lab.FEATURES)
    assert feature_lab.select_features([first.key, second.branch, first.key]) == [first, second]

    with pytest.raises(feature_lab.LabError, match="Unknown variant"):
        feature_lab.select_features(["missing"])
    with pytest.raises(feature_lab.LabError, match="cannot be combined"):
        feature_lab.select_features(["all", first.key])


def test_prepare_runtime_seeds_once_and_rewrites_local_ports(feature_lab, tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    seed_settings = repo / "data" / "user" / "settings"
    seed_settings.mkdir(parents=True)
    (seed_settings / "models.json").write_text('{"provider":"test"}', encoding="utf-8")
    (seed_settings / "system.json").write_text(
        json.dumps(
            {
                "version": 1,
                "backend_port": 8001,
                "frontend_port": 3782,
                "next_public_api_base": "http://localhost:8001",
                "custom": "keep",
            }
        ),
        encoding="utf-8",
    )
    lab_root = tmp_path / "lab"
    feature = feature_lab.FEATURES[0]

    home = feature_lab.prepare_runtime(
        repo,
        lab_root,
        feature,
        seed_home=repo,
        seed_settings=True,
    )
    settings = home / "data" / "user" / "settings"
    system = json.loads((settings / "system.json").read_text(encoding="utf-8"))

    assert json.loads((settings / "models.json").read_text(encoding="utf-8")) == {
        "provider": "test"
    }
    assert system["backend_port"] == feature.backend_port
    assert system["frontend_port"] == feature.frontend_port
    assert system["next_public_api_base"] == ""
    assert system["custom"] == "keep"

    (settings / "models.json").write_text('{"provider":"variant"}', encoding="utf-8")
    feature_lab.prepare_runtime(
        repo,
        lab_root,
        feature,
        seed_home=repo,
        seed_settings=True,
    )
    assert json.loads((settings / "models.json").read_text(encoding="utf-8")) == {
        "provider": "variant"
    }


def test_normalized_argv_defaults_to_start(feature_lab) -> None:
    assert feature_lab._normalized_argv([]) == ["start"]
    assert feature_lab._normalized_argv(["guided-question"]) == [
        "start",
        "guided-question",
    ]
    assert feature_lab._normalized_argv(["status"]) == ["status"]


def test_dependency_check_reports_install_command(feature_lab) -> None:
    available = {"fastapi", "uvicorn"}
    with mock.patch.object(
        feature_lab.importlib.util,
        "find_spec",
        side_effect=lambda module: object() if module in available else None,
    ):
        with pytest.raises(feature_lab.LabError, match="python-multipart"):
            feature_lab.check_python_dependencies()


def test_process_match_requires_both_script_and_runtime_home(feature_lab) -> None:
    state = {
        "script": r"C:\lab\worktree\scripts\start_web.py",
        "runtime_home": r"C:\lab\runtime\guided-question",
    }
    command = (
        r"python C:\lab\worktree\scripts\start_web.py "
        r"--home C:\lab\runtime\guided-question"
    )

    with mock.patch.object(feature_lab, "process_command", return_value=command):
        assert feature_lab.process_matches_state(123, state)
    with mock.patch.object(
        feature_lab,
        "process_command",
        return_value=r"python C:\other\scripts\start_web.py --home C:\other",
    ):
        assert not feature_lab.process_matches_state(123, state)


def test_spawn_launcher_pins_pythonpath_and_records_state(feature_lab, tmp_path: Path) -> None:
    lab_root = tmp_path / "lab"
    feature = feature_lab.FEATURES[0]
    worktree = feature_lab.worktree_path(lab_root, feature)
    worktree.mkdir(parents=True)
    home = feature_lab.runtime_home(lab_root, feature)
    captured: dict[str, object] = {}

    def fake_popen(command, **kwargs):
        captured["command"] = command
        captured["kwargs"] = kwargs
        return SimpleNamespace(pid=4321)

    with (
        mock.patch.object(feature_lab.subprocess, "Popen", side_effect=fake_popen),
        mock.patch.object(feature_lab, "_git_at", return_value="abc123"),
    ):
        pid = feature_lab._spawn_launcher(lab_root, feature, home)

    assert pid == 4321
    environment = captured["kwargs"]["env"]
    assert environment["PYTHONPATH"].split(feature_lab.os.pathsep)[0] == str(
        worktree.resolve()
    )
    state = json.loads(
        feature_lab.state_path(lab_root, feature).read_text(encoding="utf-8")
    )
    assert state["launcher_pid"] == 4321
    assert state["commit"] == "abc123"
    assert state["runtime_home"] == str(home.resolve())


def test_stop_refuses_recycled_or_unrelated_pid(feature_lab, tmp_path: Path) -> None:
    feature = feature_lab.FEATURES[0]
    lab_root = tmp_path / "lab"
    feature_lab._atomic_write_json(
        feature_lab.state_path(lab_root, feature),
        {"launcher_pid": 555, "script": "expected", "runtime_home": "expected-home"},
    )

    with (
        mock.patch.object(feature_lab, "pid_alive", return_value=True),
        mock.patch.object(feature_lab, "process_matches_state", return_value=False),
        mock.patch.object(feature_lab, "_stop_process_tree") as stop_tree,
    ):
        assert not feature_lab.stop_variants(lab_root, [feature])

    stop_tree.assert_not_called()
    assert feature_lab.state_path(lab_root, feature).exists()


def test_setup_does_not_update_a_live_worktree(feature_lab, tmp_path: Path) -> None:
    feature = feature_lab.FEATURES[0]
    with (
        mock.patch.object(feature_lab, "_git"),
        mock.patch.object(
            feature_lab,
            "variant_status",
            return_value={"status": "ready"},
        ),
        mock.patch.object(feature_lab, "ensure_worktree") as ensure_worktree,
        mock.patch.object(feature_lab, "ensure_frontend_dependencies") as ensure_dependencies,
    ):
        feature_lab.setup_variants(
            tmp_path / "repo",
            tmp_path / "lab",
            [feature],
            fetch=False,
        )

    ensure_worktree.assert_not_called()
    ensure_dependencies.assert_not_called()


def test_ensure_worktree_preserves_dirty_existing_checkout(
    feature_lab, tmp_path: Path
) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    lab_root = tmp_path / "lab"
    feature = feature_lab.FEATURES[0]
    target = feature_lab.worktree_path(lab_root, feature)
    (target / ".git").mkdir(parents=True)

    show_ref = subprocess.CompletedProcess(["git"], 0)
    with (
        mock.patch.object(feature_lab.subprocess, "run", return_value=show_ref),
        mock.patch.object(
            feature_lab,
            "_git_at",
            side_effect=["changed.py\n", "old-commit"],
        ),
        mock.patch.object(feature_lab, "_git", return_value="new-commit"),
    ):
        with pytest.raises(feature_lab.LabError, match="local changes"):
            feature_lab.ensure_worktree(repo, lab_root, feature)
