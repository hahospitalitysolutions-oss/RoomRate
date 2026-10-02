# Live-Fix Round: «Δείξε άλλα» and the selection race

> **For agentic workers:** execute with superpowers:subagent-driven-development
> (implementer per task → spec review with a mutation kill table → quality
> review). Findings fold back into this file.

**Goal:** the two functional bugs the 2026-08-11 live tour proved — the
wizard's correction flow cannot search live, and early competitor clicks are
silently wiped — are fixed, pinned, and re-verified against the live app.

**Scope decision (user, 2026-08-11):** this round contains exactly these two
fixes. The sparkline redesign and agent polish are a later round; the backend
room auto-select removal is chip task_99488b2f. This round MAY touch the
backend (the old wizard plan's no-backend rule does not apply here).

**Baselines before starting:** frontend e2e **91 passed + 1 skipped**
(`npx playwright test` from `frontend/`), backend **565 passed**
(`python -m pytest api/tests -q --basetemp=<scratchpad>/pytest-tmp`),
build clean. Branch: `feature/onboarding-setup-wizard` (head `8772aa3`).

## Execution status (read this first when resuming)

| Task | State | Key commits |
|---|---|---|
| A «Δείξε άλλα» live fix | done; spec review PASS (M1-M4 killed; frontend timeout half consciously unpinned — its pin is Task C live). **Execution finding that amends Task C:** the scout cache is INERT for property-candidates at any setting — the provider calls `fetch_hotel_list(engine=None)` and `scraper/scout.py` gates BOTH cache read (:181-184) and write (:233-234) on a live engine. Verified independently by the reviewer (by reading; unpinned by test). So the live symptom is fixed TODAY only by the 180s GET budget; the cache knob is correct forward-looking plumbing. Follow-up (own chip at close): thread a real engine through the candidates scout | `7228490` + close-out |
| B selection race guard | done + quality closed. All 8 `selectedKeys` sites classified (3 user-level with flag arm/disarm, 5 chain-guarded); topologically sound (both `loadMarkers` entries are preceded by a same-run disarm). **Execution findings:** (1) same-job clicks self-heal via the persist→restore round-trip, so the shipped scenario clears the just-written `lastCompetitorSelection` key to exercise the `applyTrackedCompetitors` guard — the `restoreStoredCompetitorSelection` guard is defense-in-depth (cross-tab/corrupt-value), consciously unpinned; (2) a DOM-text settle proxy was proven unsound (`toBeChecked` locks on first poll) — the wait is `waitForResponse` + a ready-text barrier; (3) M5 residual: dropping `findCompetitors`' disarm would only cost pre-ticked tracked competitors after a new search (pin not worth ~40 mock lines); M3/M6 provably equivalent. Close-out adds the error-path pin (tracked 500 → click survives — the one reachable unpinned guard) and the deterministic barrier. `activeScrapeJobId` signals-rule smell → chip task_a934b51e. **Round quality review (whole range): Ready to merge, zero Critical/Important.** Its one Minor (the timed race window could silently lose kill power) proved prophetic: the timed window DID slip under parallel dev-server contention, and a header-text in-window pin proved render-order-sensitive — final shape is a CONDITION-HELD window (the route holds the tracked response until the test releases it after the click) with a DOM-free settled-flag proof; 16/16 under a doubled parallel run | `00cad21`, `f4cb702`, `6763c64` |
| C live re-verify + close | **done (2026-08-11).** Live change-flow refine search («Rea Hotel»/Faliraki, account +smoke0811): **candidates rendered in 72s — 12 real results** (first: Avi Suites Faliraki 9.4), no fallback-timeout; before the round this call died at 45s on every attempt. Baselines at close: frontend **93 passed + 1 skipped**, backend **570 passed** (565 + Task A's 5), build/tsc clean. Chips: task_45ec2117 dismissed (fixed by B); task_79d4ce48 predates an app restart and could not be withdrawn programmatically — treat as superseded; narrowed follow-up spawned as **task_02b7f086** (engine threading to activate the cache; would turn the 72s cold search into seconds-warm) | — |

---

### Task A: «Δείξε άλλα» works against the live backend

**Diagnosis (live-proven):** `api/services/onboarding_service.py:172` invokes
the scout pipeline with `scout_cache_hours=0`, so EVERY
`GET /api/v1/onboarding/property-candidates` re-scrapes Booking live (1-4
min) and auto-setup's identical search is never reused; meanwhile
`frontend/src/app/services/api-client.service.ts:19` aborts every GET at
45 s. Three user-style retries over ~7 minutes all fell back. Auto-setup
succeeds only because POSTs get 180 s.

**Files:**
- Modify: `api/services/onboarding_service.py` (cache hours from settings)
- Modify: `api/config.py` or wherever settings live (new
  `PROPERTY_CANDIDATES_CACHE_HOURS`, default 24; document in `.env.example`)
- Modify: `api/tests/test_onboarding_routes.py` or the service's test file
  (pin the pass-through)
- Modify: `frontend/src/app/services/api-client.service.ts`
  (`get()` gains an optional `timeoutMs` parameter, default 45_000)
- Modify: `frontend/src/app/services/onboarding.service.ts`
  (`propertyCandidates` passes 180_000)

**Steps:**
- [ ] Backend TDD: failing test first — the service forwards the configured
  cache hours to the pipeline (record-the-call fake per house rules; also one
  test that the default is 24 and that `0` still means "no cache" so the old
  behavior stays reachable by config).
- [ ] Implement: read the setting, pass it instead of the literal `0`.
  Confirm (by reading the pipeline) what the scout cache keys on — name,
  location, dates, limit — and record in the code comment whether
  auto-setup's search warms the wizard's query (same key) or not (adjacent
  key); the fix is valuable either way (retry after abort hits the cache).
- [ ] `.env.example`: document the new variable where the other scrape knobs
  live.
- [ ] Frontend: optional `timeoutMs` on `ApiClientService.get` (last
  parameter, default keeps 45_000 — every other call site unchanged);
  `OnboardingService.propertyCandidates` passes `180_000` with a comment
  naming the live finding. No e2e can pin a timeout without a 45 s mock —
  the pin for this half is Task C's live re-verification; say so in the
  commit message.
- [ ] Full backend suite (expect 565 + new tests), full frontend suite
  (expect 91+1), build. Commit.

### Task B: user selections survive the map's load chain

**Diagnosis (live-proven):** clicking a competitor card while the map's
async load/restore chain is still settling ends with the selection wiped:
`map-page.component.ts` clears `selectedKeys` mid-chain (~:824/:829) and the
stored-selection restore (~:978, ~:1089) overwrites whatever is there. Same
click after settling sticks (marker renders, tracked button enables).

**Files:**
- Modify: `frontend/src/app/pages/map-page.component.ts`
- Modify: `frontend/e2e/no-second-click.spec.ts` (new scenario)

**Steps:**
- [ ] TDD: failing scenario first, in `no-second-click.spec.ts` — mock the
  LATE chain steps (`tracked/competitors**`) with ~2 s delayed fulfillments
  (wildcard-first LIFO as always), load `/map`, click the first competitor
  card DURING the delay, then after the chain settles assert the card is
  still checked and `Add selected to tracked` is enabled. (AMENDED: no
  `.mapboxgl-marker` assertion — `mockBackend` aborts all Mapbox traffic so
  GL never initializes under mocks; nothing in the suite asserts markers.)
  Red phase: today the late chain wipes it.
- [ ] Implement: a `userTouchedSelection` signal set in `toggleCompetitor`;
  the load-chain's `selectedKeys` clears and the stored-selection restore
  apply BOTH become conditional on `!userTouchedSelection()`. The flag
  resets where a fresh search legitimately resets selection
  (`findCompetitors` — AMENDED: the method's real name; the plan originally
  said `runSearch`) and on the session-level reset — enumerate every
  `selectedKeys.set` site and classify it (user-initiated keeps working,
  chain-initiated gets the guard); the classification table goes in the
  implementation report.
- [ ] Signals rule applies (writes after await). Full suite + build. Commit.

### Task C: live re-verification and closure

- [ ] With `api` + frontend running: drive the CHANGE flow live
  (`/settings` → «Αλλαγή καταλύματος» → step 1 pick mode → refine search) —
  the same `property-candidates` GET «Δείξε άλλα» uses. Acceptance: the
  candidate list renders live (warm cache: seconds; cold: within the new
  180 s window) with NO fallback-timeout error. Use a Playwright driver spec
  (temporary, deleted after) with the `+smoke0811` account.
- [ ] Rerun full frontend + backend suites one final time; update this
  file's status table; dismiss chips task_79d4ce48 and task_45ec2117 as
  superseded-by-fix.

## Standing rules

The wizard plan's standing rules apply verbatim (recording mocks,
non-default test values, signals rule, no emoji, wildcard-first LIFO,
`seedBrowserState` unscoped-seed trap). Additions from execution live here.
