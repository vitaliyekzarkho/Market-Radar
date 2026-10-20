"""C3.3.6 synthetic contract replay. No transport, collectors or live entry points."""
from copy import deepcopy
from datetime import timedelta
import hashlib
import json
from pathlib import Path
import tempfile

from driver_contract import build_driver_contract
from fast_news_story_engine import cluster_events
from fast_news_interpretation_engine import interpret_story
from reaction_window_engine import build_reaction_report
from story_intermarket_validation_engine import evaluate_story
from news_pa_context_adapter import explain_scenario
from news_lifecycle_engine import Lifecycle
from news_lifecycle_store import Store
from news_delivery_state import Delivery
from news_notification_policy import Policy, canonical, digest, utc

BASE_SHA = "8af2c7439fd63a2bad2fcf4945ae0f26b5db5d38"
CONTRACT = "c336_v1"
REQUIRED_CASES = {
    "first_significant", "communicated", "duplicate", "redistribution",
    "late_publication", "independent_development", "fact_correction",
    "hypothesis_contradiction", "pa_conflict", "mixed_unsupported_fact",
    "recipient_mismatch", "future_rejected", "unverified_rejected", "resolution",
    "linked_episode", "unknown_delivery", "unknown_no_retry", "expiry", "future_development",
}


def require(condition, code):
    if not condition:
        raise ValueError(code)


def load_fixture(manifest_path):
    """Return an isolated JSON value; validate raw bytes before parsing."""
    path = Path(manifest_path).resolve()
    manifest_bytes = path.read_bytes()
    manifest = json.loads(manifest_bytes)
    require(manifest["contract_version"] == CONTRACT, "CONTRACT_VERSION")
    require(manifest["classification"] == "synthetic", "SYNTHETIC_ONLY")
    require(manifest["provenance"] == "declared_synthetic_test_attestations", "PROVENANCE")
    target = (path.parent / manifest["file"]).resolve()
    require(target.is_relative_to(path.parent) and target != path, "FIXTURE_PATH")
    raw = target.read_bytes()
    require(hashlib.sha256(raw).hexdigest() == manifest["sha256"], "CONTENT_HASH")
    fixture = json.loads(raw)
    require(fixture["classification"] == "synthetic", "SYNTHETIC_ONLY")
    return fixture, {"manifest_sha256": hashlib.sha256(manifest_bytes).hexdigest(),
                     "content_sha256": manifest["sha256"], "file": manifest["file"],
                     "provenance": manifest["provenance"]}


def chronology(value, cutoff):
    """Strict gate before accepted modules with more permissive parsers."""
    p, o, a = (utc(value[k]) for k in
               ("published_at_utc", "observed_at_utc", "available_at_utc"))
    require(p <= o <= a <= utc(cutoff), "INPUT_CHRONOLOGY")
    for evidence in value.get("evidence", []):
        require(utc(evidence["observed_at_utc"]) <= utc(evidence["available_at_utc"]) <= a,
                "EVIDENCE_CHRONOLOGY")


def chain_input(spec, cutoff):
    """Explicit synthetic adapter through grouping, interpretation, reaction and PA.

    Lifecycle fact attestation is fixture-owned, never inferred from outlet count.
    Validation attestations describe directional consistency, never causal proof.
    """
    require(spec["provenance"] == "declared_synthetic_test_attestations", "PROVENANCE")
    events = deepcopy(spec["events"])
    for event in events:
        chronology(event, cutoff)
    stories = cluster_events(events)
    require(len(stories) == 1, "AMBIGUOUS_CHAIN_STORY")
    story = stories[0]
    interpretation = interpret_story(story)
    require(bool(interpretation["mechanism_candidates"]), "NO_MECHANISM")
    event_time = spec["event_time_utc"]
    require(utc(event_time) <= utc(spec["interpretation_available_at_utc"]) <=
            utc(spec["validation_available_at_utc"]) <= utc(cutoff), "CHAIN_CHRONOLOGY")
    require(max(utc(e["available_at_utc"]) for e in events) <=
            utc(spec["interpretation_available_at_utc"]), "INTERPRETATION_BEFORE_INPUT")
    assets = deepcopy(spec["assets"])
    for asset in assets.values():
        require(asset["source"] == "synthetic", "SYNTHETIC_ASSET_REQUIRED")
        require(asset["timeframe"] == "M15", "FIXTURE_TIMEFRAME")
        for bar in asset["candles"]:
            closed = utc(bar["utc_time"]) + timedelta(minutes=15)
            require(bar["is_closed"] is True, "UNCLOSED_BAR")
            require(closed <= utc(bar["available_at_utc"]) <=
                    utc(spec["validation_available_at_utc"]), "BAR_AVAILABILITY")
    anchor = {"event_id": story["story_id"], "event_time_utc": event_time,
              "event_family": "yield_shock_up", "status": "reported_fact"}
    driver = build_driver_contract(anchor, now=utc(cutoff))
    reaction = build_reaction_report(anchor, assets, utc(spec["validation_available_at_utc"]))
    validation = evaluate_story(interpretation, reaction, window="post_60m")
    explained = explain_scenario(
        deepcopy(spec["scenario"]),
        {"available_at_utc": spec["interpretation_available_at_utc"], "payload": interpretation},
        {"available_at_utc": spec["validation_available_at_utc"], "payload": validation},
        as_of_utc=cutoff, max_age_seconds=spec["max_age_seconds"])
    require(explained["scenario"] == spec["scenario"], "SCENARIO_MUTATED")
    states = [m["status"] for m in validation["mechanisms"]]
    state = states[0] if states and len(set(states)) == 1 else "insufficient_data"
    value = deepcopy(spec["lifecycle"])
    require(utc(value["available_at_utc"]) >= utc(spec["validation_available_at_utc"]),
            "LIFECYCLE_BEFORE_VALIDATION")
    value.update(upstream_story_id=story["story_id"],
                 member_event_ids=sorted(e["event_id"] for e in events),
                 assessment=explained["news_context"]["relation"],
                 hypothesis_status=state if state in {"supported", "contradicted"} else "insufficient_data",
                 mechanism_refs=sorted(m["mechanism_id"] for m in validation["mechanisms"]))
    # The fixture explicitly declares the validation evidence reference; its result
    # is supplied by the real validator, rather than copied from an expected verdict.
    for evidence in value["evidence"]:
        if evidence["kind"] == "validation":
            evidence["verified"] = state in {"supported", "contradicted"}
            evidence["relation"] = "contradicts" if state == "contradicted" else "supports"
            evidence["observed_at_utc"] = reaction["as_of_utc"]
            evidence["available_at_utc"] = spec["validation_available_at_utc"]
    return value, {"driver": driver, "story": story, "interpretation": interpretation,
                   "reaction": reaction, "validation": validation, "explanation": explained}


TABLES = ("clocks", "policies", "stories", "aliases", "members", "episodes",
          "inputs", "evidence", "journal", "migrations", "outbox", "attempts")


def state_snapshot(store):
    """Compare every persisted field, including IDs and journal order."""
    result = {}
    for table in TABLES:
        rows = [dict(r) for r in store.db.execute("SELECT * FROM " + table + " WHERE ns=?", (store.ns,))]
        result[table] = sorted(rows, key=canonical)
    return result


def external_directory(path):
    target = Path(path).resolve()
    require(target.is_dir(), "EXTERNAL_DIRECTORY_REQUIRED")
    require(not target.is_relative_to(Path(__file__).resolve().parent) and
            not any((p / ".git").exists() for p in [target, *target.parents]), "OUTSIDE_REPOSITORY_REQUIRED")
    return target


def replay(fixture, work_dir, *, restart_after=(), until=None):
    """Replay ordered operations into a new temporary DB, removed on completion."""
    original = canonical(fixture)
    require(fixture["classification"] == "synthetic", "SYNTHETIC_ONLY")
    policy = Policy(**fixture["policy"])
    operations = fixture["operations"]
    ids = [op["id"] for op in operations]
    require(len(ids) == len(set(ids)), "DUPLICATE_OPERATION_ID")
    ordered = sorted(operations, key=lambda op: (utc(op["at"]), op["order"], op["id"]))
    require(all(type(op["order"]) is int for op in ordered), "EXPLICIT_ORDER")
    if until is not None:
        ordered = [op for op in ordered if utc(op["at"]) <= utc(until)]
    records, intent_by_operation = [], {}
    with tempfile.TemporaryDirectory(dir=external_directory(work_dir)) as temp:
        db_path = Path(temp) / "replay.sqlite"
        store = Store(db_path, namespace="c336", mode="replay")
        try:
            engine, delivery = Lifecycle(store, policy), Delivery(store)
            for op in ordered:
                before = state_snapshot(store)
                before_intents = len(store.outbox())
                trace, result, error = None, None, None
                try:
                    if op["kind"] in {"news", "chain"}:
                        if op["kind"] == "chain":
                            value, trace = chain_input(op["chain"], op["at"])
                            scenario = op["chain"]["scenario"]
                        else:
                            value, scenario = deepcopy(op["input"]), fixture["scenario"]
                        chronology(value, op["at"])
                        result = engine.process(value, op["recipients"], now=op["at"], scenario=scenario)
                        require(result["scenario"] == scenario, "SCENARIO_MUTATED")
                        if result["duplicate"]:
                            require(len(store.outbox()) == before_intents, "DUPLICATE_INTENT")
                        intent_by_operation[op["id"]] = list(result["notification_ids"])
                        expected = op["expect"]
                        for key in ("state", "semantic_version", "duplicate"):
                            if key in expected:
                                require(result[key] == expected[key], "EXPECT_" + key.upper())
                        if "notification_kind" in expected:
                            kinds = [r["payload"]["notification_kind"] for r in store.outbox()
                                     if r["id"] in result["notification_ids"]]
                            require(kinds == expected["notification_kind"], "EXPECT_NOTIFICATION_KIND")
                        if "assessment" in expected:
                            require(store.episode(result["canonical_story_id"])["semantic"]["assessment"]
                                    == expected["assessment"], "EXPECT_ASSESSMENT")
                    elif op["kind"] == "delivery":
                        key = intent_by_operation[op["target"]][op.get("index", 0)]
                        attempt = delivery.begin(key, now=op["at"])
                        require(attempt is not None, "ATTEMPT_NOT_CREATED")
                        delivery.acknowledge(key, attempt, outcome=op["outcome"], now=op["at"],
                                             receipt_ref="synthetic_receipt" if op["outcome"] == "delivered" else None)
                    elif op["kind"] == "unknown_probe":
                        key = intent_by_operation[op["target"]][0]
                        attempts = store.db.execute("SELECT attempts FROM outbox WHERE id=?", (key,)).fetchone()[0]
                        require(delivery.begin(key, now=op["at"]) is None, "UNKNOWN_RETRIED")
                        row = store.db.execute("SELECT status,attempts FROM outbox WHERE id=?", (key,)).fetchone()
                        require(row["status"] == "unknown" and row["attempts"] == attempts, "UNKNOWN_MUTATED")
                    elif op["kind"] == "expire":
                        engine.expire(now=op["at"])
                        require(any(json.loads(r["payload"])["state"] == "EXPIRED"
                                    for r in state_snapshot(store)["episodes"]), "EXPIRY_NOT_OBSERVED")
                    else:
                        raise ValueError("UNKNOWN_OPERATION")
                except ValueError as exc:
                    error = str(exc)
                    if not op.get("expected_error") or op["expected_error"] not in error:
                        raise
                    require(state_snapshot(store) == before, "REJECTION_CHANGED_STATE")
                require(not op.get("expected_error") or error is not None, "EXPECTED_ERROR_NOT_RAISED")
                snapshot = state_snapshot(store)
                records.append({"id": op["id"], "cutoff": op["at"], "verdict": "PASS",
                                "reason": "EXPECTED_REJECTION" if error else "CONTRACT_ASSERTIONS_PASSED",
                                "error": error, "result": result, "chain": trace, "state": snapshot})
                if op["id"] in restart_after:
                    store.close()
                    store = Store(db_path, namespace="c336", mode="replay")
                    engine, delivery = Lifecycle(store, policy), Delivery(store)
                    require(state_snapshot(store) == snapshot, "RESTART_STATE_CHANGED")
            final = state_snapshot(store)
        finally:
            store.close()
    require(canonical(fixture) == original, "FIXTURE_MUTATED")
    return {"timeline": records, "final_state": final}


def validate(manifest_path, work_dir):
    fixture, manifest = load_fixture(manifest_path)
    report = {"base_sha": BASE_SHA, "contract_version": CONTRACT, "mode": "synthetic",
              "manifest": manifest, "fixture_semantic_hash": digest(fixture),
              "limitations": ["Synthetic attestations only; no historical prediction claim.",
                              "Boundary sequences are not production adapters."]}
    try:
        require(REQUIRED_CASES <= {op["id"] for op in fixture["operations"]}, "MISSING_REQUIRED_CASE")
        require(any(op["kind"] == "chain" for op in fixture["operations"]), "MISSING_CHAIN")
        scenario = fixture["scenario"]
        require(len({scenario[k]["id"] for k in ("primary", "countertrend", "alternative")}) == 3,
                "HYPOTHESES_NOT_DISTINCT")
        require(scenario["primary"]["direction"] == scenario["direction"] and
                scenario["countertrend"]["direction"] != scenario["direction"], "NESTED_SCENARIO_CONTRACT")
        baseline = replay(fixture, work_dir)
        repeated = replay(fixture, work_dir)
        resumed = replay(fixture, work_dir, restart_after=[op["id"] for op in fixture["operations"]])
        require(baseline == repeated, "NONDETERMINISTIC")
        require(baseline == resumed, "RESTART_MISMATCH")
        # Compare every prefix against its execution with all later records present.
        for cutoff in sorted({op["at"] for op in fixture["operations"]}, key=utc):
            prefix = deepcopy(fixture)
            prefix["operations"] = [op for op in fixture["operations"] if utc(op["at"]) <= utc(cutoff)]
            short = replay(prefix, work_dir)
            visible = [row for row in baseline["timeline"] if utc(row["cutoff"]) <= utc(cutoff)]
            require(short["timeline"] == visible, "LOOK_AHEAD")
        report.update(verdict="PASS", invariants={
            k: "PASS" for k in ("determinism", "restart_equivalence", "no_look_ahead",
                               "idempotency", "recipient_integrity", "delivery_safety",
                               "scenario_integrity", "evidence_integrity", "temporal_integrity")},
                      restart_comparison="ALL_PERSISTED_FIELDS_EQUAL",
                      cutoff_sequence=[r["cutoff"] for r in baseline["timeline"]],
                      cases=[{"id": r["id"], "verdict": r["verdict"], "reason": r["reason"]}
                             for r in baseline["timeline"]],
                      semantic_output_sha256=digest(baseline), replay=baseline)
    except (ValueError, KeyError, TypeError) as exc:
        report.update(verdict="FAIL", reason="REPLAY_CONTRACT_FAILURE",
                      error_type=type(exc).__name__, failures=[str(exc)])
    return report


def summary(report):
    return (f'{report["mode"]}: {report["verdict"]}; '
            f'contract={report["contract_version"]}; base={report["base_sha"]}. '
            'No live or historical prediction approval.')
