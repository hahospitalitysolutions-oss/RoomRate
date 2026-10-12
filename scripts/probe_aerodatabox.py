"""Does AeroDataBox's free plan cover RoomRate's flight-demand needs?

Run from the project folder with a RapidAPI key subscribed to AeroDataBox
(the free "Basic" plan is enough to test):

    set AERODATABOX_API_KEY=...            (PowerShell: $env:AERODATABOX_API_KEY="...")
    python scripts/probe_aerodatabox.py --airport RHO

The key can also sit in .env as AERODATABOX_API_KEY; it is never printed.

For arrivals at --airport it asks one 12-hour window (08:00–20:00 local) at
1, 7, 14, 30, 60, 90, 180 and 330 days ahead: how far ahead do scheduled
flights come back, how many, and what the plan's quota headers say. Then it
estimates the calls RoomRate would need per month. About 8 calls. The answers
are saved to tmp/aerodatabox_probe.json.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import date, timedelta
from pathlib import Path

import httpx

PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUTPUT = PROJECT_ROOT / "tmp" / "aerodatabox_probe.json"

RAPIDAPI_HOST = "aerodatabox.p.rapidapi.com"
OFFSETS_DAYS = (1, 7, 14, 30, 60, 90, 180, 330)
QUERY = {
    "withLeg": "false",
    "direction": "Arrival",
    "withCancelled": "false",
    "withCodeshared": "false",
    "withCargo": "false",
    "withPrivate": "false",
    "withLocation": "false",
}


def load_key(explicit: str | None) -> str:
    if explicit:
        return explicit
    if os.environ.get("AERODATABOX_API_KEY"):
        return os.environ["AERODATABOX_API_KEY"]
    env_file = PROJECT_ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text(encoding="utf-8-sig").splitlines():
            if line.strip().startswith("AERODATABOX_API_KEY="):
                return line.split("=", 1)[1].strip().strip("'\"")
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--airport", default="RHO", help="IATA code of the island airport")
    parser.add_argument("--key", default=None, help="RapidAPI key (default: AERODATABOX_API_KEY)")
    parser.add_argument("--airports-planned", type=int, default=10, help="island airports RoomRate would track")
    parser.add_argument("--horizon-days", type=int, default=60, help="days ahead RoomRate would track")
    args = parser.parse_args()

    key = load_key(args.key)
    if not key:
        print("Λείπει το κλειδί: βάλε AERODATABOX_API_KEY στο .env ή δώσε --key.")
        return 2

    headers = {"x-rapidapi-key": key, "x-rapidapi-host": RAPIDAPI_HOST}
    results = []
    quota: dict[str, str] = {}
    # The plan's units left after each call: their drop is what a call costs.
    units_left: list[int] = []
    with httpx.Client(timeout=30) as client:
        for offset in OFFSETS_DAYS:
            day = date.today() + timedelta(days=offset)
            url = f"https://{RAPIDAPI_HOST}/flights/airports/iata/{args.airport}/{day}T08:00/{day}T20:00"
            try:
                response = client.get(url, params=QUERY, headers=headers)
            except httpx.HTTPError as exc:
                print(f"+{offset:>3} μέρες ({day}): σφάλμα δικτύου {exc}")
                results.append({"offset_days": offset, "date": str(day), "error": str(exc)})
                break
            quota.update({name: value for name, value in response.headers.items() if name.lower().startswith("x-ratelimit")})
            remaining = response.headers.get("x-ratelimit-api-units-remaining")
            if remaining and remaining.isdigit():
                units_left.append(int(remaining))
            entry = {"offset_days": offset, "date": str(day), "status": response.status_code}
            if response.status_code == 204:
                entry["arrivals"] = 0
                print(f"+{offset:>3} μέρες ({day}): καμία πτήση (204)")
            elif response.status_code == 200:
                body = response.json()
                arrivals = body.get("arrivals") or []
                entry["arrivals"] = len(arrivals)
                entry["airlines"] = sorted({(flight.get("airline") or {}).get("name", "?") for flight in arrivals})[:15]
                if offset == OFFSETS_DAYS[0]:
                    entry["sample"] = arrivals[:3]
                print(f"+{offset:>3} μέρες ({day}): {len(arrivals)} αφίξεις 08:00–20:00")
            else:
                entry["body"] = response.text[:400]
                print(f"+{offset:>3} μέρες ({day}): HTTP {response.status_code} · {response.text[:200]}")
                if response.status_code in (401, 403, 429):
                    results.append(entry)
                    break
            results.append(entry)

    reached = [entry["offset_days"] for entry in results if entry.get("arrivals")]
    print("\nΑποτέλεσμα:")
    print(f"  Δίνει προγραμματισμένες πτήσεις έως +{max(reached)} μέρες." if reached else "  Δεν ήρθαν μελλοντικές πτήσεις.")
    if quota:
        print("  Όρια πλάνου (headers):")
        for name, value in sorted(quota.items()):
            print(f"    {name}: {value}")
    # The plan bills units, not requests (probe 2026-10-11: 400 units but
    # 1,600 requests a month on the free plan, 2 units a call).
    units_per_call = (
        (units_left[0] - units_left[-1]) / (len(units_left) - 1) if len(units_left) > 1 else None
    ) or 2.0
    print(f"  Κόστος μιας κλήσης: {units_per_call:g} μονάδες.")
    # Two 12-hour windows cover a day; a weekly refresh of the horizon.
    monthly_calls = args.airports_planned * args.horizon_days * 2 * 4.3
    monthly_units = monthly_calls * units_per_call
    print(
        f"  Για {args.airports_planned} αεροδρόμια × {args.horizon_days} μέρες × 2 παράθυρα, "
        f"με εβδομαδιαία ανανέωση: ~{monthly_calls:,.0f} κλήσεις = ~{monthly_units:,.0f} μονάδες τον μήνα."
    )
    units_limit = next(
        (value for name, value in quota.items() if name.lower() == "x-ratelimit-api-units-limit"), None
    )
    if units_limit and units_limit.isdigit():
        verdict = "καλύπτει" if int(units_limit) >= monthly_units else "ΔΕΝ καλύπτει"
        print(f"  Το πλάνο σου έχει {units_limit} μονάδες τον μήνα: {verdict} αυτή τη χρήση.")
        if units_left:
            print(f"  Απομένουν {units_left[-1]} μονάδες.")

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(
        json.dumps({"airport": args.airport, "results": results, "quota_headers": quota}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    print(f"\nΑποθηκεύτηκε στο {OUTPUT.relative_to(PROJECT_ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
