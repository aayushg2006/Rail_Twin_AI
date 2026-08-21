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
async def test_off_mode_never_touches_the_network():
    client = RailRadarClient(mode="off", api_key="")
    assert await client.station_board() == []
    assert await client.train_detail("12345") is None


@pytest.mark.asyncio
async def test_replay_serves_the_whole_recorded_board():
    client = RailRadarClient(mode="replay", api_key="")
    if not client._replay:
        pytest.skip("replay feed not present in this environment")
    rows = await client.station_board()
    assert len(rows) == len(client._replay)
    assert all(o.source == "replay" for o in rows)


@pytest.mark.asyncio
async def test_replay_spends_no_budget():
    """The counter is shared (Redis), so measure the delta, not the absolute."""
    client = RailRadarClient(mode="replay", api_key="")
    if not client._replay:
        pytest.skip("replay feed not present")
    before = await client.budget.used()
    await client.station_board()
    await client.train_detail(next(iter(client._replay)))
    assert await client.budget.used() == before


def test_parses_the_real_station_board():
    """One board row, exactly as the live API returns it."""
    row = {
        "train": {"number": "91053", "name": "Virar Mumbai EMU", "type": "EMU",
                  "source": "CCG", "destination": "VR",
                  "runDays": ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]},
        "stop": {"sequence": 29, "arrival": "02:19", "departure": "02:19",
                 "day": 1, "distance": 51.6, "isHalt": True, "platform": None},
        "live": {"type": "at-station", "startDate": "2026-08-21",
                 "expectedArrivalTime": "2026-08-21T02:25:00+05:30",
                 "delayMinutes": 6},
    }
    obs = RailRadarClient._from_board_row(row, "fallback")
    assert obs is not None
    assert obs.number == "91053"
    assert obs.lateness_sec == 6 * 60
    assert obs.distance_km == 51.6
    assert obs.scheduled_departure == "02:19"
    assert obs.status == "at-station"
    assert obs.running is True


def test_the_board_ignores_a_row_it_cannot_read():
    assert RailRadarClient._from_board_row({}, "t") is None
    assert RailRadarClient._from_board_row({"train": {}}, "t") is None
    assert RailRadarClient._from_board_row("nonsense", "t") is None


def test_detail_pulls_the_platform_and_section_speed_from_the_route():
    """The per-train endpoint is the only place these appear."""
    payload = {"success": True, "data": {
        "trainNumber": "93002", "status": "running", "isLive": True,
        "lastUpdatedAt": "2026-08-21T05:40:00+05:30",
        "currentLocation": {"stationCode": "BYR", "status": "departed",
                            "delayMinutes": 3},
        "train": {"name": "Dahanu Road - Churchgate Fast", "type": "EMU"},
        "route": [
            {"sequence": 27, "stationCode": "BYR", "platform": "1", "distance": 43.3},
            {"sequence": 29, "stationCode": "BSR", "platform": "5", "distance": 51.6,
             "scheduledDeparture": "2026-08-21T05:57:00+05:30",
             "speedToNextStationKmph": 63},
        ]}}
    obs = RailRadarClient._from_detail("93002", payload, "BSR")
    assert obs is not None
    assert obs.platform == "5"            # from the BSR row, not BYR
    assert obs.speed_to_next_kmph == 63
    assert obs.distance_km == 51.6
    assert obs.lateness_sec == 180


def test_legacy_parse_shape_retained():
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
    obs = RailRadarClient._from_detail("12284", payload, "NZM")
    assert obs is not None
    assert obs.lateness_sec == 14 * 60
    assert obs.last_station == "NZM"          # nested under currentLocation
    assert obs.observed_at == "2026-08-21T01:36:36+05:30"   # the feed's own stamp
    assert obs.running is True


def test_a_train_that_has_not_started_is_not_treated_as_on_time():
    """delayMinutes is 0 before departure; assimilating that would overwrite
    whatever the twin had legitimately inferred."""
    obs = RailRadarClient._from_detail("12284", {
        "data": {"delayMinutes": 0, "status": "not-started",
                 "currentLocation": {"stationCode": "NZM"}},
    }, "NZM")
    assert obs is not None and obs.lateness_sec == 0
    assert obs.running is False
    assert obs.as_dict()["usable"] is False


def test_response_parsing_is_defensive_about_field_names():
    parse = lambda n, p: RailRadarClient._from_detail(n, p, "BSR")
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
    watch = service.in_section()
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
    number = (service.in_section() or ["91053"])[0]
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


@pytest.mark.asyncio
async def test_a_not_started_train_still_yields_its_platform_and_section_speed():
    """The delay of a train that has not left its origin is meaningless, but the
    platform it is booked into and the speed of the section ahead are not. The
    first cut of the gate threw the whole record away and the console never saw
    a live platform or a live speed."""
    from app.ingest.service import IngestionService
    from app.orchestrator.orchestrator import SimulationOrchestrator

    orch = SimulationOrchestrator("BASE")
    service = IngestionService(orch, RailRadarClient(mode="replay", api_key=""))
    number = (service.in_section() or ["91053"])[0]

    obs = Observation(number, 0.0, "CCG", "now", "detail",
                      status="not-started", platform="5",
                      speed_to_next_kmph=63.0)
    assert obs.running is False
    assert service._record(obs) is True, "the reading is still worth assimilating"

    before = orch.engine.observed_delay_sec.get(number)
    service._assimilate([obs])
    assert orch.engine.observed_delay_sec.get(number) == before, \
        "a not-started train must not be assimilated as on time"
    for engine in (orch.engine, orch.shadow_nothing, orch.shadow_priority):
        assert engine.observed_platform.get(number) == "PF5"
        assert engine.observed_speed_kmh.get(number) == pytest.approx(63.0)


@pytest.mark.asyncio
async def test_a_bare_not_started_reading_is_still_refused():
    """Without a platform or a speed there is nothing in it worth having."""
    from app.ingest.service import IngestionService
    from app.orchestrator.orchestrator import SimulationOrchestrator

    orch = SimulationOrchestrator("BASE")
    service = IngestionService(orch, RailRadarClient(mode="replay", api_key=""))
    obs = Observation("99999", 0.0, "CCG", "now", "board", status="not-started")
    assert service._record(obs) is False


def test_detail_calls_are_spent_on_trains_that_are_actually_moving():
    """The twin's clock need not agree with the wall clock. Ordering by what the
    board says is running stops the allowance going on services that have not
    left their origin."""
    from app.ingest.service import IngestionService
    from app.orchestrator.orchestrator import SimulationOrchestrator

    orch = SimulationOrchestrator("BASE")
    service = IngestionService(orch, RailRadarClient(mode="replay", api_key=""))
    numbers = service.in_section()
    if len(numbers) < 2:
        pytest.skip("needs at least two passenger services in section")

    later = numbers[-1]
    service.observations[later] = Observation(
        later, 120.0, "VR", "now", "board", status="running")
    assert service.in_section()[0] == later, "the moving train is polled first"


def test_feed_state_distinguishes_live_degraded_stale_and_fallback():
    from app.ingest.service import IngestionService
    from app.orchestrator.orchestrator import SimulationOrchestrator

    client = RailRadarClient(mode="live", api_key="test-key")
    service = IngestionService(SimulationOrchestrator("BASE"), client)
    assert service.feed_state() == "STALE"
    service.last_live_at = time.time()
    assert service.feed_state() == "LIVE"
    service.degraded = "quota reserve"
    assert service.feed_state() == "DEGRADED"
    service.client.mode = "replay"
    assert service.feed_state() == "REPLAY_FALLBACK"


@pytest.mark.asyncio
async def test_live_locked_orchestrator_refuses_demo_pause_and_speed(monkeypatch):
    from app.config import settings
    from app.orchestrator.orchestrator import SimulationOrchestrator

    monkeypatch.setattr(settings, "railradar_mode", "live")
    monkeypatch.setattr(settings, "railradar_api_key", "test-key")
    orchestrator = SimulationOrchestrator("BASE")
    assert orchestrator.clock_mode == "LIVE"
    assert orchestrator.speed == 1
    await orchestrator.handle_command({"cmd": "pause"})
    await orchestrator.handle_command({"cmd": "set_speed", "speed": 10})
    await orchestrator.handle_command({"cmd": "set_clock_mode", "mode": "DEMO"})
    assert orchestrator.playing is True
    assert orchestrator.speed == 1
    assert orchestrator.clock_mode == "LIVE"


def test_unmatched_board_train_is_not_invented_without_a_defensible_route():
    from app.ingest.service import IngestionService
    from app.network.fleet import fleet_by_id
    from app.orchestrator.orchestrator import SimulationOrchestrator

    service = IngestionService(
        SimulationOrchestrator("BASE"), RailRadarClient(mode="replay", api_key=""))
    before = set(fleet_by_id)
    observation = Observation(
        "99998", 120.0, "UNKNOWN", "2026-08-21T12:00:00+05:30", "board",
        status="running", origin="UNKNOWN", destination="NOWHERE",
        scheduled_departure="12:10")
    service._ensure_live_services([observation])
    assert set(fleet_by_id) == before


def test_detail_location_anchor_updates_all_paired_twins():
    from app.ingest.service import IngestionService
    from app.orchestrator.orchestrator import SimulationOrchestrator

    orchestrator = SimulationOrchestrator("BASE")
    service = IngestionService(
        orchestrator, RailRadarClient(mode="replay", api_key=""))
    number = service.in_section()[0]
    observation = Observation(
        number, 60.0, "BSR", "2026-08-21T12:00:00+05:30", "detail",
        status="running")
    service._assimilate([observation])
    for engine in (orchestrator.engine, orchestrator.shadow_nothing,
                   orchestrator.shadow_priority):
        assert engine.observed_source[number] == "detail"
