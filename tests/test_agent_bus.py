"""Tests for the agent bus: the persisted inter-agent feed, its retention
policy, its cursor endpoint and the agent roster manifest.

The retention tests go at the database directly rather than through the
API, because the claim under test is about which TABLES a delete reaches
— that is invisible from the feed endpoint, which only ever reads its
own table.
"""

import pytest
from fastapi.testclient import TestClient

from app import bus, db
from main import app


@pytest.fixture
def client():
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def conn():
    """A connection to the same throwaway database the client uses."""
    connection = db.connect_ready()
    yield connection
    connection.close()


def _publish(connection, message, *, hours_ago=0):
    """Put one event on the bus, optionally backdated for the clock bound."""
    connection.execute(
        "INSERT INTO agent_feed (agent, kind, message, created_at) "
        "VALUES ('reflex', 'scan', ?, datetime('now', ?))",
        (message, f"-{hours_ago} hours"),
    )


def _seal_a_decision(connection):
    """One decision with its lifecycle row — the record prune must not reach."""
    with db.writing(connection):
        event_id = connection.execute(
            "INSERT INTO events (kind, service, occurred_on, payload_json) "
            "VALUES ('cost_anomaly', 'compute', '2026-07-01', '{}') RETURNING id"
        ).fetchone()["id"]
        action_id = connection.execute(
            "INSERT INTO actions (event_id, title, detail_json) "
            "VALUES (?, 'rightsize compute', '{}') RETURNING id",
            (event_id,),
        ).fetchone()["id"]
        connection.execute(
            "INSERT INTO decisions (action_id, service, verdict, rationale, "
            "input_context_json) VALUES (?, 'compute', 'approved', 'kept', '{}')",
            (action_id,),
        )
        connection.execute(
            "INSERT INTO action_events (action_id, transition, actor) "
            "VALUES (?, 'filed', 'test')",
            (action_id,),
        )


def _count(connection, table):
    return connection.execute(f"SELECT count(*) AS n FROM {table}").fetchone()["n"]


def test_pulse_publishes_the_whole_conversation(client):
    client.post("/pulse").json()
    feed = client.get("/agents/feed").json()
    assert feed["count"] > 0
    agents = {event["agent"] for event in feed["events"]}
    # the full cast appears: opener, triage, drafting, review, narration
    assert {"reflex", "analyst", "recommender", "skeptic", "chronicler"} <= agents
    # ids ascend — the feed replays in order
    ids = [event["id"] for event in feed["events"]]
    assert ids == sorted(ids)
    # the dialogue is visible: a skeptic request precedes its verdict
    kinds = [event["kind"] for event in feed["events"]]
    assert kinds.index("escalate") < kinds.index("verdict")


def test_feed_cursor_returns_only_new_events(client):
    client.post("/pulse")
    first = client.get("/agents/feed").json()
    assert client.get("/agents/feed", params={"after": first["last_id"]}).json()["count"] == 0
    # an operator decision lands on the bus as a new event
    action_id = client.get("/actions").json()["actions"][0]["id"]
    client.post(f"/actions/{action_id}/reject", json={"actor": "op", "rationale": "test"})
    fresh = client.get("/agents/feed", params={"after": first["last_id"]}).json()
    assert fresh["count"] == 1
    assert fresh["events"][0]["agent"] == "operator"
    assert "REJECTED" in fresh["events"][0]["message"]


def test_feed_limit_paginates_in_order(client):
    client.post("/pulse")  # emits the whole cast — well over a page of 3
    total = client.get("/agents/feed").json()
    assert total["count"] >= 4

    page = client.get("/agents/feed", params={"limit": 3}).json()
    assert page["count"] == 3
    assert page["last_id"] == page["events"][-1]["id"]
    assert [e["id"] for e in page["events"]] == sorted(e["id"] for e in page["events"])

    rest = client.get("/agents/feed", params={"after": page["last_id"]}).json()
    assert all(e["id"] > page["last_id"] for e in rest["events"])  # no overlap
    assert page["count"] + rest["count"] == total["count"]  # the page + remainder cover it


def test_feed_says_how_much_is_still_waiting(client):
    client.post("/pulse")
    everything = client.get("/agents/feed").json()
    assert everything["count"] > 2
    # nothing behind the cursor once a caller has read it all
    assert everything["total"] == everything["count"]
    assert everything["has_more"] is False

    page = client.get("/agents/feed", params={"limit": 2}).json()
    assert page["count"] == 2
    # the page is short, and says so rather than looking like the whole feed
    assert page["total"] == everything["count"]
    assert page["has_more"] is True

    tail = client.get("/agents/feed", params={"after": page["last_id"]}).json()
    assert tail["total"] == everything["count"] - 2
    assert tail["has_more"] is False


def test_pages_neither_repeat_nor_skip_when_agents_publish_mid_read(client, conn):
    client.post("/pulse")
    first = client.get("/agents/feed", params={"limit": 3}).json()
    assert first["has_more"] is True

    # an agent publishes while the client sits between two pages
    bus.emit(conn, "analyst", "triage", "landed after page one")

    served = list(first["events"])
    cursor = first["last_id"]
    while True:
        page = client.get("/agents/feed", params={"after": cursor, "limit": 3}).json()
        served.extend(page["events"])
        cursor = page["last_id"]
        if not page["has_more"]:
            break

    ids = [event["id"] for event in served]
    assert len(ids) == len(set(ids))  # no duplicates
    stored = [row["id"] for row in conn.execute("SELECT id FROM agent_feed ORDER BY id")]
    assert ids == stored  # no gaps: every stored event, once, in order
    late = [e for e in served if e["message"] == "landed after page one"]
    assert len(late) == 1  # the mid-read arrival is served exactly once


def test_page_size_is_bounded(client):
    # a page can never be asked to drain the whole retained feed
    assert bus.MAX_PAGE_ROWS < bus.FEED_KEEP_ROWS
    assert client.get("/agents/feed", params={"limit": bus.MAX_PAGE_ROWS}).status_code == 200
    assert (
        client.get("/agents/feed", params={"limit": bus.MAX_PAGE_ROWS + 1}).status_code
        == 422
    )
    assert client.get("/agents/feed", params={"limit": 0}).status_code == 422


def test_prune_ages_out_chatter_and_leaves_the_audit_record_alone(conn):
    _seal_a_decision(conn)
    for index in range(4):
        _publish(conn, f"stale {index}", hours_ago=bus.FEED_KEEP_HOURS + 1)
    _publish(conn, "fresh")

    assert bus.prune(conn) == 4
    assert [row["message"] for row in conn.execute("SELECT message FROM agent_feed")] == [
        "fresh"
    ]
    # retention reaches the bus and stops there: the sealed record is the
    # ledger's, and a missing row there reads as tampering, not cleanup.
    assert _count(conn, "decisions") == 1
    assert _count(conn, "action_events") == 1
    assert _count(conn, "actions") == 1
    assert _count(conn, "events") == 1


def test_prune_keeps_the_newest_rows_even_when_ids_are_sparse(conn):
    # ids the emit path would not produce on its own, written directly: this
    # pins the policy to rows rather than to the dense-id habit it would
    # otherwise be free to lean on
    for feed_id in (1, 2, 3, 900, 901, 902):
        conn.execute(
            "INSERT INTO agent_feed (id, agent, kind, message) "
            "VALUES (?, 'reflex', 'scan', ?)",
            (feed_id, f"event {feed_id}"),
        )

    # asked for five, five must survive. Measuring a span of id values
    # instead of counting rows spends the whole budget on the hole between
    # 3 and 900 and keeps only three.
    assert bus.prune(conn, keep_rows=5) == 1
    survivors = [row["id"] for row in conn.execute("SELECT id FROM agent_feed ORDER BY id")]
    assert survivors == [2, 3, 900, 901, 902]


def test_prune_applies_the_clock_bound_under_the_row_cap(conn):
    _publish(conn, "old but within the row cap", hours_ago=bus.FEED_KEEP_HOURS + 1)
    _publish(conn, "recent")

    # room to spare on rows, so only the clock can evict anything
    assert bus.prune(conn, keep_rows=bus.FEED_KEEP_ROWS) == 1
    assert [row["message"] for row in conn.execute("SELECT message FROM agent_feed")] == [
        "recent"
    ]


def test_prune_never_empties_the_feed_so_ids_keep_ascending(conn):
    for index in range(3):
        _publish(conn, f"ancient {index}", hours_ago=bus.FEED_KEEP_HOURS + 1)
    already_served = conn.execute("SELECT max(id) AS top FROM agent_feed").fetchone()["top"]

    # every row is past the clock bound, yet the newest one is spared
    assert bus.prune(conn) == 2
    assert _count(conn, "agent_feed") == 1

    # why it is spared: an INTEGER PRIMARY KEY is the rowid and SQLite hands
    # out max(rowid) + 1, so an emptied table restarts ids at 1 and a client
    # polling from `already_served` would never see the bus speak again.
    bus.emit(conn, "reflex", "scan", "the first thing said after the sweep")
    revived = conn.execute("SELECT max(id) AS top FROM agent_feed").fetchone()["top"]
    assert revived > already_served


def test_prune_is_a_no_op_on_an_empty_feed(conn):
    assert bus.prune(conn) == 0


def test_roster_names_the_six_agents_with_guardrails(client):
    body = client.get("/agents").json()
    assert body["count"] == 6
    assert body["debate_threshold"] == 0.6
    names = [agent["name"] for agent in body["agents"]]
    assert names == ["reflex", "analyst", "recommender", "skeptic", "chronicler", "operator"]
    assert all(agent["guardrails"] for agent in body["agents"])


def test_roster_threshold_falls_back_when_the_mission_is_unloadable(client, monkeypatch):
    from app import bus
    from app.missions import MissionError

    def _unloadable(*args, **kwargs):
        raise MissionError("mission config unavailable")

    monkeypatch.setattr(bus, "get_mission", _unloadable)
    body = client.get("/agents").json()
    # the roster is code-defined, so it stands even when the mission YAML is
    # gone; only the debate threshold (read from the mission) goes null.
    assert body["count"] == 6
    assert body["debate_threshold"] is None
    names = [agent["name"] for agent in body["agents"]]
    assert names == ["reflex", "analyst", "recommender", "skeptic", "chronicler", "operator"]
