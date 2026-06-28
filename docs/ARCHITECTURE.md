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

## 2. Tech stack

| Concern            | Choice                          | Why |
|--------------------|---------------------------------|-----|
| Language           | **Python 3.11+**                | Image processing lives in Python; one language end-to-end. |
| UI                 | **NiceGUI**                     | Preferred; built on FastAPI, async-friendly, fast modern UI with no separate JS toolchain. |
| HTTP client        | **httpx** (async)               | Async Allegro calls + concurrent image downloads. |
| Background removal | **rembg** (U²-Net)              | Isolates the object (spool *or* figure) from Allegro's mostly-white backgrounds. No training data needed. |
| Color math / CV    | **OpenCV**, **NumPy**, **scikit-learn** | LAB conversion, k-means dominant-color clustering. |
| Color matching     | **colour-science** (or small in-repo CIEDE2000) | Perceptual Delta E (CIEDE2000) instead of naive RGB distance. |
| Caching            | **diskcache**                   | Cache tokens, offer pages, and per-image color results across runs. |
| Config             | **pydantic-settings** + `.env`  | All secrets/config via environment variables. |
| Packaging / deps   | **uv** (or pip + `pyproject.toml`) | Fast, reproducible installs. |

> Decision: **No model fine-tuning for v1.** The segmentation + dominant-color + Delta E
> pipeline is explainable, needs zero labeled data, and handles both spools and figures.
> A learned model is a possible future phase *only* if accuracy proves insufficient.

---

## 3. High-level data flow

```
            ┌─────────────────────────────────────────────────────────┐
            │                      NiceGUI UI                          │
            │  brands[]  types[]  target_hex  threshold  → [Search]    │
            └───────────────┬─────────────────────────────────────────┘
                            │ search params
                            ▼
            ┌─────────────────────────────────────────────────────────┐
            │                   Search Orchestrator                    │
            │   (async; streams results back to UI with progress)     │
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
                              ranked results → UI
```

---

## 4. Project structure

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
        ├── allegro/
        │   ├── auth.py            # OAuth2 client-credentials token + refresh
        │   ├── client.py          # httpx wrapper, retries, rate-limit handling
        │   ├── categories.py      # discover filament category + brand/type params
        │   └── models.py          # Offer, Image, ListingPage dataclasses
        ├── color/
        │   ├── segmentation.py    # rembg foreground extraction
        │   ├── dominant.py        # LAB k-means, neutral filtering → filament color
        │   ├── matching.py        # CIEDE2000 delta-E, ranking
        │   └── pipeline.py        # download → segment → dominant → (color, confidence)
        ├── search/
        │   └── orchestrator.py    # ties Allegro + color pipeline, async + caching
        ├── cache.py               # diskcache wrappers (token, pages, image colors)
        └── ui/
            ├── app.py             # layout, search form, results grid
            └── components.py      # offer card, swatch, progress
```

---

## 5. Allegro API integration

### Auth — OAuth2 client-credentials (public data only)
Searching **public** offers does not require a user login, only an app token.

```
POST {AUTH_BASE}/auth/oauth/token
Authorization: Basic base64(client_id:client_secret)
body: grant_type=client_credentials
→ { access_token, expires_in }
```

- Token is cached and refreshed ~60s before expiry.
- All API requests send `Accept: application/vnd.allegro.public.v1+json`.

### Environments (selected via env var)
| Env   | AUTH_BASE                                   | API_BASE                                   |
|-------|---------------------------------------------|--------------------------------------------|
| prod  | `https://allegro.pl`                        | `https://api.allegro.pl`                   |
| sandbox | `https://allegro.pl.allegrosandbox.pl`    | `https://api.allegro.pl.allegrosandbox.pl` |

### Category & parameter discovery
Brand/type filters are **category parameters**, and their IDs differ per category, so we
discover them at runtime instead of hardcoding:
- `GET /sale/categories` (+ children) → locate the 3D-printing filament category.
- `GET /sale/categories/{id}/parameters` → find the "brand/marka" and "material type"
  parameter IDs and their allowed dictionary values.
- Cache the resolved IDs and value maps (they change rarely).

### Offer search
```
GET {API_BASE}/offers/listing
    ?category.id={filamentCategoryId}
    &parameter.{brandParamId}={valueIds...}
    &parameter.{typeParamId}={valueIds...}
    &limit=60&offset=0
```
- Paginate with `limit`/`offset` (cap total via a configurable `MAX_OFFERS`).
- Each item carries an `images[]` array of URLs → fed to the color pipeline.

### Rate limits & resilience
- Respect `429` with backoff; honor `Retry-After`.
- Cache listing pages by query signature (short TTL) and image colors (long TTL).

---

## 6. Color detection pipeline (per image)

Designed to work for **both** spool photos and printed-figure photos.

1. **Download** the image (httpx, cached by URL hash).
2. **Segment foreground** with `rembg` → RGBA; keep pixels where alpha > threshold.
   - Removes the (typically white/gray) studio background for spools *and* figures.
3. **Cluster** foreground pixels in **LAB** color space with k-means (k = 3–5).
4. **Filter neutral clusters** — drop near-white/gray/black clusters (low LAB chroma):
   these are the reel, shadows, highlights, or leftover background.
   - Exception: if the **target color itself is neutral** (low chroma), keep neutrals.
5. **Pick the filament color** = most prominent remaining (most pixels × saturation-weighted)
   cluster centroid.
6. Return `(rgb, confidence)` where confidence reflects foreground coverage and cluster
   dominance. Images with no usable foreground are skipped.

**Per offer:** run the pipeline over its images, then aggregate (e.g. the highest-confidence
image, or median of consistent images) into one representative color.

---

## 7. Matching & ranking

- Convert representative color and target hex to LAB.
- Compute **CIEDE2000 ΔE** (perceptually uniform; far better than RGB Euclidean distance).
- Rank offers by ΔE ascending; apply a user-tunable threshold (default e.g. ΔE ≤ 10).
- Rough ΔE intuition: `<1` imperceptible · `1–2` very close · `2–10` close · `>10` distinct.

---

## 8. Concurrency, caching & performance

- **Async orchestration**: fetch listing pages, then download images concurrently
  (`asyncio` + bounded semaphore). Run CPU-bound CV (rembg/k-means) in a thread/process
  pool so the event loop stays responsive.
- **Stream results**: process top-N offers first and push cards into the UI as they
  resolve, with a progress bar — no waiting for the full set.
- **Caching layers** (diskcache):
  - OAuth token (until expiry).
  - Resolved category/parameter IDs.
  - Listing pages (short TTL).
  - Per-image extracted color keyed by image-URL hash (long TTL — the biggest win).

---

## 9. UI / UX (NiceGUI)

- **Search panel**: multi-select brands, multi-select types, hex color picker (with live
  swatch + manual hex entry), ΔE threshold slider, max-results input.
- **Results grid**: cards showing offer thumbnail, extracted color swatch beside the
  target swatch, ΔE score/badge, price, and a link to the Allegro offer.
- **Progress**: live progress bar + count as offers stream in; graceful empty/error states.

---

## 10. Configuration (environment variables)

All via `.env` / real env (`.env.example` shipped, never the real `.env`):

```
ALLEGRO_CLIENT_ID=...
ALLEGRO_CLIENT_SECRET=...
ALLEGRO_ENV=prod            # prod | sandbox
MAX_OFFERS=120              # cap offers processed per search
IMAGE_CONCURRENCY=8         # parallel downloads/CV workers
DELTA_E_THRESHOLD=10        # default match threshold
CACHE_DIR=.cache
```

---

## 11. Dependencies (initial)

`nicegui`, `httpx`, `pydantic`, `pydantic-settings`, `python-dotenv`, `rembg`,
`onnxruntime`, `opencv-python-headless`, `numpy`, `scikit-learn`, `colour-science`,
`diskcache`, `pillow`. Dev: `pytest`, `ruff`.

---

## 12. Phased roadmap

- **M1 — Allegro client**: auth, category/param discovery, offer listing + pagination,
  cached. CLI smoke test that prints offers for given brands/types.
- **M2 — Color pipeline**: download → rembg → LAB k-means → neutral filtering →
  representative color. Test on a handful of real spool + figure images.
- **M3 — Matching**: CIEDE2000 ranking + threshold; end-to-end CLI (params in → ranked
  offers out).
- **M4 — NiceGUI UI**: search form, streaming results grid, swatches, progress.
- **M5 — Hardening**: caching tuning, rate-limit/backoff, error states, README + setup
  guide for registering the Allegro app.
- **M6 (optional)** — accuracy pass: small labeled set to measure error; consider a
  learned color model only if Delta E proves insufficient.

---

## 13. Risks & open questions

- **Spool reel vs filament**: when the reel color ≈ filament color or the wound filament
  is a small image region, dominant-color may misfire. Mitigation: neutral filtering +
  saturation weighting; revisit with region heuristics if needed.
- **Multi-color / mixed photos**: lifestyle shots with several spools or props. Mitigation:
  confidence scoring + multi-image aggregation; prefer high-confidence images.
- **rembg model size/latency**: first run downloads the U²-Net weights; CV is CPU-bound.
  Mitigation: caching, thread/process pool, bounded concurrency.
- **Allegro rate limits / category schema changes**: discover params at runtime, cache,
  back off on 429.
- **Display vs photo color fidelity**: lighting/white-balance shifts true color. Delta E
  threshold is tunable; perfect accuracy is not guaranteed.

---

## 14. Testing

- Unit: neutral-cluster filtering, CIEDE2000 against known reference pairs, Allegro
  query-string building, token refresh logic.
- Integration: color pipeline on a small fixture set of real spool + figure images with
  expected color ranges.
- Allegro client tested against mocked HTTP responses (no live calls in CI).
```
