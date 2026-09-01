# Deployment

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

### Close the instance

Vercel's password protection is a Pro feature; on Hobby, Vercel Authentication
admits only the account owner, which is no use for handing a link to judges.

The access control therefore lives at the API, which is the right place for it
anyway — a CDN password protects the HTML, not the endpoint that scores
disclosures. Set on the backend:

```
SAMVEDNA_ALLOWED_OPERATORS=judge-01,judge-02,mentor-01
```

and build the console with `VITE_OPERATOR_ID` set to one of them. Any other
identity, and any request without one, gets an identical 401 — a rejected
caller learns nothing about which identities exist, because attribution in the
audit ledger is the thing being protected.

This is not a substitute for the ministry's gateway and is not claimed to be. It
exists so that a demonstration instance with nothing in front of it is not an
open triage endpoint. A publicly usable triage interface for atrocity victims,
with no clinical sign-off and unreviewed crisis lexicons, would contradict the
README, the readiness endpoint and the DPIA simultaneously.

---

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
