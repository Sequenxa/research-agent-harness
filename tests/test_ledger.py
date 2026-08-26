from __future__ import annotations

from pathlib import Path

from research_harness.ledger import LedgerEventType, LedgerStore


def test_ledger_append_and_list(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.db")
    event = store.append(
        project_id="example",
        contract_version=1,
        event_type=LedgerEventType.STATE_TRANSITION,
        payload={"lifecycle": "RUNNING"},
    )

    events = store.list_events(project_id="example")
    assert len(events) == 1
    assert events[0].event_id == event.event_id
    assert events[0].contract_version == 1
    assert events[0].payload["lifecycle"] == "RUNNING"


def test_ledger_latest_event(tmp_path: Path) -> None:
    store = LedgerStore(tmp_path / "ledger.db")
    store.append(
        project_id="example",
        contract_version=1,
        event_type=LedgerEventType.EXPERIMENT_START,
        payload={"note": "first"},
    )
    store.append(
        project_id="example",
        contract_version=1,
        event_type=LedgerEventType.CHECKPOINT,
        payload={"note": "second"},
    )

    latest = store.latest_event(project_id="example")
    assert latest is not None
    assert latest.event_type == LedgerEventType.CHECKPOINT
    assert latest.payload["note"] == "second"
