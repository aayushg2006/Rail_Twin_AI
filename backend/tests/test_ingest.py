"""Live-data ingestion: budget discipline, honest degradation, assimilation."""
from __future__ import annotations

import json
import time

import pytest

from app.ingest.railradar import (BudgetExhausted, Observation, RailRadarClient,
                                  RequestBudget)


@pytest.mark.asyncio
async def test_budget_refuses_once_the_allowance_is_spent():
    budget = RequestBudget(limit=3, redis_url=None)
    for _ in range(3):
        await budget.spend()
    assert await budget.remaining() == 0
    with pytest.raises(BudgetExhausted):
        await budget.spend()


@pytest.mark.asyncio
async def test_budget_is_reserved_before_the_call_not_after():
    """A crash mid-request must not let the allowance be overspent."""
    budget = RequestBudget(limit=1, redis_url=None)
    await budget.spend()
    assert await budget.used() == 1


def test_live_mode_without_a_key_falls_back_instead_of_failing():
    # An empty key means "none configured"; None means "read the settings".
    client = RailRadarClient(mode="live", api_key="")
    assert client.mode == "off"
    assert "API_KEY" in client.last_error


@pytest.mark.asyncio
async def test_off_mode_never_returns_an_observation():
    client = RailRadarClient(mode="off", api_key="")
    assert await client.live_status("12345", time.time()) is None


@pytest.mark.asyncio
async def test_replay_serves_recorded_readings_and_caches_them():
    client = RailRadarClient(mode="replay", api_key="")
    if not client._replay:
        pytest.skip("replay feed not present in this environment")
    number = next(iter(client._replay))
    now = time.time()
    first = await client.live_status(number, now)
    assert first is not None and first.source == "replay"
    # Second read inside the TTL must not re-read the feed.
    again = await client.live_status(number, now + 1)
    assert again is first


@pytest.mark.asyncio
async def test_replay_spends_no_budget():
    """The counter is shared (Redis), so measure the delta, not the absolute."""
    client = RailRadarClient(mode="replay", api_key="")
    if not client._replay:
        pytest.skip("replay feed not present")
    before = await client.budget.used()
    for number in list(client._replay)[:3]:
        await client.live_status(number, time.time())
    assert await client.budget.used() == before


def test_parses_the_real_railradar_shape():
    """Captured from the live API, trimmed to the fields we consume."""
    payload = {
        "success": True,
        "data": {
            "trainNumber": "12284",
            "delayMinutes": 14,
            "status": "running",
            "isLive": True,
            "lastUpdatedAt": "2026-08-21T01:36:36+05:30",
            "currentLocation": {
                "stationCode": "NZM", "status": "departed",
                "delayMinutes": 14, "sequence": 1,
            },
            "route": [{"sequence": 1, "stationCode": "NZM"}],
        },
    }
    obs = RailRadarClient._parse("12284", payload)
    assert obs is not None
    assert obs.lateness_sec == 14 * 60
    assert obs.last_station == "NZM"          # nested under currentLocation
    assert obs.observed_at == "2026-08-21T01:36:36+05:30"   # the feed's own stamp
    assert obs.running is True


def test_a_train_that_has_not_started_is_not_treated_as_on_time():
    """delayMinutes is 0 before departure; assimilating that would overwrite
    whatever the twin had legitimately inferred."""
    obs = RailRadarClient._parse("12284", {
        "data": {"delayMinutes": 0, "status": "not-started",
                 "currentLocation": {"stationCode": "NZM"}},
    })
    assert obs is not None and obs.lateness_sec == 0
    assert obs.running is False
    assert obs.as_dict()["usable"] is False


def test_response_parsing_is_defensive_about_field_names():
    parse = RailRadarClient._parse
    assert parse("1", {"data": {"delayMinutes": 7, "lastStation": "VR"}}).lateness_sec == 420
    assert parse("1", {"delay": 3, "currentStation": {"code": "NSP"}}).last_station == "NSP"
    # An upstream rename must degrade to "no observation", never a wrong one.
    assert parse("1", {"data": {"somethingElse": 9}}) is None
    assert parse("1", {}) is None
    assert parse("1", {"data": "unexpected"}) is None


@pytest.mark.asyncio
async def test_status_reports_coverage_honestly():
    client = RailRadarClient(mode="replay", api_key="")
    status = await client.status()
    assert "synthetic" in status["coverage"]
    assert status["budgetLimit"] > 0


def test_watchlist_picks_passenger_services_near_their_booked_time():
    from app.ingest.service import IngestionService
    from app.orchestrator.orchestrator import SimulationOrchestrator

    orch = SimulationOrchestrator("BASE")
    service = IngestionService(orch, RailRadarClient(mode="replay", api_key=""))
    watch = service.watchlist()
    assert watch, "watchlist should not be empty at the demo clock"
    assert len(watch) <= 8
    assert len(set(watch)) == len(watch), "no duplicate train numbers"

    from app.network.fleet import fleet
    by_number = {f.number: f for f in fleet}
    for number in watch:
        assert not by_number[number].is_freight, "goods have no live feed"


def test_assimilation_reaches_the_shadow_twins_too():
    """A baseline that did not see the same observations is not a counterfactual."""
    from app.ingest.service import IngestionService
    from app.orchestrator.orchestrator import SimulationOrchestrator

    orch = SimulationOrchestrator("BASE")
    service = IngestionService(orch, RailRadarClient(mode="replay", api_key=""))
    number = service.watchlist()[0]
    service._assimilate([Observation(number, 540.0, "VR", "now", "replay")])
    for engine in (orch.engine, orch.shadow_nothing, orch.shadow_priority):
        assert engine.observed_delay_sec.get(number) == 540.0


def test_replay_feed_declares_that_it_is_not_real():
    client = RailRadarClient(mode="replay", api_key="")
    if not client._replay:
        pytest.skip("replay feed not present")
    from pathlib import Path
    for base in (Path("/srv/data"), Path(__file__).resolve().parents[2] / "data"):
        path = base / "railradar-replay.json"
        if path.exists():
            pack = json.loads(path.read_text(encoding="utf-8"))
            assert pack["provenance"] in ("synthetic", "observed")
            if pack["provenance"] == "synthetic":
                assert "NOT real observations" in pack["provenanceNote"]
            return
