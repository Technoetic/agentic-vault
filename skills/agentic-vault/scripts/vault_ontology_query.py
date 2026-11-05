#!/usr/bin/env python3
"""Read-only lookup and structured definition preflight for ontology contracts.

Reviewed annotations and current source bindings are data, not proof of truth,
approval, or the existence of underlying answer data. No query is executed.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
import re
import unicodedata

from vault_evidence import EvidenceError, decode
from vault_ontology import (REPORT_TYPE, SCHEMA_VERSION, _with_registry,
                            bounded_json, unavailable)


MAX_ITEMS = 32
MAX_TEXT = 8192
_ID = re.compile(r"[a-z][a-z0-9_.:-]{0,95}", re.ASCII)
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}", re.ASCII)
_COLLECTIONS = (("entities", "entity"), ("relations", "relation"),
                ("links", "link"), ("metrics", "metric"))
_SLOTS = ("unit", "grain", "population", "aggregation", "period")
_TRIPLE = ("subject", "predicate", "object")


def _id(value):
    return type(value) is str and _ID.fullmatch(value) is not None


def _text(value):
    return (type(value) is str and 0 < len(value) <= MAX_TEXT
            and bool(value.strip()) and "\x00" not in value)


def _keys(value, keys):
    return type(value) is dict and set(value) == set(keys)


def _period(value):
    if not _keys(value, ("start", "end")):
        return False
    if any(type(value[key]) is not str or not _DATE.fullmatch(value[key])
           for key in ("start", "end")):
        return False
    try:
        return date.fromisoformat(value["start"]) < date.fromisoformat(value["end"])
    except ValueError:
        return False


def _validate_request(request):
    """Validate the whole bounded host plan before consulting any definition."""
    if (not _keys(request, ("schema_version", "entities", "relations", "metrics"))
            or type(request["schema_version"]) is not int
            or request["schema_version"] != SCHEMA_VERSION):
        raise ValueError("invalid_request")
    for key in ("entities", "relations", "metrics"):
        if type(request[key]) is not list or len(request[key]) > MAX_ITEMS:
            raise ValueError("invalid_request")
    entities = request["entities"]
    if any(not _id(value) for value in entities) or len(set(entities)) != len(entities):
        raise ValueError("invalid_request")
    triples = set()
    for relation in request["relations"]:
        if (not _keys(relation, _TRIPLE)
                or any(not _id(relation[key]) for key in _TRIPLE)):
            raise ValueError("invalid_request")
        triple = tuple(relation[key] for key in _TRIPLE)
        if triple in triples:
            raise ValueError("invalid_request")
        triples.add(triple)
    metrics = set()
    for metric in request["metrics"]:
        if (not _keys(metric, ("id", "slots")) or not _id(metric["id"])
                or metric["id"] in metrics or type(metric["slots"]) is not dict
                or not set(metric["slots"]).issubset(_SLOTS)):
            raise ValueError("invalid_request")
        metrics.add(metric["id"])
        for slot, value in metric["slots"].items():
            if not (_period(value) if slot == "period" else _text(value)):
                raise ValueError("invalid_request")


def _report(registry, status, *, context=None, matches=None, gaps=None):
    return {
        "type": REPORT_TYPE, "schema_version": SCHEMA_VERSION,
        "authority": "data_only", "registry_sha256": registry["registry_sha256"],
        "status": status, "context": [] if context is None else context,
        "matches": [] if matches is None else matches,
        "gaps": [] if gaps is None else gaps,
    }


def _index(registry):
    return {record["id"]: (kind, record)
            for collection, kind in _COLLECTIONS for record in registry[collection]}


def _context(registry, selected):
    # Declaration order is preserved; neither labels nor source quotes normalize.
    return [{"kind": kind, "record": record}
            for collection, kind in _COLLECTIONS for record in registry[collection]
            if record["id"] in selected]


def _match(kind, record):
    return {"id": record["id"], "kind": kind, "label": record.get("label", record["id"])}


class _Selection:
    def __init__(self, registry):
        self.records = _index(registry)
        self.selected = set()
        self.gaps = []

    def gap(self, code, **fields):
        gap = {"code": code, **fields}
        if gap not in self.gaps:
            self.gaps.append(gap)

    def add(self, term_id):
        if term_id in self.selected:
            return
        kind, record = self.records[term_id]
        self.selected.add(term_id)
        if record["review"] != "reviewed":
            self.gap("unreviewed_dependency", id=term_id)
        if kind == "link":
            for key in _TRIPLE:
                self.add(record[key])


def search_terms(vault: Path, snapshot: dict, text: str) -> dict:
    """Find reviewed entity/metric labels, aliases, or IDs by exact NFC casefold."""
    if not _text(text):
        return unavailable("invalid_search_text")
    normalized = unicodedata.normalize("NFC", text).casefold()

    def build(registry):
        found = []
        for collection, kind in (("entities", "entity"), ("metrics", "metric")):
            for record in registry[collection]:
                if record["review"] != "reviewed":
                    continue
                terms = (record["id"], record["label"], *record["aliases"])
                if any(unicodedata.normalize("NFC", term).casefold() == normalized for term in terms):
                    found.append((kind, record))
        status = "not_found" if not found else "matched" if len(found) == 1 else "ambiguous"
        selected = {found[0][1]["id"]} if len(found) == 1 else set()
        return _report(registry, status, context=_context(registry, selected),
                       matches=[_match(kind, record) for kind, record in found])

    return _with_registry(vault, snapshot, build)


def get_term(vault: Path, snapshot: dict, term_id: str) -> dict:
    """Get a global ID only with its complete reviewed definition dependencies."""
    if not _id(term_id):
        return unavailable("invalid_term_id")

    def build(registry):
        selection = _Selection(registry)
        if term_id not in selection.records:
            return _report(registry, "not_found")
        selection.add(term_id)
        if selection.gaps:
            return _report(registry, "needs_review", gaps=selection.gaps)
        kind, record = selection.records[term_id]
        return _report(registry, "matched", context=_context(registry, selection.selected),
                       matches=[_match(kind, record)])

    return _with_registry(vault, snapshot, build)


def check_answerable(vault: Path, snapshot: dict, request: dict) -> dict:
    """Preflight explicit definitions and slots; success only readies evidence work."""
    try:
        # Do not let a caller change the validated plan during registry reads.
        request = decode(bounded_json(request))
        _validate_request(request)
    except (ValueError, TypeError, EvidenceError, RecursionError, OverflowError):
        return unavailable("invalid_request")

    def build(registry):
        selection = _Selection(registry)
        entities = {record["id"]: record for record in registry["entities"]}
        relations = {record["id"]: record for record in registry["relations"]}
        metrics = {record["id"]: record for record in registry["metrics"]}
        links = {tuple(record[key] for key in _TRIPLE): record for record in registry["links"]}
        if not any(request[key] for key in ("entities", "relations", "metrics")):
            selection.gap("empty_plan")
        for term_id in request["entities"]:
            if term_id not in entities:
                selection.gap("unknown_entity", id=term_id)
            else:
                selection.add(term_id)
        for relation in request["relations"]:
            known = True
            for key in ("subject", "object"):
                term_id = relation[key]
                if term_id not in entities:
                    selection.gap("unknown_entity", id=term_id)
                    known = False
                else:
                    selection.add(term_id)
            predicate = relation["predicate"]
            if predicate not in relations:
                selection.gap("unknown_relation", id=predicate)
                known = False
            else:
                selection.add(predicate)
            triple = tuple(relation[key] for key in _TRIPLE)
            if known:
                if triple not in links:
                    selection.gap("unsupported_link", **relation)
                else:
                    selection.add(links[triple]["id"])
        for metric in request["metrics"]:
            term_id, slots = metric["id"], metric["slots"]
            definition = metrics.get(term_id)
            if definition is None:
                selection.gap("unknown_metric", id=term_id)
            else:
                selection.add(term_id)
            for slot in _SLOTS:
                if slot not in slots:
                    selection.gap("missing_slot", id=term_id, slot=slot)
                elif definition is not None and slot != "period" and slots[slot] != definition[slot]:
                    selection.gap("conflicting_slot", id=term_id, slot=slot)
        if selection.gaps:
            return _report(registry, "needs_clarification", gaps=selection.gaps)
        return _report(registry, "ready_for_evidence", context=_context(registry, selection.selected))

    return _with_registry(vault, snapshot, build)
