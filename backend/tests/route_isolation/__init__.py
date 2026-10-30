"""Building blocks of the organization isolation sweep (``tests/test_route_isolation_sweep.py``).

Every API route is classified exactly once: as a :class:`Probe` in the ``PROBES`` table of one
area module next to this file (``core``, ``finance``, ``documents``, ``access``), or as an
exemption with a reason in ``EXEMPT`` of the sweep test. The sweep signs in as organization B and
sends each probe with the ids of records that belong to *another* organization.

A probe names those records with SQL (see :func:`row`): the query runs as the migrator (it sees
every organization) and returns one row whose columns are named like the path parameters. The same
names fill query parameters, header parameters and body fields, so a create that references a
foreign parent (``POST /api/units`` with another organization's ``property_id``) is swept too.
Every UUID of that row is also replaced by a fresh random UUID for a second, "twin" request: the
answer to the foreign id must not differ from the answer to an id that exists nowhere.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass, field
from typing import Any

# Bodies that are not given in a probe are generated from the request schema (see ``example``).
# These values satisfy the usual field types; a probe overrides what a route needs beyond that.
SAMPLE_PDF = b"%PDF-1.4\n% organization isolation sweep\n%%EOF\n"
SAMPLE_TEXT = "Isolationstest"
# Conditional commands (app/api/preconditions.py) refuse a missing If-Match before any lookup;
# '"none"' states "no predecessor", which lets the request reach the record lookup.
NO_PREDECESSOR = '"none"'


@dataclass(frozen=True)
class Probe:
    """How the sweep calls one route as organization B.

    ids:     SQL returning one record of another organization; columns are named like the path,
             query, header or body fields they fill. ``:b`` is organization B's id. Empty for
             routes that take no id (lists, own creates).
    body:    JSON body, or form fields for multipart routes. A string value ``"{name}"`` is
             replaced by the column ``name`` of the ``ids`` row. ``None``: generated from the
             request schema, with every field named like an ``ids`` column filled from it.
    query, headers: like ``body``; required parameters not given here are generated.
    status:  the answer expected when the request carries foreign ids. 404 is the rule
             (architecture.md 7.4: existence is never revealed); any other value needs ``because``.
    because: why this route answers ``status`` instead of 404.
    """

    ids: str = ""
    body: Any = None
    query: dict[str, Any] = field(default_factory=dict)
    headers: dict[str, str] = field(default_factory=dict)
    status: int = 404
    because: str = ""

    def __post_init__(self) -> None:
        if self.status != 404 and not self.because:
            raise ValueError(f"Probe expecting {self.status} instead of 404 needs a reason (because=...)")


def row(table: str, **columns: str) -> str:
    """SQL for one record of ``table`` that belongs to another organization than B.

    ``row("unit", unit_id="id", property_id="property_id")`` selects a foreign unit's id as
    ``unit_id`` and its property as ``property_id``. Without columns, ``id`` is selected under the
    name ``<table>_id``.
    """
    columns = columns or {f"{table}_id": "id"}
    selected = ", ".join(f"x.{column} AS {name}" for name, column in columns.items())
    return f'SELECT {selected} FROM "{table}" x WHERE x.organization_id IS DISTINCT FROM :b ORDER BY x.id LIMIT 1'


# -- building one request ---------------------------------------------------------------------

PATH_PARAMETER = re.compile(r"{([^}:]+)(?::[^}]+)?}")
UUID_PATTERN = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def is_uuid(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    try:
        uuid.UUID(value)
    except ValueError:
        return False
    return True


def fill(template: Any, ids: dict[str, str]) -> Any:
    """Replace ``"{name}"`` strings anywhere in ``template`` by ``ids[name]``."""
    if isinstance(template, str) and template.startswith("{") and template.endswith("}") and template[1:-1] in ids:
        return ids[template[1:-1]]
    if isinstance(template, dict):
        return {key: fill(value, ids) for key, value in template.items()}
    if isinstance(template, list):
        return [fill(value, ids) for value in template]
    return template


def resolve(schema: dict, components: dict) -> dict:
    while "$ref" in schema:
        schema = components[schema["$ref"].rsplit("/", 1)[1]]
    return schema


def example(schema: dict, components: dict, ids: dict[str, str], name: str = "") -> Any:
    """A small value that satisfies ``schema``; fields named like an id column get that id."""
    if name in ids:
        return ids[name]
    schema = resolve(schema, components)
    if "const" in schema:
        return schema["const"]
    if schema.get("enum"):
        return schema["enum"][0]
    for combinator in ("anyOf", "oneOf", "allOf"):
        options = [resolve(option, components) for option in schema.get(combinator, [])]
        options = [option for option in options if option.get("type") != "null"]
        if options:
            return example(options[0], components, ids, name)
    kind = schema.get("type")
    if kind == "object" or "properties" in schema:
        properties = schema.get("properties", {})
        wanted = [key for key in properties if key in schema.get("required", []) or key in ids]
        return {key: example(properties[key], components, ids, key) for key in wanted}
    if kind == "array":
        return [example(schema.get("items", {}), components, ids, name) for _ in range(schema.get("minItems", 0))]
    if kind == "integer":
        return max(1, int(schema.get("minimum", schema.get("exclusiveMinimum", 0) + 1)))
    if kind == "number":
        return max(1, schema.get("minimum", 1))
    if kind == "boolean":
        return False
    text_format = schema.get("format", "")
    if text_format == "uuid" or name.endswith("_id"):
        return str(uuid.uuid4())
    if text_format == "date":
        return "2025-01-01"
    if text_format == "date-time":
        return "2025-01-01T00:00:00Z"
    if text_format == "binary" or "contentMediaType" in schema:
        return SAMPLE_PDF
    if "pattern" in schema and "\\d" in schema["pattern"]:
        return "1.00"  # Decimal fields are strings with a numeric pattern
    value = SAMPLE_TEXT
    return (value * (schema.get("minLength", 0) // len(value) + 1))[: schema.get("maxLength", len(value))]


@dataclass
class Request:
    method: str
    url: str
    query: dict[str, Any]
    headers: dict[str, str]
    json: Any = None
    form: dict[str, Any] | None = None
    files: dict[str, Any] | None = None

    def send_kwargs(self) -> dict[str, Any]:
        kwargs: dict[str, Any] = {"params": self.query, "headers": self.headers}
        if self.form is not None or self.files is not None:
            kwargs["data"] = self.form or {}
            kwargs["files"] = self.files or {}
        elif self.json is not None:
            kwargs["json"] = self.json
        return kwargs


def build_request(method: str, path: str, operation: dict, components: dict, probe: Probe, ids: dict[str, str]) -> Request:
    """The request for one probe with the given ids (foreign ones, or their random twins)."""
    url = PATH_PARAMETER.sub(lambda match: ids[match.group(1)], path)
    query = fill(dict(probe.query), ids)
    headers = fill(dict(probe.headers), ids)
    for parameter in operation.get("parameters", []):
        name, where = parameter["name"], parameter["in"]
        target = {"query": query, "header": headers}.get(where)
        if target is None or name in target:
            continue
        if name in ids or parameter.get("required"):
            target[name] = str(example(parameter.get("schema", {}), components, ids, name))
        elif name == "if-match":
            target[name] = NO_PREDECESSOR
    request = Request(method, url, query, headers)
    content = operation.get("requestBody", {}).get("content", {})
    if "multipart/form-data" in content or "application/x-www-form-urlencoded" in content:
        schema = resolve(next(iter(content.values()))["schema"], components)
        given = fill(probe.body, ids) if probe.body is not None else {}
        request.form, request.files = {}, {}
        for key, value in {**example(schema, components, ids), **given}.items():
            if isinstance(value, bytes):
                request.files[key] = ("isolation.pdf", value, "application/pdf")
            elif value is not None:
                request.form[key] = str(value)
    elif "application/json" in content:
        request.json = (
            fill(probe.body, ids) if probe.body is not None
            else example(content["application/json"]["schema"], components, ids)
        )
    return request


def twin_ids(ids: dict[str, str]) -> dict[str, str]:
    """The same ids with every UUID replaced by one that exists nowhere."""
    return {name: str(uuid.uuid4()) if is_uuid(value) else value for name, value in ids.items()}


def normalized(text: str, *id_sets: dict[str, str]) -> str:
    """Response text with the ids the request carried replaced by their column names."""
    for ids in id_sets:
        for name, value in ids.items():
            if is_uuid(value):
                text = text.replace(value, "{" + name + "}")
    return text


def is_request_validation_error(status: int, payload: Any) -> bool:
    """FastAPI's own 422: the request never reached the route's code."""
    return status == 422 and isinstance(payload, dict) and isinstance(payload.get("detail"), list) and all(
        isinstance(item, dict) and "loc" in item and "type" in item for item in payload["detail"]
    )
