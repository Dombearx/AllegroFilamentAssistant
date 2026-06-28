# Allegro Filament Assistant — Architecture & Plan

## 1. Goal

Find 3D-printing filaments listed on **Allegro** that match a **target color**.

The user:
1. Picks one or more **brands** and **filament types** (PLA, PETG, ABS, …).
2. Provides a **target color as hex** (e.g. `#1E88E5`).

The app then:
1. Queries the Allegro API for matching offers.
2. Downloads each offer's photos.
3. Estimates the **filament color** from each photo — robust to photos that show a
   **spool** *or* a **printed figure**.
4. Ranks offers by how close their color is to the target, and shows them in the UI
   with an extracted color swatch and a match score.

---

## 2. Design principle: frontend-agnostic backend

The core logic is exposed through a **stable HTTP/JSON API** (FastAPI). Any frontend is a
thin client over that API, so we can run **multiple interchangeable frontends**:

- **NiceGUI** app (Python) — all-in-one local/self-host option; mounted on the same
  FastAPI process.
- **Static SPA on GitHub Pages** (plain TypeScript) — pure static files that call the
  hosted API over CORS. GitHub Pages can serve *only* static assets and **cannot run the
  CV/image processing** — so it is a pure presentation client pointed at a deployed
  backend.

Both frontends speak the **same documented contract** (auto-generated OpenAPI), so adding
or swapping a frontend never touches the core. The CV/Allegro work always runs server-side.

```
   ┌───────────────┐     ┌──────────────────────────────┐
   │ NiceGUI (py)  │     │ Static SPA (GitHub Pages, TS) │   ← interchangeable frontends
   └──────┬────────┘     └──────────────┬───────────────┘
          │ in-process / HTTP           │ HTTPS + CORS
          └──────────────┬──────────────┘
                         ▼
              ┌────────────────────────┐
              │   FastAPI HTTP/JSON API │   ← stable contract (OpenAPI)
              └───────────┬────────────┘
                          ▼
              ┌────────────────────────┐
              │   Core service layer    │   ← orchestrator + Allegro + color (no web deps)
              └────────────────────────┘
```

---

## 3. Tech stack

| Concern            | Choice                          | Why |
|--------------------|---------------------------------|-----|
| Language (backend) | **Python 3.11+**                | Image processing lives in Python. |
| API                | **FastAPI**                     | Stable JSON contract, async, auto OpenAPI/docs, CORS. NiceGUI already runs on it. |
| UI option A        | **NiceGUI**                     | Preferred all-in-one; mounts on the same FastAPI app. |
| UI option B        | **Static TS SPA** (Vite)        | Optional GitHub Pages build; calls the API, no backend of its own. |
| HTTP client        | **httpx** (async)               | Async Allegro calls + concurrent image downloads. |
| Background removal | **rembg** (U²-Net)              | Isolates the object (spool *or* figure) from mostly-white backgrounds. No training data. |
| Color math / CV    | **OpenCV**, **NumPy**, **scikit-learn** | LAB conversion, k-means dominant-color clustering. |
| Color matching     | **colour-science** (or small in-repo CIEDE2000) | Perceptual Delta E (CIEDE2000) over naive RGB distance. |
| Caching            | **diskcache**                   | Cache tokens, offer pages, and per-image color results. |
| Config             | **pydantic-settings** + `.env`  | All secrets/config via environment variables. |
| Packaging / deps   | **uv** (or pip + `pyproject.toml`) | Fast, reproducible installs. |

> Decision: **No model fine-tuning for v1.** Segmentation + dominant-color + Delta E is
> explainable, needs zero labeled data, and handles spools and figures. A learned model is
> a possible later phase *only* if accuracy proves insufficient.

---

## 4. End-to-end data flow

```
            ┌─────────────────────────────────────────────────────────┐
            │             Frontend (NiceGUI or static SPA)             │
            │  brands[]  types[]  target_hex  threshold  → [Search]    │
            └───────────────┬─────────────────────────────────────────┘
                            │  GET /api/filters · POST /api/searches · SSE
                            ▼
            ┌─────────────────────────────────────────────────────────┐
            │                  FastAPI API layer                       │
            └───────────────┬─────────────────────────────────────────┘
                            │ calls
                            ▼
            ┌─────────────────────────────────────────────────────────┐
            │                   Search Orchestrator                    │
            │   (async; streams results back with progress)           │
            └───────┬──────────────────────────┬──────────────────────┘
                    │                           │
                    ▼                           ▼
        ┌───────────────────────┐   ┌──────────────────────────────────┐
        │   Allegro API client  │   │     Color pipeline (per image)   │
        │  • OAuth2 token       │   │  1. download (httpx)             │
        │  • category/param     │   │  2. rembg → foreground mask      │
        │    discovery          │   │  3. LAB + k-means dominant color │
        │  • /offers/listing    │   │  4. drop neutral clusters        │
        │  • paginate + cache   │   │  5. pick filament color          │
        └───────────┬───────────┘   └───────────────┬──────────────────┘
                    │ offers (with image URLs)       │ per-offer color
                    └───────────────┬────────────────┘
                                    ▼
                    ┌──────────────────────────────────┐
                    │   Matcher: CIEDE2000 vs target    │
                    │   rank ascending, apply threshold │
                    └──────────────────┬────────────────┘
                                       ▼
                       ranked results → API → frontend
```

---

## 5. HTTP API contract

Versioned under `/api`. FastAPI auto-publishes `GET /openapi.json` and Swagger UI at
`/docs`; the static SPA can codegen a typed client from it.

### Endpoints

| Method | Path                          | Purpose |
|--------|-------------------------------|---------|
| GET    | `/api/health`                 | Liveness/readiness probe. |
| GET    | `/api/filters`                | Available **brands** and **types**, discovered from Allegro category params (cached). Populates the frontend selects. |
| POST   | `/api/searches`               | Start a search job. Returns a `searchId`. |
| GET    | `/api/searches/{id}/events`   | **SSE** stream of progress + ranked results as they resolve (EventSource-compatible → works from a static SPA). |
| GET    | `/api/searches/{id}`          | Poll a snapshot (status + accumulated results) — non-SSE fallback. |

### Schemas (shape, not final)

```jsonc
// GET /api/filters
{
  "brands": [{ "id": "string", "name": "string" }],
  "types":  [{ "id": "string", "name": "string" }]
}

// POST /api/searches  (request)
{
  "brands": ["id", ...],          // optional; empty = all
  "types":  ["id", ...],          // optional; empty = all
  "targetHex": "#1E88E5",
  "deltaEThreshold": 10,          // optional; default from config
  "maxOffers": 120                // optional; default from config
}
// POST /api/searches  (response)
{ "searchId": "uuid" }

// SSE events on /api/searches/{id}/events
event: progress   data: { "processed": 42, "total": 120 }
event: result     data: {
  "offerId": "string", "title": "string", "url": "https://allegro.pl/...",
  "price": { "amount": "39.99", "currency": "PLN" },
  "thumbnailUrl": "https://...",
  "extractedColorHex": "#1B85Dd", "deltaE": 3.7, "confidence": 0.82
}
event: done       data: { "processed": 120, "matched": 18 }
```

### Why async + SSE
Searches are long (many image downloads + CV). The job model (`POST` → `searchId` →
stream/poll) keeps the API responsive and lets the UI render results **incrementally** with
a progress bar. SSE is chosen over WebSockets because it is one-directional (server→client),
trivially proxied, and natively supported by the browser `EventSource` API — ideal for a
static GitHub Pages client.

### CORS
Allowed origins (e.g. the GitHub Pages domain) come from env `CORS_ALLOW_ORIGINS`
(comma-separated). The NiceGUI frontend is same-origin and needs no CORS.

---

## 6. Project structure

The web layers (`api/`, `ui/`) are thin; all logic lives in `core/` with **no web
dependencies**, so it is reusable from the API, NiceGUI, tests, or a CLI.

```
AllegroFilamentAssistant/
├── pyproject.toml
├── .env.example
├── README.md
├── docs/
│   └── ARCHITECTURE.md            # this file
├── frontend/                      # optional static SPA (GitHub Pages); added in its phase
│   └── (Vite + TypeScript, reads API_BASE_URL, generated API client)
└── src/
    └── filament_assistant/
        ├── __init__.py
        ├── server.py              # builds FastAPI app, mounts API + (optional) NiceGUI
        ├── config.py              # pydantic-settings (env vars)
        ├── api/
        │   ├── routes.py          # /api/* endpoints (thin: validate → call core)
        │   ├── schemas.py         # pydantic request/response models
        │   └── sse.py             # SSE helpers
        ├── core/                  # NO web deps — pure services
        │   ├── allegro/
        │   │   ├── auth.py        # OAuth2 client-credentials token + refresh
        │   │   ├── client.py      # httpx wrapper, retries, rate-limit handling
        │   │   ├── categories.py  # discover filament category + brand/type params
        │   │   └── models.py      # Offer, Image, ListingPage dataclasses
        │   ├── color/
        │   │   ├── segmentation.py# rembg foreground extraction
        │   │   ├── dominant.py    # LAB k-means, neutral filtering → filament color
        │   │   ├── matching.py    # CIEDE2000 delta-E, ranking
        │   │   └── pipeline.py    # download → segment → dominant → (color, confidence)
        │   ├── search/
        │   │   └── orchestrator.py# ties Allegro + color pipeline, async + streaming
        │   └── cache.py           # diskcache wrappers (token, pages, image colors)
        └── ui/
            ├── app.py             # NiceGUI layout; calls core (in-process)
            └── components.py      # offer card, swatch, progress
```

> NiceGUI calls `core/` in-process (no HTTP hop). The static SPA calls the same logic via
> the `api/` layer. The API is the contract; `core/` is the single source of truth.

---

## 7. Allegro API integration

### Auth — OAuth2 client-credentials (public data only)
Searching **public** offers needs only an app token, no user login.

```
POST {AUTH_BASE}/auth/oauth/token
Authorization: Basic base64(client_id:client_secret)
body: grant_type=client_credentials
→ { access_token, expires_in }
```
- Token cached and refreshed ~60s before expiry.
- All API requests send `Accept: application/vnd.allegro.public.v1+json`.

### Environments (env-selected)
| Env     | AUTH_BASE                                | API_BASE                                   |
|---------|------------------------------------------|--------------------------------------------|
| prod    | `https://allegro.pl`                     | `https://api.allegro.pl`                   |
| sandbox | `https://allegro.pl.allegrosandbox.pl`   | `https://api.allegro.pl.allegrosandbox.pl` |

### Category & parameter discovery
Brand/type filters are **category parameters** whose IDs differ per category, so discover
them at runtime instead of hardcoding:
- `GET /sale/categories` (+ children) → locate the 3D-printing filament category.
- `GET /sale/categories/{id}/parameters` → find "brand/marka" and "material type" param
  IDs + allowed dictionary values (these feed `GET /api/filters`).
- Cache resolved IDs/value maps (they change rarely).

### Offer search
```
GET {API_BASE}/offers/listing
    ?category.id={filamentCategoryId}
    &parameter.{brandParamId}={valueIds...}
    &parameter.{typeParamId}={valueIds...}
    &limit=60&offset=0
```
- Paginate with `limit`/`offset` (cap total via `MAX_OFFERS`).
- Each item carries an `images[]` array → fed to the color pipeline.

### Rate limits & resilience
- Honor `429` + `Retry-After` with backoff.
- Cache listing pages by query signature (short TTL) and image colors (long TTL).

---

## 8. Color detection pipeline (per image)

Works for **both** spool photos and printed-figure photos.

1. **Download** the image (httpx, cached by URL hash).
2. **Segment foreground** with `rembg` → RGBA; keep pixels where alpha > threshold.
3. **Cluster** foreground pixels in **LAB** with k-means (k = 3–5).
4. **Filter neutral clusters** — drop near-white/gray/black (low LAB chroma): reel,
   shadows, highlights, leftover background. Exception: keep neutrals if the **target is
   itself neutral**.
5. **Pick the filament color** = most prominent remaining cluster (pixels × saturation
   weight) centroid.
6. Return `(rgb, confidence)` from foreground coverage + cluster dominance; skip images
   with no usable foreground.

**Per offer:** run over its images, then aggregate (highest-confidence image, or median of
consistent images) into one representative color.

---

## 9. Matching & ranking

- Convert representative color and target hex to LAB.
- Compute **CIEDE2000 ΔE** (perceptually uniform).
- Rank by ΔE ascending; apply a user-tunable threshold (default e.g. ΔE ≤ 10).
- ΔE intuition: `<1` imperceptible · `1–2` very close · `2–10` close · `>10` distinct.

---

## 10. Concurrency, caching & performance

- **Async orchestration**: fetch listing pages, download images concurrently (`asyncio`
  + bounded semaphore). Run CPU-bound CV (rembg/k-means) in a thread/process pool.
- **Stream results** through the SSE endpoint as offers resolve; process top-N first.
- **Caching** (diskcache): OAuth token; resolved category/param IDs; listing pages (short
  TTL); per-image color by URL hash (long TTL — the biggest win).

---

## 11. UI / UX

Same behavior across frontends (driven by the API):
- **Search panel**: multi-select brands, multi-select types, hex color picker (live swatch
  + manual entry), ΔE threshold slider, max-results input.
- **Results grid**: cards with offer thumbnail, extracted swatch beside target swatch, ΔE
  badge, price, link to the Allegro offer.
- **Progress**: live progress bar/count via SSE; graceful empty/error states.

---

## 12. Configuration (environment variables)

```
ALLEGRO_CLIENT_ID=...
ALLEGRO_CLIENT_SECRET=...
ALLEGRO_ENV=prod                 # prod | sandbox
MAX_OFFERS=120
IMAGE_CONCURRENCY=8
DELTA_E_THRESHOLD=10
CACHE_DIR=.cache
CORS_ALLOW_ORIGINS=https://<user>.github.io   # comma-separated; for the static SPA
# Static SPA build-time: API_BASE_URL=https://api.your-deployment.example
```

---

## 13. Deployment

- **Backend (API + CV + optional NiceGUI)**: one deployable service (container) with CPU
  for rembg. Hosts `/api/*`, `/docs`, and — if enabled — the NiceGUI UI on the same origin.
- **Static SPA (optional)**: `frontend/` built to static files and published to **GitHub
  Pages**. It is presentation-only, configured at build time with `API_BASE_URL` pointing
  at the deployed backend; the backend's `CORS_ALLOW_ORIGINS` must include the Pages domain.
  GitHub Pages never runs image processing.
- **Two ways to run**, same core:
  1. **All-in-one**: backend + NiceGUI together (simplest self-host).
  2. **Split**: backend API hosted anywhere + static SPA on GitHub Pages.

---

## 14. Dependencies (initial)

`fastapi`, `uvicorn`, `nicegui`, `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`,
`rembg`, `onnxruntime`, `opencv-python-headless`, `numpy`, `scikit-learn`, `colour-science`,
`diskcache`, `pillow`, `sse-starlette`. Dev: `pytest`, `ruff`. Static SPA (its phase):
`vite`, `typescript` + an OpenAPI client generator.

---

## 15. Phased roadmap

- **M1 — Allegro client**: auth, category/param discovery, listing + pagination, cached.
  CLI smoke test printing offers for given brands/types.
- **M2 — Color pipeline**: download → rembg → LAB k-means → neutral filtering →
  representative color. Test on real spool + figure images.
- **M3 — Matching**: CIEDE2000 ranking + threshold; end-to-end CLI (params → ranked offers).
- **M4 — API layer**: FastAPI `/api/filters`, `/api/searches` (+ SSE), CORS, OpenAPI. The
  contract both frontends depend on. Integration tests against mocked Allegro/core.
- **M5 — NiceGUI UI**: search form, streaming results grid, swatches, progress — over the
  core/API.
- **M6 — Static SPA (optional)**: `frontend/` Vite+TS app, generated API client, GitHub
  Pages deploy, documented `API_BASE_URL`/CORS wiring.
- **M7 — Hardening**: caching tuning, rate-limit/backoff, error states, README + Allegro
  app-registration guide.
- **M8 (optional)** — accuracy pass: small labeled set; consider a learned color model only
  if Delta E proves insufficient.

---

## 16. Risks & open questions

- **Spool reel vs filament**: reel color ≈ filament, or small filament region. Mitigation:
  neutral filtering + saturation weighting; region heuristics later if needed.
- **Multi-color / mixed photos**: several spools/props. Mitigation: confidence scoring +
  multi-image aggregation.
- **rembg model size/latency**: first run downloads U²-Net weights; CV is CPU-bound.
  Mitigation: caching, thread/process pool, bounded concurrency.
- **Allegro rate limits / schema changes**: discover params at runtime, cache, back off.
- **Display vs photo color fidelity**: lighting/white-balance shifts true color; ΔE
  threshold is tunable, perfect accuracy not guaranteed.
- **Static SPA hosting**: GitHub Pages is static-only, so a deployed backend + correct CORS
  are required; the SPA is useless without the API base URL configured.

---

## 17. Testing

- Unit: neutral-cluster filtering, CIEDE2000 vs reference pairs, Allegro query-string
  building, token refresh, API schema validation.
- Integration: color pipeline on a fixture set of real spool + figure images; API routes
  against mocked core; SSE event sequence.
- Allegro client tested against mocked HTTP responses (no live calls in CI).
```
