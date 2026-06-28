import asyncio
import logging

from nicegui import ui

from filament_assistant.config import get_settings
from filament_assistant.core.allegro.categories import (
    get_filament_filters,
    invalidate_category,
    invalidate_filters,
    search_offers,
)
from filament_assistant.core.allegro.client import AllegroClient
from filament_assistant.core.allegro.models import FilamentFilters
from filament_assistant.core.color.matching import RankedOffer
from filament_assistant.core.color.pipeline import process_offers

logger = logging.getLogger(__name__)


async def _load_filters() -> FilamentFilters | None:
    try:
        async with AllegroClient(get_settings()) as client:
            return await get_filament_filters(client)
    except Exception as exc:
        logger.error("Failed to load Allegro filters: %s", exc)
        return None


def _render_result(ranked: RankedOffer, container: ui.column) -> None:
    with container:
        with ui.card().classes("w-full p-3"):
            with ui.row().classes("items-center gap-3 w-full flex-nowrap"):
                # Colour swatch
                ui.element("div").style(
                    f"width:36px;height:36px;flex-shrink:0;"
                    f"background:{ranked.color.hex};"
                    "border-radius:6px;border:1px solid rgba(0,0,0,0.15)"
                )
                # Name + price
                with ui.column().classes("flex-1 gap-0 min-w-0"):
                    ui.label(ranked.offer.name).classes(
                        "font-medium text-sm leading-tight"
                    ).style("overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                    if ranked.offer.price:
                        p = ranked.offer.price
                        ui.label(f"{p.amount} {p.currency}").classes("text-xs text-gray-500")
                # ΔE badge
                de = ranked.delta_e
                color = "green" if de < 5 else ("orange" if de < 12 else "red")
                ui.badge(f"ΔE {de:.1f}", color=color)
                # Allegro link
                ui.link("Open ↗", ranked.offer.url, new_tab=True).classes(
                    "text-xs text-blue-500 flex-shrink-0"
                )


@ui.page("/")
async def main_page() -> None:
    ui.page_title("Filament Color Finder")

    filters = await _load_filters()

    with ui.column().classes("w-full max-w-2xl mx-auto p-4 gap-4"):
        # ── Header ──────────────────────────────────────────────────────────
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Filament Color Finder").classes("text-2xl font-bold")
            ui.link("⚙ Dev", "/dev").classes("text-sm text-gray-400 no-underline")

        # ── Search form ──────────────────────────────────────────────────────
        with ui.card().classes("w-full p-4"):
            with ui.column().classes("w-full gap-4"):
                hex_input = ui.color_input(
                    label="Target colour",
                    value="#FF5733",
                ).classes("w-full")

                brand_opts = {v.id: v.name for v in filters.brands} if filters else {}
                brands_select = (
                    ui.select(
                        options=brand_opts,
                        multiple=True,
                        label="Brands (empty = all)",
                        clearable=True,
                    )
                    .classes("w-full")
                    .props("outlined use-chips")
                )

                type_opts = {v.id: v.name for v in filters.types} if filters else {}
                types_select = (
                    ui.select(
                        options=type_opts,
                        multiple=True,
                        label="Types (empty = all)",
                        clearable=True,
                    )
                    .classes("w-full")
                    .props("outlined use-chips")
                )

                with ui.column().classes("w-full gap-1"):
                    threshold_label = ui.label("Max ΔE: 10.0").classes("text-sm text-gray-500")
                    threshold_slider = ui.slider(min=1, max=30, step=0.5, value=10.0).classes(
                        "w-full"
                    )
                    threshold_slider.on_value_change(
                        lambda e: threshold_label.set_text(f"Max ΔE: {e.value:.1f}")
                    )

                find_btn = ui.button("Find matching filaments").classes("w-full")

        # ── Status + results ─────────────────────────────────────────────────
        status_label = ui.label("").classes("text-sm text-gray-500 self-center")
        spinner = ui.spinner(size="lg").classes("self-center")
        spinner.visible = False
        results_col = ui.column().classes("w-full gap-2")

    async def run_search() -> None:
        target_hex: str = hex_input.value or "#FF0000"
        brand_ids: list[str] = list(brands_select.value or [])
        type_ids: list[str] = list(types_select.value or [])
        threshold: float = float(threshold_slider.value)
        max_offers = get_settings().max_offers

        find_btn.disable()
        results_col.clear()
        spinner.visible = True
        status_label.text = "Fetching offers from Allegro…"

        try:
            offers = []
            page_size = 60
            async with AllegroClient(get_settings()) as client:
                while len(offers) < max_offers:
                    page = await search_offers(
                        client,
                        brand_ids=brand_ids or None,
                        type_ids=type_ids or None,
                        limit=min(page_size, max_offers - len(offers)),
                        offset=len(offers),
                    )
                    offers.extend(page.offers)
                    if len(page.offers) < page_size or len(offers) >= page.total_count:
                        break
                    status_label.text = f"Fetched {len(offers)} / {page.total_count} offers…"

            status_label.text = f"Analysing {len(offers)} offers…"

            count = 0
            async for ranked in process_offers(offers, target_hex, threshold):
                count += 1
                _render_result(ranked, results_col)
                status_label.text = f"Found {count} match{'es' if count != 1 else ''} so far…"
                await asyncio.sleep(0)

            if count:
                plural = "s" if count != 1 else ""
                status_label.text = f"Done — {count} matching offer{plural} found."
            else:
                status_label.text = "No matching offers found. Try a higher ΔE threshold."

        except Exception as exc:
            logger.exception("Search failed")
            ui.notify(str(exc), type="negative", position="top")
            status_label.text = "Search failed — check credentials and try again."
        finally:
            spinner.visible = False
            find_btn.enable()

    find_btn.on_click(run_search)

    if not filters:
        ui.notify(
            "Could not load Allegro filters — check your API credentials in .env",
            type="warning",
            position="top",
            timeout=0,
        )


@ui.page("/dev")
async def dev_page() -> None:
    ui.page_title("Dev Settings — Filament Assistant")
    settings = get_settings()

    with ui.column().classes("w-full max-w-xl mx-auto p-4 gap-4"):
        with ui.row().classes("w-full items-center gap-4"):
            ui.label("Dev Settings").classes("text-2xl font-bold")
            ui.button(
                "← Back", on_click=lambda: ui.navigate.to("/")
            ).props("flat color=grey")

        ui.separator()

        ui.label("Cache").classes("text-lg font-semibold")
        ui.label(f"Directory: {settings.cache_dir}").classes("text-sm text-gray-500")

        async def on_refresh_filters() -> None:
            invalidate_filters()
            try:
                async with AllegroClient(settings) as client:
                    fresh = await get_filament_filters(client)
                ui.notify(
                    f"Filters refreshed — {len(fresh.brands)} brands, {len(fresh.types)} types",
                    type="positive",
                )
            except Exception as exc:
                ui.notify(f"Refresh failed: {exc}", type="negative")

        def on_clear_category() -> None:
            invalidate_category()
            ui.notify(
                "Category cache cleared — will re-walk on next search",
                type="positive",
            )

        with ui.row().classes("gap-2"):
            ui.button("Refresh filters", on_click=on_refresh_filters).props("color=primary")
            ui.button("Clear category cache", on_click=on_clear_category).props("color=orange")

        ui.separator()

        ui.label("Allegro").classes("text-lg font-semibold")
        for label, value in [
            ("Environment", settings.allegro_env),
            ("API base", settings.api_base),
            ("Max offers", str(settings.max_offers)),
            ("Default ΔE threshold", str(settings.delta_e_threshold)),
            ("Image concurrency", str(settings.image_concurrency)),
        ]:
            with ui.row().classes("gap-2"):
                ui.label(f"{label}:").classes("text-sm text-gray-400 w-40")
                ui.label(value).classes("text-sm font-mono")


def create_app() -> None:
    ui.run(
        title="Filament Color Finder",
        host="0.0.0.0",
        port=8080,
        reload=False,
        show=False,
        dark=False,
    )
