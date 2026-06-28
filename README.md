# Filament Assistant

Find 3D printing filaments on [Allegro](https://allegro.pl) that match a target colour.

Pick a hex colour, choose brands and filament types, and the app fetches listings,
removes image backgrounds, extracts the dominant filament colour with LAB k-means
clustering, and ranks results by CIEDE2000 perceptual colour distance (ΔE).

## Prerequisites

- Python 3.11+
- [uv](https://docs.astral.sh/uv/) package manager
- Allegro API client credentials — register at [developer.allegro.pl](https://developer.allegro.pl)

## Setup

```bash
git clone https://github.com/Dombearx/AllegroFilamentAssistant
cd AllegroFilamentAssistant

# Install dependencies (including rembg's ~170 MB U2-Net model on first run)
uv sync

# Configure credentials
cp .env.example .env
$EDITOR .env   # fill ALLEGRO_CLIENT_ID and ALLEGRO_CLIENT_SECRET
```

## Running

```bash
uv run filament-assistant
```

Open [http://localhost:8080](http://localhost:8080) in your browser.

On first run the app will:
1. Walk the Allegro category tree to find the *Filamenty* category ID and cache it permanently.
2. Fetch available brand and type filter values and cache them permanently.

Both caches survive restarts. Use the **Dev → Refresh filters** button to invalidate them.

## Configuration

All settings can be overridden via environment variables or `.env`:

| Variable | Default | Description |
|---|---|---|
| `ALLEGRO_CLIENT_ID` | *(required)* | Allegro OAuth2 client ID |
| `ALLEGRO_CLIENT_SECRET` | *(required)* | Allegro OAuth2 client secret |
| `ALLEGRO_ENV` | `sandbox` | `sandbox` or `prod` |
| `MAX_OFFERS` | `120` | Maximum offers fetched per search |
| `IMAGE_CONCURRENCY` | `8` | Parallel image download + CV workers |
| `DELTA_E_THRESHOLD` | `10.0` | Default ΔE match threshold shown in the UI |
| `CACHE_DIR` | `.cache` | Directory for the persistent diskcache |

Switch to production by setting `ALLEGRO_ENV=prod`.

## How it works

```
User picks colour (#hex) + filters
        │
        ▼
Allegro API  ──►  paginated offer listing (up to MAX_OFFERS)
        │
        ▼  (concurrent, bounded by IMAGE_CONCURRENCY)
Per-offer images  ──►  rembg (U2-Net) background removal
                  ──►  LAB k-means (k=4) dominant colour
                  ──►  neutral-cluster filter (chroma < 15)
        │
        ▼
CIEDE2000 ΔE vs target  ──►  filter by threshold  ──►  stream ranked cards
```

Extracted colours are cached per image URL (30-day TTL) so repeat searches are fast.

## Development

```bash
# Run tests
uv run pytest

# Lint
uv run ruff check src/ tests/

# Smoke-test the Allegro API connection (requires valid credentials)
uv run allegro-smoke-test
```

### Project layout

```
src/filament_assistant/
  config.py                  # pydantic-settings singleton
  main.py                    # entrypoint (logging + ui.run)
  core/
    cache.py                 # thin diskcache wrapper
    allegro/
      auth.py                # OAuth2 client-credentials token
      client.py              # httpx client with retry logic
      categories.py          # category discovery + filter caching + search
      models.py              # dataclasses: Offer, Price, ParamValue, …
    color/
      segmentation.py        # rembg background removal
      dominant.py            # LAB k-means dominant-colour extraction
      matching.py            # CIEDE2000 ΔE + RankedOffer
      pipeline.py            # async orchestrator (download → process → rank)
  ui/
    app.py                   # NiceGUI pages: main search + /dev settings
```
