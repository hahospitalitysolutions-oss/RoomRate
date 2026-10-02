# RoomRate frontend — static Angular bundle behind nginx.
#
# Two stages: Node 20 compiles the app, nginx serves the result. Nothing from
# the build stage (node_modules, sources, the Node runtime itself) reaches the
# final image, which contains only static files and nginx.
#
# BUILD-TIME vs RUNTIME CONFIGURATION — the thing to understand before
# changing this file:
#
#   The Angular app does NOT read environment variables, and no API URL is
#   compiled into the bundle. index.html loads /runtime-config.js, which sets
#   window.__ROOMRATE_CONFIG__; src/environments/environment.ts reads that
#   object at startup for apiBaseUrl, supabaseUrl, supabaseAnonKey and
#   mapboxToken. (The NG_APP_* names still mentioned in some older notes are
#   dead — nothing in the codebase reads them.)
#
#   Consequence: this image has NO build arguments for configuration and the
#   same image tag is promoted from staging to production unchanged. Point it
#   at an environment with the ROOMRATE_* variables below, which nginx.conf
#   turns into the /runtime-config.js response and into the CSP's connect-src.
#
#   src/environments/environment.development.ts is only used by `ng serve`;
#   `npm run build` defaults to the production configuration, which keeps
#   environment.ts (the runtime-config reader). No local dev values are baked
#   into this image.

# ---------------------------------------------------------------------------
# Stage 1 — build
# ---------------------------------------------------------------------------
FROM node:20-alpine AS build

WORKDIR /app

# Copy the manifests alone first so `npm ci` is re-run only when dependencies
# actually change, not on every source edit.
COPY package.json package-lock.json ./

# `npm ci` installs devDependencies too — @angular/build and @angular/cli are
# the compiler, not optional extras. `--no-audit --no-fund` keeps CI output
# readable and avoids network calls that are irrelevant to a build.
RUN npm ci --no-audit --no-fund

COPY . .

# Defaults to the production configuration (angular.json:
# defaultConfiguration: "production") — optimization on, outputHashing "all",
# source maps off.
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2 — serve
# ---------------------------------------------------------------------------
# nginx-unprivileged: identical to the official nginx image but running as uid
# 101 and listening on 8080, so the container needs no root and no added
# capabilities. Pinned to a minor version; bump deliberately.
FROM nginxinc/nginx-unprivileged:1.27-alpine

# The @angular/build:application builder emits the browser bundle into a
# `browser/` subdirectory — dist/<project>/browser, with the project name from
# angular.json ("roomrate-frontend"). The sibling files it also writes
# (3rdpartylicenses.txt, prerendered-routes.json) stay behind on purpose:
# only browser/ is meant to be public.
COPY --from=build /app/dist/roomrate-frontend/browser/ /usr/share/nginx/html/

# Installed as a template, not as a finished config: the stock entrypoint
# (/docker-entrypoint.d/20-envsubst-on-templates.sh) runs envsubst over
# everything in /etc/nginx/templates and writes the result to
# /etc/nginx/conf.d, which is what makes the ROOMRATE_* placeholders inside
# nginx.conf resolve at container start instead of at build time.
#
# Named default.conf.template on purpose: the entrypoint writes it to
# /etc/nginx/conf.d/default.conf, overwriting the stock site the base image
# ships, so there is no second server block competing for port 8080. No `RUN
# rm` is needed for that, and attempting one would depend on build-time file
# ownership that differs between registry rebuilds.
COPY nginx.conf /etc/nginx/templates/default.conf.template

# Restrict envsubst to the ROOMRATE_ prefix. Without this it would consider
# every exported variable, and an unrelated variable called e.g. `host` would
# quietly eat nginx's own $host.
ENV NGINX_ENVSUBST_FILTER="^ROOMRATE_"

# Every placeholder used in nginx.conf needs a default here. envsubst leaves
# an undefined ${NAME} untouched, and nginx would then refuse to start with
# "unknown variable". Empty is a valid, honest default: the app boots, tells
# the user its configuration is missing, and nothing fake is baked in.
#
# ROOMRATE_CSP_API_ORIGINS is the CSP allow-list for the API, and needs BOTH
# schemes when the API is cross-origin, because the alerts socket reuses the
# same base URL with http swapped for ws:
#     ROOMRATE_CSP_API_ORIGINS="https://api.example.gr wss://api.example.gr"
# Always list the explicit origins, including wss://, even when the API is
# reverse-proxied onto this same origin: relying on 'self' covering wss: is a
# CSP Level 3 detail, and stating the origin costs nothing.
ENV ROOMRATE_API_BASE_URL="" \
    ROOMRATE_SUPABASE_URL="" \
    ROOMRATE_SUPABASE_ANON_KEY="" \
    ROOMRATE_MAPBOX_TOKEN="" \
    ROOMRATE_CSP_API_ORIGINS=""

EXPOSE 8080

# busybox wget, present in the alpine base. `-q -O -` keeps it silent and
# writes nothing to disk; a non-2xx status makes wget exit non-zero.
HEALTHCHECK --interval=30s --timeout=3s --start-period=5s --retries=3 \
    CMD wget -q -O - http://127.0.0.1:8080/ > /dev/null || exit 1

# Inherited from the base image: USER 101, plus the entrypoint that expands the
# templates before starting nginx. Repeating CMD/ENTRYPOINT here would only
# risk skipping that expansion.
