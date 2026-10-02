# RoomRate Angular frontend

This folder is an Angular app. Do not add Next.js, Clerk, or server-side proxy routes here.

Frontend browser code must call FastAPI directly with `Authorization: Bearer <Supabase JWT>`.
Never put `INTERNAL_API_KEY`, `ROOMRATE_API_KEY`, database URLs, or other backend secrets in Angular code.

The map page is `src/app/pages/map-page.component.ts` (state, load chains, results column);
its parts live in `src/app/pages/map/`: the Mapbox GL JS map (`competitor-map.component.ts`),
the filters sidebar, the competitor and match cards, the shared formatting helpers and the
page's message constants. The map's markers are imperative: the page calls `renderMarkers()`
when the shown set changes, and the map reads the page state through `CompetitorMapSource`.

Any component/service state written after an `await` or inside a WebSocket/timer
callback MUST be an Angular signal, never a plain field. supabase-js getSession()
acquires a `navigator.locks` lock that zone.js cannot patch, so every API-call
continuation runs OUTSIDE the Angular zone; plain-field writes there never
trigger change detection (data only appeared after an unrelated click). Signal
writes schedule change detection regardless of zone. `frontend/e2e/` (npm run
e2e) pins this behavior — keep it green.

The `/ws/alerts` socket authenticates with a one-time ticket from
`POST /api/v1/notifications/ws-ticket` — never put the Supabase JWT in a
WebSocket URL.
