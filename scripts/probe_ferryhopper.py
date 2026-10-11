"""See what Ferryhopper's MCP server answers, next to what RoomRate reads from it.

Run from the project folder on a machine with internet access (no key needed):

    python scripts/probe_ferryhopper.py
    python scripts/probe_ferryhopper.py --island Rhodes --from Piraeus --date 2026-11-13
    python scripts/probe_ferryhopper.py --show     (prints samples of the saved answers)

It lists the server's tools and calls three of them (ports, direct connections
of --island, trips --from → --island on --date). Every raw answer is saved to
tmp/ferryhopper_probe.json: send that file back so the parser in
api/services/ferryhopper_client.py can be checked against the real shapes.
About 4 tool calls; nothing is written to the database.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from api.services.ferry_demand_service import aggregate_trips  # noqa: E402
from api.services.ferryhopper_client import (  # noqa: E402
    CONNECTION_TOOLS,
    DEFAULT_MCP_URL,
    PORT_TOOLS,
    TRIP_TOOLS,
    FerryhopperClient,
    parse_ports,
    parse_trips,
)
from api.services.mcp_http_client import McpError, McpHttpClient  # noqa: E402

OUTPUT = PROJECT_ROOT / "tmp" / "ferryhopper_probe.json"


def next_friday(after_days: int = 7) -> date:
    day = date.today() + timedelta(days=after_days)
    return day + timedelta(days=(4 - day.weekday()) % 7)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--url", default=DEFAULT_MCP_URL)
    parser.add_argument("--island", default="Rhodes", help="island port for connections and trips")
    parser.add_argument("--from", dest="origin", default="Piraeus", help="departure port for the trip search")
    parser.add_argument("--date", type=date.fromisoformat, default=next_friday(), help="travel date YYYY-MM-DD")
    parser.add_argument("--show", action="store_true", help="print samples of the saved answers, no calls")
    args = parser.parse_args()
    if args.show:
        return show_saved()

    record: dict = {"url": args.url, "arguments": {"island": args.island, "from": args.origin, "date": str(args.date)}}
    mcp = McpHttpClient(args.url, client_name="roomrate-probe")
    client = FerryhopperClient(mcp, url=args.url, min_interval_seconds=1.0)
    try:
        try:
            info = mcp.initialize()
            tools = mcp.list_tools()
        except McpError as exc:
            print(f"Δεν απαντά ο server ({args.url}): {exc}")
            return 1
        record["server"] = info
        record["tools"] = tools
        print(f"Server: {json.dumps(info.get('serverInfo'), ensure_ascii=False)} · πρωτόκολλο {info.get('protocolVersion')}")
        print(f"Εργαλεία ({len(tools)}):")
        for tool in tools:
            schema = tool.get("inputSchema") or {}
            properties = list((schema.get("properties") or {}).keys())
            print(f"  - {tool.get('name')}: ορίσματα {properties} (υποχρεωτικά {schema.get('required') or []})")
        names = {tool.get("name") for tool in tools}
        for label, candidates in (("ports", PORT_TOOLS), ("connections", CONNECTION_TOOLS), ("trips", TRIP_TOOLS)):
            found = next((name for name in candidates if name in names), None)
            print(f"  → για {label} χρησιμοποιείται: {found or 'ΚΑΝΕΝΑ (θέλει αλλαγή στον client)'}")

        record["answers"] = {}
        step(record, "ports", lambda: client.ports(), lambda payload: summarize_ports(payload), client)
        step(record, "connections", lambda: client.direct_connections(args.island), lambda payload: summarize_ports(payload), client)
        step(
            record,
            "trips",
            lambda: client.trips(args.origin, args.island, args.date),
            lambda payload: summarize_trips(payload),
            client,
        )
    finally:
        client.close()

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(record, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    print(f"\nΑποθηκεύτηκαν όλες οι απαντήσεις στο {OUTPUT.relative_to(PROJECT_ROOT)}")
    return 0


def step(record: dict, name: str, call, summarize, client: FerryhopperClient) -> None:
    print(f"\n== {name} ==")
    try:
        call()
    except McpError as exc:
        record["answers"][name] = {"error": str(exc)}
        print(f"  σφάλμα: {exc}")
        return
    record["answers"][name] = client.last_payload
    summarize(client.last_payload)


def summarize_ports(payload) -> None:
    ports = parse_ports(payload)
    with_coordinates = sum(1 for port in ports if port.latitude is not None)
    print(f"  τύπος απάντησης: {type(payload).__name__}; διαβάστηκαν {len(ports)} λιμάνια, {with_coordinates} με συντεταγμένες")
    for port in ports[:5]:
        print(f"    {port}")


def summarize_trips(payload) -> None:
    trips = parse_trips(payload)
    print(f"  τύπος απάντησης: {type(payload).__name__}; διαβάστηκαν {len(trips)} δρομολόγια")
    for trip in trips[:10]:
        print(
            f"    αναχώρηση {trip.departure} · {trip.operator} · τιμή {trip.price_eur} € · "
            f"διαθέσιμο {trip.available} · απευθείας {trip.direct}"
        )
    print(f"  σύνοψη όπως αποθηκεύεται: {aggregate_trips(trips)}")
    if isinstance(payload, (dict, list)):
        text = json.dumps(payload, ensure_ascii=False)[:600]
        print(f"  αρχή της απάντησης: {text}")


def show_saved() -> int:
    """Raw samples of every saved answer, short enough to paste in a chat."""
    if not OUTPUT.exists():
        print(f"Δεν υπάρχει {OUTPUT.relative_to(PROJECT_ROOT)}: τρέξε πρώτα το probe χωρίς --show.")
        return 1
    record = json.loads(OUTPUT.read_text(encoding="utf-8"))
    for name, answer in (record.get("answers") or {}).items():
        print(f"\n== {name} ==")
        if isinstance(answer, dict):
            print(f"  κλειδιά: {list(answer)}")
        items = first_list_of_dicts(answer)
        if items:
            keys: dict[str, None] = {}
            for item in items:
                keys.update(dict.fromkeys(item))
            print(f"  {len(items)} αντικείμενα, πεδία: {list(keys)}")
        if name == "ports" and items:
            greek = [item for item in items if "gree" in json.dumps(item).lower()][:4]
            for item in greek or items[:4]:
                print(f"  {json.dumps(item, ensure_ascii=False)[:400]}")
        elif name == "trips" and items:
            print(json.dumps(items[0], ensure_ascii=False, indent=1)[:5000])
            for item in items[1:]:
                print(f"  επόμενο: {json.dumps(item, ensure_ascii=False)[:300]}")
        else:
            print(f"  {json.dumps(answer, ensure_ascii=False)[:1200]}")
    return 0


def first_list_of_dicts(node, depth: int = 0) -> list[dict]:
    if isinstance(node, list) and node and all(isinstance(item, dict) for item in node):
        return node
    if depth < 4 and isinstance(node, dict):
        for value in node.values():
            found = first_list_of_dicts(value, depth + 1)
            if found:
                return found
    return []


if __name__ == "__main__":
    sys.exit(main())
