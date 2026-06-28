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

## 2. Shape of the app

A single **NiceGUI** application. The UI is a thin layer over a web-free **core service
layer** that does all the real work (Allegro access + color analysis). NiceGUI calls the
core **in-process** — no HTTP API, no second frontend.

Keeping the core free of UI/web dependencies is still worth it: it stays unit-testable and
usable from a CLI smoke test, and the UI never embeds business logic.

```
   ┌─────────────────────────────┐
   │        NiceGUI UI            │  search form · streaming results grid · progress
   └──────────────┬──────────────┘
                  │ in-process calls (async)
                  ▼
   ┌─────────────────────────────┐
   │      Core service layer      │  orchestrator + Allegro client + color pipeline
   └─────────────────────────────┘  (no UI/web deps)
```

Progress and incremental results flow back to the UI through an **async generator /
callback** from the orchestrator; NiceGUI updates the page reactively as offers resolve
(it manages the browser sync over its own websocket internally — nothing for us to wire).

---

## 3. Tech stack

| Concern            | Choice                          | Why |
|--------------------|---------------------------------|-----|
| Language           | **Python 3.11+**                | Image processing lives in Python; one language end-to-end. |
| UI                 | **NiceGUI**                     | Preferred; async-friendly, reactive updates, no separate JS toolchain. |
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
            │                      NiceGUI UI                          │
            │  brands[]  types[]  target_hex  threshold  → [Search]    │
            └───────────────┬─────────────────────────────────────────┘
                            │ async call
                            ▼
            ┌─────────────────────────────────────────────────────────┐
            │                   Search Orchestrator                    │
            │   (async; yields progress + results incrementally)      │
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
                          ranked results → NiceGUI grid
```

---

## 5. Project structure

The UI (`ui/`) is thin; all logic lives in `core/` with **no UI/web dependencies**, so it
is reusable from the UI, tests, or a CLI smoke test.

```
AllegroFilamentAssistant/
├── pyproject.toml
├── .env.example
├── README.md
├── docs/
│   └── ARCHITECTURE.md            # this file
└── src/
    └── filament_assistant/
        ├── __init__.py
        ├── main.py                # NiceGUI app entrypoint
        ├── config.py              # pydantic-settings (env vars)
        ├── core/                  # NO UI/web deps — pure services
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
        │   │   └── orchestrator.py# ties Allegro + color pipeline; async, yields results
        │   └── cache.py           # diskcache wrappers (token, pages, image colors)
        └── ui/
            ├── app.py             # NiceGUI layout; calls core (in-process)
            └── components.py      # offer card, swatch, progress
```

---

## 6. Allegro API integration

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
  IDs + allowed dictionary values (these populate the UI selects).
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

## 7. Color detection pipeline (per image)

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

## 8. Matching & ranking

- Convert representative color and target hex to LAB.
- Compute **CIEDE2000 ΔE** (perceptually uniform).
- Rank by ΔE ascending; apply a user-tunable threshold (default e.g. ΔE ≤ 10).
- ΔE intuition: `<1` imperceptible · `1–2` very close · `2–10` close · `>10` distinct.

---

## 9. Concurrency, caching & performance

- **Async orchestration**: fetch listing pages, download images concurrently (`asyncio`
  + bounded semaphore). Run CPU-bound CV (rembg/k-means) in a thread/process pool so the
  event loop (and the NiceGUI UI) stays responsive.
- **Stream results**: the orchestrator yields offers as they resolve; the UI renders cards
  incrementally with a progress bar — process top-N first.
- **Caching** (diskcache): OAuth token; resolved category/param IDs; listing pages (short
  TTL); per-image color by URL hash (long TTL — the biggest win).

---

## 10. UI / UX (NiceGUI)

- **Search panel**: multi-select brands, multi-select types, hex color picker (live swatch
  + manual entry), ΔE threshold slider, max-results input.
- **Results grid**: cards with offer thumbnail, extracted swatch beside target swatch, ΔE
  badge, price, link to the Allegro offer.
- **Progress**: live progress bar/count as offers stream in; graceful empty/error states.

---

## 11. Configuration (environment variables)

```
ALLEGRO_CLIENT_ID=...
ALLEGRO_CLIENT_SECRET=...
ALLEGRO_ENV=prod                 # prod | sandbox
MAX_OFFERS=120                   # cap offers processed per search
IMAGE_CONCURRENCY=8              # parallel downloads/CV workers
DELTA_E_THRESHOLD=10             # default match threshold
CACHE_DIR=.cache
```

`.env.example` is shipped; the real `.env` is never committed.

---

## 12. Dependencies (initial)

`nicegui`, `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`, `rembg`,
`onnxruntime`, `opencv-python-headless`, `numpy`, `scikit-learn`, `colour-science`,
`diskcache`, `pillow`. Dev: `pytest`, `ruff`.

---

## 13. Phased roadmap

- **M1 — Allegro client**: auth, category/param discovery, listing + pagination, cached.
  CLI smoke test printing offers for given brands/types.
- **M2 — Color pipeline**: download → rembg → LAB k-means → neutral filtering →
  representative color. Test on real spool + figure images.
- **M3 — Matching**: CIEDE2000 ranking + threshold; end-to-end CLI (params → ranked offers).
- **M4 — NiceGUI UI**: search form, streaming results grid, swatches, progress — over core.
- **M5 — Hardening**: caching tuning, rate-limit/backoff, error states, README + Allegro
  app-registration guide.
- **M6 (optional)** — accuracy pass: small labeled set; consider a learned color model only
  if Delta E proves insufficient.

---

## 14. Risks & open questions

- **Spool reel vs filament**: reel color ≈ filament, or small filament region. Mitigation:
  neutral filtering + saturation weighting; region heuristics later if needed.
- **Multi-color / mixed photos**: several spools/props. Mitigation: confidence scoring +
  multi-image aggregation.
- **rembg model size/latency**: first run downloads U²-Net weights; CV is CPU-bound.
  Mitigation: caching, thread/process pool, bounded concurrency.
- **Allegro rate limits / schema changes**: discover params at runtime, cache, back off.
- **Display vs photo color fidelity**: lighting/white-balance shifts true color; ΔE
  threshold is tunable, perfect accuracy not guaranteed.

---

## 15. Testing

- Unit: neutral-cluster filtering, CIEDE2000 vs reference pairs, Allegro query-string
  building, token refresh.
- Integration: color pipeline on a fixture set of real spool + figure images with expected
  color ranges.
- Allegro client tested against mocked HTTP responses (no live calls in CI).
```
