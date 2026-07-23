# WorldEval Lab deployment

WorldEval Lab has two deliberately separate browser surfaces:

- The authenticated Team Lab at `/`, which can configure and launch a run.
- Unlisted, read-only game guides at `/share/games/<game-id>`, which can show
  only server-published guide metadata, published replays, and verified
  benchmark results.

The game authority, provider adapters, checkpoints, and scoring remain on the
backend. The browser is never authoritative and does not receive a provider
key after a launch request.

## Local development

Use [`Open WorldEval Lab.command`](../Open%20WorldEval%20Lab.command) from the
repository root. It starts a loopback-only API on port `8000`, a Vite Lab on
port `5173`, and opens the latter in the default browser. Runtime logs and PID
files stay in the ignored `.worldeval-runtime/` directory.

The default local authentication mode is intentionally development-only. It
uses an in-memory session and permits only loopback origins. Press **Connect
local session** in the right-hand Run Composer before launching a race.
The launcher starts Uvicorn with access logging disabled so even a local
magic-link callback query cannot be copied into its runtime log.

Do not put a provider key in the launcher, a command-line argument, browser
storage, a checked-in environment file, or a public deployment configuration.
For a local race, the operator pastes the key into the password field. The
browser submits it only with that launch request, then clears the field; the
backend holds it only in the live provider session and closes it when the race
ends.

## Team deployment

The checked-in Nginx example at
[`deploy/nginx/worldeval-lab.conf`](../deploy/nginx/worldeval-lab.conf) is
HTTPS-first and proxies the API over loopback. It disables Nginx access logging
for `/api/auth/callback`, because a one-use magic-link token is present in that
query string before the application consumes it and redirects to `/`.

Set these server-only values through the service manager or secret manager,
not in the dashboard bundle:

```text
GENESIS_AUTH_MODE=magic_link
GENESIS_AUTH_ENVIRONMENT=production
GENESIS_HOST=127.0.0.1
GENESIS_AUTH_PUBLIC_ORIGIN=https://lab.example.com
GENESIS_AUTH_DATABASE_PATH=/var/lib/worldeval/lab-auth.sqlite3
GENESIS_AUTH_ALLOWED_EMAILS=operator-one@example.com,operator-two@example.com
GENESIS_AUTH_TOKEN_PEPPER=<at-least-32-random-bytes>
```

`magic_link` mode deliberately fails closed unless all of the following are
true: the public origin is HTTPS, the allowlist is non-empty, the SQLite path
is absolute and persistent, and a production transactional-email sender has
been constructed at application startup. The repository currently provides
the sender boundary and an in-memory test sender only; it does **not** select
or configure an email provider. Connecting a verified-domain transactional
sender is therefore the remaining deployment action before enabling
`magic_link` in production.

When it is configured, the same Composer switches from the local-session
button to an email field and **Send access link** action. Its response is
generic for invited, uninvited, and temporarily undeliverable addresses, so
the browser cannot use it to enumerate team membership.

Do not add a permissive CORS policy or broad extra origins. Authenticated Lab
mutations require the session cookie, the session-bound
`X-WorldEval-CSRF` token, and an exact configured `Origin`.

## Publishing and replay safety

Only `/api/public/games/*` is intentionally anonymous. It sends `noindex,
nofollow` and exposes no active controls, private runs, provider material,
prompts, raw model responses, scratchpads, private observations, or navigation
memory. The Team Lab API lives under `/api/lab/*` and requires an authenticated
operator for every read; state changes also require CSRF protection.

The current browser arena uses the generated Godot broadcast MP4 as its
authoritative visual fallback. It can play, pause, seek to safe timeline
markers, and show the published observer roster. A future Godot Web export may
replace that visual client, but must consume the same safe replay projection
and never become gameplay authority.
