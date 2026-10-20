"""Isolated deterministic lifecycle; inputs are normalized evidence contracts.

This module does not classify news text, assert source independence or call MT5.
Those upstream attestations must be supplied with auditable opaque references.
"""
from copy import deepcopy
from dataclasses import asdict
from datetime import timedelta
import json
from news_notification_policy import Policy, canonical, digest, identifier, intent, stamp, utc


ASSESSMENTS = {"supports", "conflicts", "insufficient_data", "not_evaluated"}
FACTS = {"unverified", "corroborated", "verified", "corrected", "retracted", "cancelled"}
HYPOTHESES = {"proposed", "supported", "contradicted", "insufficient_data", "not_evaluated"}
CHANGES = {"initial", "development", "repeat", "redistribution", "recalculation",
           "fact_correction", "hypothesis_contradiction", "context_update", "resolution"}


def refs(values):
    if not isinstance(values, list):
        raise ValueError("reference list required")
    return sorted({identifier(v) for v in values})


def normalize(raw, now, policy):
    allowed = {"input_id", "upstream_story_id", "member_event_ids", "published_at_utc",
               "observed_at_utc", "available_at_utc", "significant", "change_kind", "assessment",
               "fact_status", "hypothesis_status", "hypothesis_id", "contradicts_hypothesis_id",
               "fact_refs", "mechanism_refs", "evidence", "resolution_reason", "display_text"}
    if set(raw) - allowed:
        raise ValueError("unsupported input fields")
    value = {k: identifier(raw[k]) for k in ("input_id", "upstream_story_id", "hypothesis_id")}
    value["member_event_ids"] = refs(raw["member_event_ids"])
    if not value["member_event_ids"]:
        raise ValueError("member lineage required")
    for key in ("published_at_utc", "observed_at_utc", "available_at_utc"):
        value[key] = stamp(utc(raw[key]))
    published, observed, available = (utc(value[k]) for k in
                                    ("published_at_utc", "observed_at_utc", "available_at_utc"))
    if not published <= observed <= available <= now:
        raise ValueError("inadmissible input chronology")
    if now - available > timedelta(seconds=policy.evidence_max_age_seconds):
        raise ValueError("stale input")
    if type(raw["significant"]) is not bool:
        raise ValueError("significance must be explicit")
    value["significant"] = raw["significant"]
    for key, choices in (("change_kind", CHANGES), ("assessment", ASSESSMENTS),
                         ("fact_status", FACTS), ("hypothesis_status", HYPOTHESES)):
        if raw[key] not in choices:
            raise ValueError("invalid " + key)
        value[key] = raw[key]
    value["contradicts_hypothesis_id"] = identifier(raw["contradicts_hypothesis_id"]) if raw.get("contradicts_hypothesis_id") else None
    value["fact_refs"], value["mechanism_refs"] = refs(raw["fact_refs"]), refs(raw["mechanism_refs"])
    evidence = []
    for item in raw["evidence"]:
        if set(item) != {"ref", "origin_ref", "subject_ref", "relation", "kind", "verified", "independent", "observed_at_utc", "available_at_utc"}:
            raise ValueError("evidence schema mismatch")
        if item["kind"] not in {"fact", "validation", "correction", "resolution"}:
            raise ValueError("unknown evidence kind")
        if type(item["verified"]) is not bool or type(item["independent"]) is not bool:
            raise ValueError("explicit evidence attestation required")
        if item["relation"] not in {"supports", "contradicts", "corrects", "resolves", "context"}:
            raise ValueError("unknown evidence relation")
        eo, ea = utc(item["observed_at_utc"]), utc(item["available_at_utc"])
        if not eo <= ea <= available or now - eo > timedelta(seconds=policy.evidence_max_age_seconds):
            raise ValueError("inadmissible evidence chronology")
        evidence.append({"ref": identifier(item["ref"]), "origin_ref": identifier(item["origin_ref"]),
                         "subject_ref": identifier(item["subject_ref"]), "relation": item["relation"],
                         "kind": item["kind"], "verified": item["verified"], "independent": item["independent"],
                         "observed_at_utc": stamp(eo), "available_at_utc": stamp(ea)})
    if len({e["ref"] for e in evidence}) != len(evidence):
        raise ValueError("duplicate evidence reference")
    value["evidence"] = sorted(evidence, key=lambda e: e["ref"])
    value["resolution_reason"] = raw.get("resolution_reason")
    if value["resolution_reason"] not in {None, "verified_correction", "documented_cancellation", "authorized_manual"}:
        raise ValueError("invalid resolution reason")
    return value


def semantic(value):
    return {k: deepcopy(value[k]) for k in ("assessment", "fact_status", "hypothesis_status",
            "hypothesis_id", "contradicts_hypothesis_id", "fact_refs", "mechanism_refs", "significant")}


class Lifecycle:
    def __init__(self, store, policy):
        if not isinstance(policy, Policy):
            raise ValueError("explicit policy required")
        self.store, self.policy = store, policy
        with store.transaction():
            payload = canonical(asdict(policy))
            old = store.db.execute("SELECT payload FROM policies WHERE ns=? AND version=?", (store.ns, policy.version)).fetchone()
            if old and old[0] != payload:
                raise ValueError("policy version reused with different values")
            store.db.execute("INSERT OR IGNORE INTO policies VALUES (?,?,?)", (store.ns, policy.version, payload))

    def _expire(self, now):
        s, p = self.store, self.policy
        for row in s.db.execute("SELECT payload FROM episodes WHERE ns=?", (s.ns,)).fetchall():
            ep = json.loads(row[0])
            if ep["state"] in {"RESOLVED", "EXPIRED"}:
                continue
            # Lifetime policy is frozen when each episode is created.
            deadline = min(utc(ep["created_at_utc"]) + timedelta(seconds=ep["max_seconds"]),
                           utc(ep["last_material_at_utc"]) + timedelta(seconds=ep["inactivity_seconds"]))
            if now >= deadline:
                ep.update(state="EXPIRED", closed_at_utc=stamp(now))
                s.save_episode(ep)
                s.log("EXPIRED", now, {"episode_id": ep["episode_id"], "deadline": stamp(deadline)})
                s.db.execute("UPDATE outbox SET status='expired' WHERE ns=? AND episode=? AND status IN ('queued','failed')",
                             (s.ns, ep["episode_id"]))

    def expire(self, *, now):
        moment = utc(now)
        with self.store.transaction():
            self.store.clock(moment)
            self._expire(moment)

    def _identity(self, value):
        s = self.store
        found = set()
        for table, column, keys in (("aliases", "alias", [value["upstream_story_id"]]),
                                    ("members", "member", value["member_event_ids"])):
            for key in keys:
                rows = s.db.execute(f"SELECT m.story FROM {table} m JOIN stories s ON m.ns=s.ns AND m.story=s.id WHERE m.ns=? AND m.{column}=? AND s.status='active'", (s.ns, key))
                found.update(row[0] for row in rows)
        if not found:
            # An alias of a migrated parent cannot silently become a new story.
            old = s.db.execute("SELECT story FROM aliases WHERE ns=? AND alias=?", (s.ns, value["upstream_story_id"])).fetchall()
            if old:
                return None, "requiring_review"
        if len(found) > 1:
            return None, "ambiguous"
        return (next(iter(found)), "resolved") if found else (None, "new")

    def process(self, raw, recipients, *, now, scenario=None):
        moment, s, p = utc(now), self.store, self.policy
        value = normalize(raw, moment, p)
        recipients = refs(recipients)
        if any(not r.startswith("recipient_") for r in recipients):
            raise ValueError("opaque recipient keys required")
        input_hash = digest(value)
        with s.transaction():
            s.clock(moment)
            existing = s.db.execute("SELECT hash,result FROM inputs WHERE ns=? AND id=?", (s.ns, value["input_id"])).fetchone()
            if existing:
                if existing["hash"] != input_hash:
                    raise ValueError("input version mutated")
                result = json.loads(existing["result"])
                result.update(duplicate=True, notification_ids=[])
                return {**result, "scenario": deepcopy(scenario)}
            self._expire(moment)
            for evidence in value["evidence"]:
                encoded = canonical(evidence)
                prior = s.db.execute("SELECT payload FROM evidence WHERE ns=? AND ref=?", (s.ns, evidence["ref"])).fetchone()
                if prior and prior[0] != encoded:
                    raise ValueError("immutable evidence version changed")
                s.db.execute("INSERT OR IGNORE INTO evidence VALUES (?,?,?)", (s.ns, evidence["ref"], encoded))
            story, identity_status = self._identity(value)
            result = {"identity_status": identity_status, "notification_ids": [], "duplicate": False}
            if identity_status in {"ambiguous", "requiring_review"}:
                s.log("IDENTITY_REVIEW", moment, {"input_id": value["input_id"], "status": identity_status})
            else:
                if story is None:
                    story = "story_" + digest([s.ns, value["input_id"]])[:24]
                    s.db.execute("INSERT INTO stories VALUES (?,?,?)", (s.ns, story, "active"))
                result.update(canonical_story_id=story, identity_status="resolved")
                s.db.execute("INSERT OR IGNORE INTO aliases VALUES (?,?,?)", (s.ns, value["upstream_story_id"], story))
                for member in value["member_event_ids"]:
                    s.db.execute("INSERT OR IGNORE INTO members VALUES (?,?,?)", (s.ns, member, story))
                ep = s.episode(story)
                previous = deepcopy(ep["semantic"]) if ep else None
                sem = semantic(value)
                verified = {e["kind"] for e in value["evidence"] if e["verified"]}
                independent = any(e["verified"] and e["independent"] and e["kind"] == "fact" for e in value["evidence"])
                kind = value["change_kind"]
                if value["assessment"] in {"supports", "conflicts"} or value["hypothesis_status"] in {"supported", "contradicted"}:
                    if "validation" not in verified:
                        raise ValueError("validated assessment requires evidence")
                if value["hypothesis_status"] == "contradicted" and not value["contradicts_hypothesis_id"]:
                    raise ValueError("contradiction requires original hypothesis")
                if value["hypothesis_status"] == "contradicted" and not any(
                    e["verified"] and e["kind"] == "validation" and e["relation"] == "contradicts"
                    and e["subject_ref"] == value["contradicts_hypothesis_id"] for e in value["evidence"]
                ):
                    raise ValueError("contradiction evidence must address the original hypothesis")
                if value["fact_status"] in {"corrected", "retracted"} or kind == "fact_correction":
                    if "correction" not in verified:
                        raise ValueError("correction requires verified evidence")
                    if not any(e["verified"] and e["kind"] == "correction" and e["relation"] == "corrects"
                               and e["subject_ref"] in set(value["fact_refs"]) | set((previous or {}).get("fact_refs", []))
                               for e in value["evidence"]):
                        raise ValueError("correction must identify an affected fact")
                if value["fact_status"] in {"verified", "corroborated"} and "fact" not in verified:
                    raise ValueError("verified fact status requires evidence")
                if value["fact_status"] == "corroborated" and not independent:
                    raise ValueError("corroboration needs attested independence")
                if kind == "hypothesis_contradiction" and value["hypothesis_status"] != "contradicted":
                    raise ValueError("contradiction kind/status mismatch")
                if kind == "resolution" and (not value["resolution_reason"] or "resolution" not in verified):
                    raise ValueError("resolution requires explicit evidence")
                if kind == "resolution" and not any(e["verified"] and e["kind"] == "resolution" and e["relation"] == "resolves" for e in value["evidence"]):
                    raise ValueError("explicit resolution evidence relation required")
                changed = sem != previous or (kind == "resolution" and ep and ep["state"] not in {"RESOLVED", "EXPIRED"})
                material = changed and kind not in {"repeat", "redistribution", "recalculation"}
                if previous and sem["fact_refs"] != previous["fact_refs"] and kind not in {"fact_correction", "resolution"} and not independent:
                    material = False
                if previous and sem["fact_refs"] != previous["fact_refs"] and kind not in {"fact_correction", "resolution"}:
                    additions = set(sem["fact_refs"]) - set(previous["fact_refs"])
                    attestations = {e["subject_ref"] for e in value["evidence"] if e["kind"] == "fact" and e["verified"] and e["independent"] and e["relation"] == "supports"}
                    if not additions <= attestations or set(previous["fact_refs"]) - set(sem["fact_refs"]):
                        material = False
                if ep and utc(value["available_at_utc"]) < utc(ep["last_available_at_utc"]):
                    material = False
                    result["reason"] = "late_version_journal_only"
                if ep and ep["state"] in {"RESOLVED", "EXPIRED"} and (not material or kind == "resolution"):
                    result["reason"] = "terminal_episode_unchanged"
                    material = False
                elif ep is None or (ep["state"] in {"RESOLVED", "EXPIRED"} and material):
                    predecessor = ep["episode_id"] if ep else None
                    ep = {"canonical_story_id": story, "episode_id": "episode_" + digest([story, value["input_id"]])[:24],
                          "predecessor_episode_id": predecessor, "state": "NEW", "semantic_version": 0,
                          "semantic": sem, "created_at_utc": stamp(moment), "last_material_at_utc": stamp(moment),
                          "last_available_at_utc": value["available_at_utc"], "max_seconds": p.episode_max_seconds,
                          "inactivity_seconds": p.inactivity_seconds, "policy_version": p.version,
                          "migrated": len(s.ancestors(story)) > 1}
                    material = True
                if material:
                    ep["semantic_version"] += 1
                    ep.update(semantic=sem, semantic_fingerprint=digest(sem),
                              last_material_at_utc=stamp(moment), last_available_at_utc=value["available_at_utc"])
                    ep["state"] = "RESOLVED" if kind == "resolution" else ("NEW" if ep["semantic_version"] == 1 else "DEVELOPING")
                    if ep["state"] == "RESOLVED":
                        ep["resolution"] = {"reason": value["resolution_reason"], "evidence_refs": [e["ref"] for e in value["evidence"]]}
                    s.save_episode(ep)
                    transition = "NEW" if ep["semantic_version"] == 1 else "MATERIAL_UPDATE"
                    if kind == "resolution": transition = "RESOLVED"
                    s.log(transition, moment, {"episode_id": ep["episode_id"], "semantic_version": ep["semantic_version"],
                                             "input_id": value["input_id"], "previous": previous, "current": sem})
                    for recipient in recipients:
                        notification_kind = "MATERIAL_UPDATE"
                        if kind == "resolution": notification_kind = "EPISODE_RESOLVED"
                        elif kind == "fact_correction" or (sem["fact_status"] in {"corrected", "retracted"} and (not previous or sem["fact_status"] != previous["fact_status"])): notification_kind = "FACT_CORRECTION"
                        elif sem["hypothesis_status"] == "contradicted":
                            if not any(
                                e["verified"] and e["kind"] == "validation" and e["relation"] == "contradicts"
                                and e["subject_ref"] == sem["contradicts_hypothesis_id"]
                                and s.communicated(story, recipient, sem["contradicts_hypothesis_id"],
                                                   evidence_observed_at=e["observed_at_utc"])
                                for e in value["evidence"]
                            ):
                                continue
                            notification_kind = "MARKET_HYPOTHESIS_CONTRADICTED"
                        elif sem["assessment"] == "conflicts": notification_kind = "PA_HTF_CONTEXT_CONFLICT"
                        elif ep["semantic_version"] == 1 and not ep["migrated"]: notification_kind = "FIRST_SIGNIFICANT_STORY"
                        if not sem["significant"] and notification_kind not in {"FACT_CORRECTION", "MARKET_HYPOTHESIS_CONTRADICTED", "EPISODE_RESOLVED"}:
                            continue
                        # Migration must not reannounce a story already in predecessor history.
                        if ep["migrated"] and ep["semantic_version"] == 1:
                            # Preserve delivered/uncertain semantic history through migrations.
                            ancestors = s.ancestors(story) - {story}
                            inherited = s.db.execute("SELECT story,payload FROM outbox WHERE ns=? AND recipient=? AND status IN ('delivered','attempted','unknown')",
                                                     (s.ns, recipient)).fetchall()
                            if any(r["story"] in ancestors and json.loads(r["payload"]).get("semantic_fingerprint") == digest(sem) for r in inherited):
                                continue
                        message = intent(ep, previous, notification_kind, recipient,
                                         [e["ref"] for e in value["evidence"]], value["available_at_utc"], moment, p)
                        # Suppression delays ordinary updates instead of dropping them.
                        ready = moment
                        if notification_kind == "MATERIAL_UPDATE":
                            last = s.db.execute("SELECT last_at FROM outbox WHERE ns=? AND story=? AND recipient=? AND status='delivered' ORDER BY last_at DESC LIMIT 1", (s.ns, story, recipient)).fetchone()
                            if last and last[0]: ready = max(moment, utc(last[0]) + timedelta(seconds=p.suppression_seconds))
                        s.db.execute("INSERT OR IGNORE INTO outbox(ns,id,story,episode,recipient,status,next_at,payload) VALUES (?,?,?,?,?,'queued',?,?)",
                                     (s.ns, message["idempotency_key"], story, ep["episode_id"], recipient, stamp(ready), canonical(message)))
                        result["notification_ids"].append(message["idempotency_key"])
                else:
                    s.log("NON_MATERIAL", moment, {"input_id": value["input_id"], "story": story})
                result.update(episode_id=ep["episode_id"], state=ep["state"], semantic_version=ep["semantic_version"])
            s.db.execute("INSERT INTO inputs VALUES (?,?,?,?,?)", (s.ns, value["input_id"], input_hash, canonical(value), canonical(result)))
            return {**result, "scenario": deepcopy(scenario)}

    def migrate(self, *, migration_id, parents, children, authorization_ref, now):
        """Explicit merge/split: children map aliases to disjoint member lists.

        No heuristic authorizes a merge. The caller supplies a reviewed decision
        reference; all predecessor histories remain available through lineage.
        """
        s, moment = self.store, utc(now)
        identifier(migration_id); identifier(authorization_ref)
        parents = refs(parents)
        if not parents or not children:
            raise ValueError("migration needs parents and children")
        clean = {identifier(alias): refs(members) for alias, members in children.items()}
        flat = [m for members in clean.values() for m in members]
        if not all(clean.values()) or len(flat) != len(set(flat)):
            raise ValueError("split partitions must be nonempty and disjoint")
        with s.transaction():
            s.clock(moment)
            if s.db.execute("SELECT 1 FROM migrations WHERE ns=? AND id=?", (s.ns, migration_id)).fetchone():
                raise ValueError("migration already recorded")
            known = set()
            for parent in parents:
                row = s.db.execute("SELECT status FROM stories WHERE ns=? AND id=?", (s.ns, parent)).fetchone()
                if not row or row[0] != "active": raise ValueError("active parent required")
                known.update(r[0] for r in s.db.execute("SELECT member FROM members WHERE ns=? AND story=?", (s.ns, parent)))
            if set(flat) != known:
                raise ValueError("migration must account for all historical members")
            result = []
            for alias, members in sorted(clean.items()):
                if s.db.execute("SELECT 1 FROM aliases WHERE ns=? AND alias=?", (s.ns, alias)).fetchone():
                    raise ValueError("new migration alias required")
                child = "story_" + digest([s.ns, migration_id, alias])[:24]
                s.db.execute("INSERT INTO stories VALUES (?,?,'active')", (s.ns, child))
                s.db.execute("INSERT INTO aliases VALUES (?,?,?)", (s.ns, alias, child))
                for member in members: s.db.execute("INSERT INTO members VALUES (?,?,?)", (s.ns, member, child))
                for parent in parents: s.db.execute("INSERT INTO migrations VALUES (?,?,?,?,?)", (s.ns, migration_id, parent, child, stamp(moment)))
                result.append(child)
            for parent in parents:
                s.db.execute("UPDATE stories SET status='migrated' WHERE ns=? AND id=?", (s.ns, parent))
                s.db.execute("UPDATE outbox SET status='superseded' WHERE ns=? AND story=? AND status IN ('queued','failed')", (s.ns, parent))
            s.log("IDENTITY_MIGRATION", moment, {"id": migration_id, "parents": parents, "children": result, "authorization_ref": authorization_ref})
            return result
