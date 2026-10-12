# RoomRate Angular Frontend

This frontend is an Angular application that talks directly to the FastAPI backend.

## Local Development

```bash
npm install
npm run dev
```

Open:

```text
http://127.0.0.1:4200
```

## Required Configuration

Set browser-safe values in `src/environments/environment.ts` for local development:

```ts
export const environment = {
  apiBaseUrl: "http://127.0.0.1:8000",
  neonAuthUrl: "https://ep-YOUR-ENDPOINT.neonauth.REGION.aws.neon.tech/neondb/auth",
  mapboxToken: "YOUR_MAPBOX_PUBLIC_TOKEN",
};
```

Do not put `INTERNAL_API_KEY` or any backend secret in Angular code.
