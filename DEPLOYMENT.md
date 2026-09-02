# Deployment

**Console (live):** https://samvedna-tawny.vercel.app
Vercel project `samvedna`, production target. Public, no password.

Until a backend is reachable it shows a red banner saying so and computes
nothing — which is the correct thing for it to say, not a bug.

The console is served from Vercel. **The backend is not**, and the reason is
worth stating rather than discovering:

- **openSMILE** is a native library. It cannot be installed in Vercel's Python
  runtime, so all prosody analysis would be lost.
- **faster-whisper** is CTranslate2 plus several hundred megabytes of weights,
  well past the serverless bundle limit.
- **The console feed is a long-lived WebSocket.** Vercel functions are
  request-scoped and cannot hold one open.
- **The audit ledger needs a persistent database.** Its `UNIQUE(seq)`
  constraint is what stops the hash chain forking under concurrent writes. On an
  ephemeral filesystem the ledger resets between invocations, which voids the
  one property it exists to provide — silently, which is the worst way for an
  accountability record to fail.

So: **console on Vercel, backend in a container.**

```
  Browser ──▶ Vercel (React console, password-protected)
                 │  VITE_API_BASE
                 ▼
             Render / Railway / Fly  (FastAPI + openSMILE, Docker)
                 │
                 ▼
             PostgreSQL  (case record + audit chain)
```

---

## 0. The database

Nothing about the system requires the database to be on your machine, and for a
demo it should not be: a judge who reloads the console must see the same case
record you were just looking at, and SQLite in a container filesystem does not
survive a redeploy.

### Getting a hosted Postgres

Any managed provider works. Neon is the shortest path — free tier, no card:

1. Sign up at https://neon.tech and create a project (region: Singapore or
   Mumbai, whichever is offered).
2. Copy the connection string from the dashboard. It looks like
   `postgres://user:password@ep-xxx.ap-southeast-1.aws.neon.tech/neondb`.
3. Set it and run:

```bash
export SAMVEDNA_DATABASE_URL='postgres://…the string you copied…'
python3 -m scripts.run_api          # tables are created on first start
python3 scripts/verify_audit.py     # confirms the chain on the cloud database
```

Supabase, Railway, Render and Amazon RDS work identically — paste the string
they give you, unchanged.

### Why you can paste the string unchanged

Providers hand out `postgres://` URLs. SQLAlchemy 2.0 reads that scheme as the
long-removed psycopg2 driver and fails with `ModuleNotFoundError: No module
named 'psycopg2'`, which tells you nothing about what is wrong.
`normalise_database_url()` rewrites the scheme to `postgresql+psycopg` and adds
`sslmode=require` when the host belongs to a known managed provider — while
never downgrading an `sslmode` you set yourself, and leaving localhost and
SQLite alone. Connection strings are redacted (password *and* query string)
before they reach any log line. See D57.

### One thing to know about running it hosted

The audit chain is hash-linked, so two concurrent appenders must not read the
same head. On SQLite the single-writer lock made that impossible by accident;
on real Postgres it is not, and the concurrency test failed the first time it
ran against one. The chain now takes a transaction-scoped advisory lock before
reading the head. Nothing to configure — but it is the reason the ledger holds
under load rather than dropping events. See D58.

### Keeping SQLite

Leave `SAMVEDNA_DATABASE_URL` unset. It defaults to `sqlite:///samvedna.db`,
which is right for tests and for working offline, and wrong for anything a
second person will look at.

---

## 1. Backend

### Locally, in the deployment shape

```bash
docker compose up --build      # Postgres, not SQLite
curl localhost:8000/health
```

Worth doing once before deploying: it exercises the audit chain against
Postgres, where the `UNIQUE(seq)` concurrency control actually matters, rather
than against SQLite's single-writer behaviour.

### On Render

1. https://dashboard.render.com/blueprints → **New Blueprint Instance**
2. Point it at `github.com/prajyot2003/samvedna`. `render.yaml` provisions the
   web service and a Postgres instance.
3. **Apply.** First build takes several minutes — the image installs
   libsndfile, ffmpeg and the Python stack.
4. Note the service URL, e.g. `https://samvedna-api.onrender.com`.

Railway and Fly work equally well; the `Dockerfile` is the contract, and
`render.yaml` only encodes what the blueprint would otherwise ask for.

### Environment

| Variable | Purpose |
|---|---|
| `SAMVEDNA_DATABASE_URL` | Postgres DSN. Set from the database by the blueprint |
| `SAMVEDNA_ALLOWED_ORIGINS` | The Vercel console URL. **Until set, browsers are refused** — the default is localhost only |
| `SAMVEDNA_DEMO_BANNER` | `1` on any instance not cleared for live calls, which today is all of them |
| `SAMVEDNA_BHASHINI_*` | ULCA credentials. Absent, the router reports no ASR backend |

Whisper weights are deliberately **not** baked into the image and **not**
fetched at boot: several hundred megabytes for a demo that has no validation
recordings to run against. Without them the pipeline runs text-first. Uploaded
audio still goes through VAD, the quality gate and openSMILE, so the abstention
behaviour — the fairness argument — remains demonstrable.

---

## 2. Console

The Vercel project builds from `web/`.

| Setting | Value |
|---|---|
| Root directory | `web` |
| Framework | Vite (auto-detected) |
| `VITE_API_BASE` | the backend URL from step 1 |
| `VITE_OPERATOR_ID` | any identifier; the demo has no gateway in front of it |

`VITE_*` variables are inlined at build time, so **changing them requires a
redeploy**, not just a save.

### Access

The demonstration instance is **open** — no password, no allowlist — so anyone
given the link can try it without a credential exchange.

What carries the weight instead is the banner. It is not dismissible, it names
the two lexicon blockers, and it states that no accuracy has been measured. The
readiness endpoint says the same thing to anyone who queries it. Someone who
opens this link cannot come away thinking it is a live helpline.

If a future instance needs closing — a pilot, or a link shared beyond the
people you intended — the control exists and lives at the API rather than the
CDN, which is the right layer: a CDN password protects the HTML, not the
endpoint that scores disclosures.

```
SAMVEDNA_ALLOWED_OPERATORS=judge-01,judge-02,mentor-01
```

Build the console with `VITE_OPERATOR_ID` set to one of them. Any other
identity, and any request without one, gets a byte-identical 401 — a rejected
caller learns nothing about which identities exist, because attribution in the
audit ledger is the thing being protected. Unset, as it is now, any non-empty
identity is accepted.

---

## 2a. The laptop path — a real URL without deploying a backend

For a demo where the machine is in the room, the deployed console can talk to a
backend running on the laptop. No hosting bill, no cold starts, and the audio
path works because openSMILE is installed locally.

```bash
SAMVEDNA_ALLOWED_ORIGINS=https://samvedna-tawny.vercel.app \
SAMVEDNA_DEMO_BANNER=1 \
make dev
```

Then open the Vercel URL. The console's default `VITE_API_BASE` is
`http://127.0.0.1:8000`, so it will reach the local service.

Two caveats worth knowing before you rely on it in front of judges. Chrome and
Firefox treat `http://localhost` as trustworthy and allow the call from an HTTPS
page; **Safari blocks it**, so demo in Chrome. And it only works on the machine
running the backend — the URL is public, but for anyone else it shows the
no-backend banner. For a link judges can use on their own devices, deploy the
backend as in step 1.

## 3. Order of operations

The two halves reference each other, so:

1. Deploy the backend. Note its URL.
2. Deploy the console with `VITE_API_BASE` set to that URL.
3. Set `SAMVEDNA_ALLOWED_ORIGINS` on the backend to the Vercel URL. Redeploy the
   backend.
4. Turn on password protection.
5. Open the console. The demo banner should be visible, and
   `GET /readiness` should still report **not production ready** with both
   lexicon blockers named.

If step 5 shows a green readiness verdict, something is wrong: no build should
report ready until the lexicons are signed off.

---

## 4. What a demo instance is for, and is not

It is for showing that the system works: crisis language forcing Critical with
the model bypassed, the statutory action packet with owners and deadlines, the
SVI moving with its contribution panel, and the audit chain detecting a tampered
row.

It is **not** a helpline. It has no authentication, no clinical sign-off, no
measured accuracy, and no reviewed crisis lexicons. The banner says so on every
screen, and that honesty is the strongest claim the project has — a deployment
that quietly implied otherwise would spend it.
