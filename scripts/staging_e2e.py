"""Staging end-to-end test: scrape → price history → alert → WebSocket → pricing.

Drives one full RoomRate data journey against a LIVE stack (API + worker +
PostgreSQL + Apify credentials) using the internal API key, and prints a
PASS/FAIL/SKIP table like scripts/pre_production_checklist.py:

  1. /ready readiness and account context (property, baseline room, market key)
  2. Scrape job #1 over the API, polled to completion (a worker must run it)
  3. Tracked competitors saved from the scrape's map markers
  4. /api/v1/market/price-history returns points for the market key
  5. Deterministic price nudge: run #1's stored prices are lowered in SQL so
     scrape job #2 is GUARANTEED to cross the alert threshold (staging runs
     minutes apart otherwise observe identical prices and never alert)
  6. Scrape job #2 with a live /ws/alerts WebSocket attached — authenticated
     with a one-time ticket (POST /api/v1/notifications/ws-ticket), never a
     JWT in the URL — expecting a live price_change frame
  7. The price_change notification also exists in the REST feed
  8. POST /api/v1/agents/price-recommendation returns a validated
     recommendation (positive, ordered, plausible, deterministic confidence)

Usage:
    python scripts/staging_e2e.py [--api-base-url http://127.0.0.1:8000]
                                  [--check-in 2026-08-22 --check-out 2026-08-26]
                                  [--limit 5] [--nudge-factor 0.7]
                                  [--timeout-minutes 20]

Requirements: an ONBOARDED account (owned property + selected baseline room),
INTERNAL_API_KEY and DATABASE_URL in the environment/.env, and a process that
executes queued scrape jobs (ROOMRATE_PROCESS_ROLE=all locally, or a separate
worker in staging). The scrape steps spend real Apify credits.

The module imports cleanly with no live infrastructure; every step touches
the stack only at runtime and converts errors into FAIL rows.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
import urllib.parse
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"

POLL_INTERVAL_SECONDS = 10.0
HTTP_TIMEOUT_SECONDS = 30.0
# How long after job #2 completes we keep waiting for the live WS frame.
ALERT_FRAME_TIMEOUT_SECONDS = 90.0
VALID_CONFIDENCES = {"low", "medium", "high"}


@dataclass(frozen=True)
class CheckResult:
    """Outcome of one staging step."""

    name: str
    status: str  # PASS | FAIL | SKIP
    detail: str = ""


# ----------------------------------------------------------------------------
# Pure helpers (unit-tested; no infrastructure involved)
# ----------------------------------------------------------------------------


def ws_alerts_url(api_base_url: str, ticket: str) -> str:
    """Derive the ws(s):// alerts URL carrying a one-time ticket."""
    ws_base = api_base_url.rstrip("/")
    if ws_base.startswith("https"):
        ws_base = "wss" + ws_base[len("https"):]
    elif ws_base.startswith("http"):
        ws_base = "ws" + ws_base[len("http"):]
    return f"{ws_base}/ws/alerts?ticket={urllib.parse.quote(ticket, safe='')}"


def select_tracked_competitors(markers: list[dict], limit: int) -> list[dict]:
    """First ``limit`` distinct competitor properties from map markers."""
    selected: list[dict] = []
    seen: set[str] = set()
    for marker in markers:
        property_id = marker.get("property_id")
        if not property_id or property_id in seen:
            continue
        seen.add(property_id)
        selected.append(
            {"property_id": property_id, "room_package_id": marker.get("room_package_id")}
        )
        if len(selected) >= limit:
            break
    return selected


def is_price_change_frame(raw: str) -> bool:
    """Whether one WS frame is a price_change notification envelope."""
    try:
        frame = json.loads(raw)
    except (ValueError, TypeError):
        return False
    if not isinstance(frame, dict):
        return False
    notification = frame.get("notification")
    return isinstance(notification, dict) and notification.get("notification_type") == "price_change"


def recommendation_problems(body: dict) -> list[str]:
    """Validate the /agents/price-recommendation response invariants.

    Returns a list of human-readable problems; empty means the response obeys
    the post-validator contract (positive prices, ordered range, deterministic
    confidence, and a coherent recommendation_available flag).
    """
    problems: list[str] = []
    available = body.get("recommendation_available")
    recommendation = body.get("recommendation")

    if available is False:
        problems.append(
            "recommendation_available=false — staging just produced 2 scrape runs, "
            "so the market history must support a recommendation"
        )
        return problems

    if recommendation is None:
        problems.append("recommendation_available=true but recommendation is null/None")
        return problems

    recommended = recommendation.get("recommended_price_eur")
    low = recommendation.get("price_range_low_eur")
    high = recommendation.get("price_range_high_eur")
    for label, value in (("recommended", recommended), ("low", low), ("high", high)):
        if not isinstance(value, (int, float)) or value <= 0:
            problems.append(f"{label} price is not a positive number: {value!r}")
    if not problems and not (low <= recommended <= high):
        problems.append(f"range out of order: low={low} recommended={recommended} high={high}")
    if recommendation.get("confidence") not in VALID_CONFIDENCES:
        problems.append(f"confidence not in {sorted(VALID_CONFIDENCES)}: {recommendation.get('confidence')!r}")
    return problems


def default_market_dates(today: date) -> tuple[str, str]:
    """A 4-night stay ~30 days out: far enough to have live availability."""
    check_in = today + timedelta(days=30)
    check_out = check_in + timedelta(days=4)
    return check_in.isoformat(), check_out.isoformat()


def format_results_table(results: list[CheckResult]) -> str:
    """Render results as an aligned STEP | STATUS | DETAIL text table."""
    name_header, status_header, detail_header = "STEP", "STATUS", "DETAIL"
    name_width = max([len(name_header), *(len(r.name) for r in results)] or [len(name_header)])
    status_width = max([len(status_header), *(len(r.status) for r in results)] or [len(status_header)])
    lines = [
        f"{name_header:<{name_width}}  {status_header:<{status_width}}  {detail_header}",
        f"{'-' * name_width}  {'-' * status_width}  {'-' * len(detail_header)}",
    ]
    for result in results:
        lines.append(f"{result.name:<{name_width}}  {result.status:<{status_width}}  {result.detail}")
    return "\n".join(lines)


def summarize_exit_code(results: list[CheckResult]) -> int:
    """Exit 1 when any step failed; SKIPs are informational only."""
    return 1 if any(result.status == FAIL for result in results) else 0


def _one_line(text: str, max_length: int = 300) -> str:
    flattened = " ".join(str(text).split())
    return flattened if len(flattened) <= max_length else flattened[: max_length - 3] + "..."


# ----------------------------------------------------------------------------
# Live steps (imported lazily so the module loads with no infrastructure)
# ----------------------------------------------------------------------------


class StagingRun:
    """One staging execution: shared HTTP client, auth headers and context."""

    def __init__(self, args: argparse.Namespace):
        import httpx

        from api.config import settings

        self.args = args
        self.settings = settings
        self.base_url = (args.api_base_url or "http://127.0.0.1:8000").rstrip("/")
        account_id = args.account_id or str(settings.roomrate_default_account_id)
        self.headers = {
            "X-API-Key": settings.internal_api_key,
            "X-RoomRate-Account-ID": account_id,
            "Content-Type": "application/json",
        }
        self.account_id = account_id
        self.client = httpx.Client(timeout=HTTP_TIMEOUT_SECONDS, headers=self.headers)
        # Populated by the context step and reused by every later step.
        self.owned_property_id: str | None = None
        self.canonical_destination: str | None = None
        self.destination: str | None = None
        self.raw_destination: str | None = None
        self.room_type_category: str | None = None
        self.first_job_id: str | None = None

    # -- step 1 ---------------------------------------------------------
    def check_ready_and_context(self) -> CheckResult:
        name = "readiness & account context"
        try:
            ready = self.client.get(f"{self.base_url}/ready")
            if ready.status_code != 200:
                return CheckResult(name, FAIL, _one_line(f"/ready -> {ready.status_code}: {ready.text}"))
            me = self.client.get(f"{self.base_url}/api/v1/me")
            me.raise_for_status()
            body = me.json()
            self.owned_property_id = body.get("owned_property_id")
            self.canonical_destination = body.get("canonical_destination")
            self.destination = body.get("destination")
            self.raw_destination = body.get("raw_destination")
            self.room_type_category = self.args.room_type_category or body.get(
                "selected_room_type_category"
            )
            missing = [
                label
                for label, value in (
                    ("owned property", self.owned_property_id),
                    ("canonical destination", self.canonical_destination),
                    ("selected room type", self.room_type_category),
                )
                if not value
            ]
            if missing:
                return CheckResult(
                    name,
                    FAIL,
                    "account is not onboarded (missing: " + ", ".join(missing) + "); "
                    "complete onboarding for the staging account first",
                )
            return CheckResult(
                name,
                PASS,
                f"{self.canonical_destination} / {self.room_type_category}",
            )
        except Exception as exc:  # noqa: BLE001 — every failure becomes a row
            return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))

    # -- scrape helpers -------------------------------------------------
    def _create_scrape_job(self) -> dict:
        payload = {
            "owned_property_id": self.owned_property_id,
            "job_type": "competitor_search",
            "room_type_category": self.room_type_category,
            "destination": self.destination or self.canonical_destination,
            "raw_destination": self.raw_destination or self.destination,
            "check_in": self.args.check_in,
            "check_out": self.args.check_out,
            "adults": 2,
            "children": 0,
            "rooms": 1,
            "filters_payload": {"limit": self.args.limit},
        }
        response = self.client.post(f"{self.base_url}/api/v1/scrape-jobs/", json=payload)
        response.raise_for_status()
        return response.json()

    def _poll_job(self, job_id: str, timeout_seconds: float) -> dict:
        deadline = time.monotonic() + timeout_seconds
        while time.monotonic() < deadline:
            response = self.client.get(f"{self.base_url}/api/v1/scrape-jobs/{job_id}")
            response.raise_for_status()
            job = response.json()
            if job.get("status") == "completed":
                return job
            if job.get("status") == "failed":
                raise RuntimeError(f"scrape job failed: {job.get('error_message')}")
            time.sleep(POLL_INTERVAL_SECONDS)
        raise TimeoutError(
            f"scrape job {job_id} did not complete within {timeout_seconds:.0f}s "
            "(is a worker/scheduler executing queued jobs?)"
        )

    # -- step 2 ---------------------------------------------------------
    def run_first_scrape(self) -> CheckResult:
        name = "scrape job #1 (live)"
        if not self.owned_property_id:
            return CheckResult(name, SKIP, "no account context")
        try:
            job = self._create_scrape_job()
            completed = self._poll_job(job["id"], self.args.timeout_minutes * 60)
            self.first_job_id = completed["id"]
            return CheckResult(name, PASS, f"job {completed['id']} completed")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))

    # -- step 3 ---------------------------------------------------------
    def save_tracked_competitors(self) -> CheckResult:
        name = "tracked competitors"
        if not self.first_job_id:
            return CheckResult(name, SKIP, "no completed scrape")
        try:
            params = {
                "destination": self.destination or self.canonical_destination,
                "check_in": self.args.check_in,
                "check_out": self.args.check_out,
                "adults": 2,
                "children": 0,
                "rooms": 1,
                "room_type_category": self.room_type_category,
                "owned_property_id": self.owned_property_id,
                "scrape_job_id": self.first_job_id,
                "limit": 50,
            }
            markers = self.client.get(
                f"{self.base_url}/api/v1/maps/competitors", params=params
            )
            markers.raise_for_status()
            competitors = select_tracked_competitors(markers.json(), limit=3)
            if not competitors:
                return CheckResult(name, FAIL, "scrape produced no competitor properties to track")
            response = self.client.post(
                f"{self.base_url}/api/v1/tracked/competitors",
                json={
                    "owned_property_id": self.owned_property_id,
                    "room_type_category": self.room_type_category,
                    "competitors": competitors,
                },
            )
            response.raise_for_status()
            saved = response.json().get("saved_count", 0)
            return CheckResult(name, PASS, f"tracking {len(competitors)} properties (saved {saved})")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))

    # -- step 4 ---------------------------------------------------------
    def check_price_history(self) -> CheckResult:
        name = "price history endpoint"
        if not self.first_job_id:
            return CheckResult(name, SKIP, "no completed scrape")
        try:
            params = {
                "destination": self.canonical_destination,
                "check_in": self.args.check_in,
                "check_out": self.args.check_out,
                "adults": 2,
                "children": 0,
                "rooms": 1,
                "room_type_category": self.room_type_category,
                "owned_property_id": self.owned_property_id,
            }
            response = self.client.get(
                f"{self.base_url}/api/v1/market/price-history", params=params
            )
            response.raise_for_status()
            points = response.json().get("points", [])
            if not points:
                return CheckResult(name, FAIL, "no price-history points after a completed scrape")
            return CheckResult(name, PASS, f"{len(points)} points")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))

    # -- step 5 ---------------------------------------------------------
    def nudge_first_run_prices(self) -> CheckResult:
        """Lower run #1's stored prices so run #2 deterministically alerts.

        Two live scrapes minutes apart observe the same market — 0% change,
        no alert, and the WS/alert legs of this test would be vacuous. The
        nudge rewrites history (only for this account's run #1) so the second
        scrape's unchanged real prices register as a >=30% rise.
        """
        name = "deterministic price nudge (SQL)"
        if not self.first_job_id:
            return CheckResult(name, SKIP, "no completed scrape")
        if not self.settings.database_url:
            return CheckResult(name, FAIL, "DATABASE_URL is required for the price nudge")
        try:
            import sqlalchemy

            engine = sqlalchemy.create_engine(self.settings.database_url, pool_pre_ping=True)
            with engine.begin() as connection:
                updated = connection.execute(
                    sqlalchemy.text(
                        """
                        UPDATE roomrate_room_packages rp
                        SET price_per_night_eur = round(
                            rp.price_per_night_eur * :factor, 2
                        )
                        FROM roomrate_rate_observations ro
                        JOIN roomrate_scrape_runs sr ON sr.id = ro.scrape_run_id
                        WHERE rp.rate_observation_id = ro.id
                          AND sr.scrape_job_id = :job_id
                          AND sr.account_id = :account_id
                          AND rp.price_per_night_eur IS NOT NULL
                        """
                    ),
                    {
                        "factor": self.args.nudge_factor,
                        "job_id": self.first_job_id,
                        "account_id": self.account_id,
                    },
                ).rowcount
            engine.dispose()
            if not updated:
                return CheckResult(name, FAIL, "nudge matched no stored prices for run #1")
            return CheckResult(name, PASS, f"lowered {updated} package prices by x{self.args.nudge_factor}")
        except Exception as exc:  # noqa: BLE001
            return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))

    # -- step 6 ---------------------------------------------------------
    def run_second_scrape_with_ws(self) -> CheckResult:
        name = "scrape job #2 + live WS alert"
        if not self.first_job_id:
            return CheckResult(name, SKIP, "no completed first scrape")
        try:
            frame = asyncio.run(self._scrape_with_ws_listener())
            title = ""
            try:
                title = json.loads(frame)["notification"].get("title", "")
            except Exception:  # noqa: BLE001 — the frame already matched
                pass
            return CheckResult(name, PASS, _one_line(f"live price_change frame received: {title}"))
        except Exception as exc:  # noqa: BLE001
            return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))

    async def _scrape_with_ws_listener(self) -> str:
        """Connect /ws/alerts via one-time ticket, run job #2, await the frame."""
        import websockets

        ticket_response = await asyncio.to_thread(
            self.client.post, f"{self.base_url}/api/v1/notifications/ws-ticket"
        )
        ticket_response.raise_for_status()
        ticket = ticket_response.json()["ticket"]

        async with websockets.connect(ws_alerts_url(self.base_url, ticket)) as socket:
            greeting = json.loads(await asyncio.wait_for(socket.recv(), timeout=15))
            if greeting.get("type") != "connected":
                raise RuntimeError(f"unexpected WS greeting: {greeting}")

            job = await asyncio.to_thread(self._create_scrape_job)
            completed_at: float | None = None
            poll_task = asyncio.create_task(
                asyncio.to_thread(self._poll_job, job["id"], self.args.timeout_minutes * 60)
            )
            try:
                while True:
                    if poll_task.done():
                        # Raises here if the job failed/timed out.
                        poll_task.result()
                        if completed_at is None:
                            completed_at = time.monotonic()
                        if time.monotonic() - completed_at > ALERT_FRAME_TIMEOUT_SECONDS:
                            raise TimeoutError(
                                "scrape #2 completed but no price_change WS frame arrived "
                                f"within {ALERT_FRAME_TIMEOUT_SECONDS:.0f}s"
                            )
                    try:
                        raw = await asyncio.wait_for(socket.recv(), timeout=5)
                    except asyncio.TimeoutError:
                        continue
                    if is_price_change_frame(str(raw)):
                        return str(raw)
            finally:
                if not poll_task.done():
                    poll_task.cancel()

    # -- step 7 ---------------------------------------------------------
    def check_notifications_rest(self) -> CheckResult:
        name = "notification in REST feed"
        try:
            response = self.client.get(
                f"{self.base_url}/api/v1/notifications", params={"limit": 20}
            )
            response.raise_for_status()
            rows = response.json()
            price_changes = [row for row in rows if row.get("notification_type") == "price_change"]
            if not price_changes:
                return CheckResult(name, FAIL, "no price_change notification in the feed")
            return CheckResult(name, PASS, _one_line(price_changes[0].get("title", "")))
        except Exception as exc:  # noqa: BLE001
            return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))

    # -- step 8 ---------------------------------------------------------
    def check_price_recommendation(self) -> CheckResult:
        name = "pricing recommendation"
        if not self.owned_property_id:
            return CheckResult(name, SKIP, "no account context")
        try:
            response = self.client.post(
                f"{self.base_url}/api/v1/agents/price-recommendation",
                json={
                    "owned_property_id": self.owned_property_id,
                    "room_type_category": self.room_type_category,
                    "check_in": self.args.check_in,
                    "check_out": self.args.check_out,
                    "adults": 2,
                    "children": 0,
                    "rooms": 1,
                },
            )
            response.raise_for_status()
            body = response.json()
            problems = recommendation_problems(body)
            if problems:
                return CheckResult(name, FAIL, _one_line("; ".join(problems)))
            recommendation = body["recommendation"]
            return CheckResult(
                name,
                PASS,
                f"EUR {recommendation['recommended_price_eur']} "
                f"[{recommendation['price_range_low_eur']}-{recommendation['price_range_high_eur']}] "
                f"{recommendation['confidence']} ({recommendation['source']})",
            )
        except Exception as exc:  # noqa: BLE001
            return CheckResult(name, FAIL, _one_line(f"{type(exc).__name__}: {exc}"))


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("--api-base-url", default=None, help="default: http://127.0.0.1:8000")
    parser.add_argument("--account-id", default=None, help="default: ROOMRATE_DEFAULT_ACCOUNT_ID")
    parser.add_argument("--room-type-category", default=None, help="default: account's baseline room")
    default_check_in, default_check_out = default_market_dates(date.today())
    parser.add_argument("--check-in", default=default_check_in)
    parser.add_argument("--check-out", default=default_check_out)
    parser.add_argument("--limit", type=int, default=5, help="competitor result limit per scrape")
    parser.add_argument("--nudge-factor", type=float, default=0.7)
    parser.add_argument("--timeout-minutes", type=float, default=20.0)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    run = StagingRun(args)
    results: list[CheckResult] = []
    print(f"RoomRate staging E2E against {run.base_url}")
    print(f"Market: {args.check_in} -> {args.check_out}, limit {args.limit}\n")

    steps = [
        run.check_ready_and_context,
        run.run_first_scrape,
        run.save_tracked_competitors,
        run.check_price_history,
        run.nudge_first_run_prices,
        run.run_second_scrape_with_ws,
        run.check_notifications_rest,
        run.check_price_recommendation,
    ]
    for step in steps:
        result = step()
        results.append(result)
        print(f"[{result.status}] {result.name}: {result.detail}")

    print("\n" + format_results_table(results))
    return summarize_exit_code(results)


if __name__ == "__main__":
    sys.exit(main())
