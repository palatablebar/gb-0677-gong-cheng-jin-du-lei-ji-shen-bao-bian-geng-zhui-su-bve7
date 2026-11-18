import tempfile
from pathlib import Path

import pytest

from app import store
from app.pipeline import execute, load_sample


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(store, "DATA_DIR", tmp_path)
    store._initialised.clear()
    return tmp_path


def run(sample="dirty.csv", params=None):
    return execute(load_sample(sample), params)


def revised(raw):
    """只改一条已采纳记录的数量，制造真正的数据变化。"""
    return raw.replace(b"00002,002,2026-02,125,100", b"00002,002,2026-02,130,100")


def test_same_input_and_rules_do_not_add_version(isolated):
    first = run()
    again = run()
    assert again["reused"] is True
    assert again["run_id"] == first["run_id"]
    assert len(store.history()) == 1
    assert store.current()["run_id"] == first["run_id"]


def test_parameter_key_order_is_not_a_rule_change(isolated):
    first = execute(load_sample("clean.csv"), {"retention_rate": "0.05", "advance_rate": "0.10"})
    again = execute(load_sample("clean.csv"), {"advance_rate": "0.10", "retention_rate": "0.05"})
    assert again["run_id"] == first["run_id"]
    assert again["reused"] is True
    assert len(store.history()) == 1


def test_new_conclusion_supersedes_exactly_one_current(isolated):
    first = run("dirty.csv")
    second = run("clean.csv")
    assert second["state"] == store.CURRENT
    assert second["replaced_run_id"] == first["run_id"]
    superseded = store.get(first["run_id"])
    assert superseded["state"] == store.SUPERSEDED
    assert superseded["superseded_by"] == second["run_id"]
    assert [entry["is_current"] for entry in store.history()].count(True) == 1


def test_superseded_result_is_unchanged_and_reviewable(isolated):
    first = run("dirty.csv")
    before_review = store.review(first["run_id"], 1, "已复核")
    run("clean.csv")
    after = store.get(first["run_id"])
    assert after["decision"] == "已复核"
    assert after["version"] == before_review["version"]
    assert after["reviews"] == before_review["reviews"]
    assert after["stages"] == before_review["stages"]


def test_superseded_version_rejects_new_review(isolated):
    first = run("dirty.csv")
    run("clean.csv")
    with pytest.raises(Exception) as error:
        store.review(first["run_id"], first["version"], "已复核")
    assert getattr(error.value, "code", None) == "SUPERSEDED"
    assert store.get(first["run_id"])["reviews"] == []


def test_data_change_is_distinguished_from_rule_change(isolated):
    base = load_sample("clean.csv")
    execute(base)
    by_data = execute(revised(base))
    assert by_data["comparison"]["data_changed"] is True
    assert by_data["comparison"]["rule_changed"] is False
    assert by_data["comparison"]["causes"] == ["data"]
    assert by_data["comparison"]["records"]["by_cause"]["data"] > 0

    by_rule = execute(revised(base), {"retention_rate": "0.06"})
    assert by_rule["comparison"]["data_changed"] is False
    assert by_rule["comparison"]["rule_changed"] is True
    assert by_rule["comparison"]["causes"] == ["rules"]
    assert by_rule["comparison"]["rules"]["parameters"]["added"] == ["retention_rate"]
    assert by_rule["comparison"]["records"]["by_cause"]["data"] == 0


def test_both_kinds_of_change_are_reported(isolated):
    base = load_sample("clean.csv")
    execute(base)
    both = execute(revised(base), {"retention_rate": "0.06"})
    assert both["comparison"]["causes"] == ["data", "rules"]
    causes = {entry["cause"] for entry in both["comparison"]["records"]["details"]}
    assert "both" in causes and "rule" in causes
    revised_entry = next(entry for entry in both["comparison"]["records"]["details"] if entry["claim_id"] == "00002")
    assert revised_entry["cause"] == "both"


def test_record_change_carries_field_level_evidence(isolated):
    base = load_sample("clean.csv")
    execute(base)
    changed = execute(revised(base))
    details = changed["comparison"]["records"]["details"]
    assert details
    for entry in details:
        assert entry["cause"] in {"data", "rule"}
        if entry["change"] == "changed":
            assert entry["fields"]
    target = next(entry for entry in details if entry["claim_id"] == "00002")
    assert target["change"] == "changed"
    assert target["cause"] == "data"
    fields = {item["field"]: item for item in target["fields"]}
    assert fields["cumulative_qty"]["before"] == "125" and fields["cumulative_qty"]["after"] == "130"


def test_rule_change_metric_cause_is_rule(isolated):
    run("clean.csv")
    changed = run("clean.csv", {"retention_rate": "0.06"})
    assert changed["comparison"]["metrics"]["changes"]
    assert all(entry["cause"] == "rule" for entry in changed["comparison"]["metrics"]["changes"])


def test_activate_restores_superseded_version_and_keeps_chain(isolated):
    first = run("dirty.csv")
    second = run("clean.csv")
    restored = store.activate(first["run_id"], version=first["version"])
    assert restored["state"] == store.CURRENT
    assert restored["replaced_run_id"] == second["run_id"]
    assert store.current()["run_id"] == first["run_id"]
    assert store.get(second["run_id"])["state"] == store.SUPERSEDED
    assert store.get(second["run_id"])["superseded_by"] == first["run_id"]
    history = store.history()
    assert len(history) == 2
    assert [entry["is_current"] for entry in history].count(True) == 1


def test_activate_can_walk_back_from_current(isolated):
    first = run("dirty.csv")
    run("clean.csv")
    second = run("legacy.csv")
    back = store.activate(first["run_id"], version=first["version"])
    assert back["replaced_run_id"] == second["run_id"]
    earlier = store.activate(second["run_id"], version=second["version"])
    assert earlier["state"] == store.CURRENT
    assert store.get(first["run_id"])["state"] == store.SUPERSEDED


def test_activate_with_stale_version_changes_nothing(isolated):
    first = run("dirty.csv")
    second = run("clean.csv")
    with pytest.raises(Exception) as error:
        store.activate(first["run_id"], version=first["version"] + 5)
    assert getattr(error.value, "status", None) == 409
    assert store.current()["run_id"] == second["run_id"]
    assert store.get(first["run_id"])["state"] == store.SUPERSEDED


def test_activate_with_moved_current_changes_nothing(isolated):
    first = run("dirty.csv")
    second = run("clean.csv")
    third = run("legacy.csv")
    with pytest.raises(Exception) as error:
        store.activate(first["run_id"], version=first["version"], expected_current=second["run_id"])
    assert getattr(error.value, "code", None) == "CURRENT_MOVED"
    assert store.current()["run_id"] == third["run_id"]
    assert store.get(second["run_id"])["state"] == store.SUPERSEDED
    assert store.get(first["run_id"])["state"] == store.SUPERSEDED


def test_activate_current_is_noop_and_does_not_add_version(isolated):
    first = run("dirty.csv")
    same = store.activate(first["run_id"], version=first["version"])
    assert same["state"] == store.CURRENT
    assert len(store.history()) == 1


def test_comparison_targeting_arbitrary_version(isolated):
    first = run("dirty.csv")
    run("clean.csv")
    third = run("legacy.csv")
    pair = store.compare_runs(third["run_id"], first["run_id"])
    assert pair["comparison"]["from_run_id"] == first["run_id"]
    assert pair["comparison"]["to_run_id"] == third["run_id"]


def test_baseline_has_no_previous(isolated):
    first = run()
    assert first["comparison"]["baseline"] is True
    assert first["replaced_run_id"] is None
    assert first["comparison"]["causes"] == []


def test_new_version_chain_and_comparison_persist_in_archive(isolated):
    from app.archive import build_archive

    first = run("clean.csv")
    second = run("dirty.csv")
    text = build_archive(store.get(second["run_id"]))
    assert "与上一版结论的差异" in text
    assert first["run_id"] in text
    assert second["run_id"] in text
    assert "复核记录" in text


def test_original_comparison_survives_a_later_switch(isolated):
    base = load_sample("clean.csv")
    first = execute(base)
    revised_run = execute(revised(base))
    before = store.get(revised_run["run_id"])["comparison"]
    store.activate(first["run_id"], version=first["version"])
    after = store.get(revised_run["run_id"], with_comparison=False)
    assert after["state"] == store.SUPERSEDED
    again = store.get(revised_run["run_id"])["comparison"]
    assert again["from_run_id"] == before["from_run_id"] == first["run_id"]
    assert again["records"] == before["records"]
    assert again["metrics"] == before["metrics"]


def test_reverting_keeps_old_version_and_superseded_content(isolated):
    first = run("dirty.csv")
    second = run("clean.csv")
    store.review(second["run_id"], 1, "已复核")
    store.activate(first["run_id"], version=first["version"])
    superseded = store.get(second["run_id"])
    assert superseded["state"] == store.SUPERSEDED
    assert superseded["decision"] == "已复核"
    assert len(superseded["stages"]) == 7
    assert len(store.history()) == 2
    # 切回为现行后，复核该版本仍受版本条件保护。
    assert store.review(first["run_id"], first["version"], "已复核")["version"] == first["version"] + 1


def test_rerunning_a_superseded_conclusion_adds_nothing(isolated):
    first = run("dirty.csv")
    second = run("clean.csv")
    again = run("dirty.csv")
    assert again["reused"] is True
    assert again["run_id"] == first["run_id"]
    assert len(store.history()) == 2
    assert store.current()["run_id"] == second["run_id"]


def test_activate_missing_run_leaves_current(isolated):
    first = run("dirty.csv")
    with pytest.raises(Exception) as error:
        store.activate("missing", version=1)
    assert getattr(error.value, "status", None) == 404
    assert store.current()["run_id"] == first["run_id"]


def test_failed_write_keeps_previous_conclusion(isolated, monkeypatch):
    from app.pipeline import calculate

    first = run("dirty.csv")
    second = calculate(load_sample("clean.csv"))
    real_dumps = store.json.dumps
    calls = {"count": 0}

    def failing_dumps(*args, **kwargs):
        calls["count"] += 1
        if calls["count"] == 2:
            raise RuntimeError("写入失败")
        return real_dumps(*args, **kwargs)

    monkeypatch.setattr(store.json, "dumps", failing_dumps)
    with pytest.raises(RuntimeError):
        store.save(second["run_id"], second)
    monkeypatch.setattr(store.json, "dumps", real_dumps)

    assert store.current()["run_id"] == first["run_id"]
    assert store.get(first["run_id"])["state"] == store.CURRENT
    assert len(store.history()) == 1


def test_workbench_shows_current_and_superseded_states(isolated):
    from app.main import create_app

    app = create_app()
    client = app.test_client()
    response = client.post("/api/runs", json={"sample": "dirty.csv"})
    first = response.get_json()["run_id"]
    response = client.post("/api/runs", json={"sample": "clean.csv"})
    second = response.get_json()["run_id"]

    page = client.get("/").get_data(as_text=True)
    assert second in page and "现行" in page
    old = client.get("/?run=" + first).get_data(as_text=True)
    assert first in old and "被替代" in old

    assert client.get("/api/runs/current").get_json()["run_id"] == second
    listed = client.get("/api/runs").get_json()
    assert listed["current"] == second
    assert [entry["is_current"] for entry in listed["runs"]].count(True) == 1

    restored = client.post("/api/runs/" + first + "/activate", json={"version": 1})
    assert restored.status_code == 200
    assert client.get("/api/runs/current").get_json()["run_id"] == first
    assert client.post("/api/runs/" + second + "/activate", json={"version": 99}).status_code == 409
    assert client.get("/api/runs/current").get_json()["run_id"] == first

