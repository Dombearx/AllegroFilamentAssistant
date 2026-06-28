import asyncio
import logging
import os
import re
from datetime import UTC, datetime

from nicegui import app, ui

from filament_assistant.config import get_settings
from filament_assistant.core.allegro.categories import (
    get_filament_filters,
    invalidate_category,
    invalidate_filters,
    search_offers,
)
from filament_assistant.core.allegro.client import AllegroClient
from filament_assistant.core.allegro.color_match import closest_allegro_colour
from filament_assistant.core.allegro.models import FilamentFilters
from filament_assistant.core.color.matching import RankedOffer
from filament_assistant.core.color.pipeline import (
    get_debug_mode,
    init_executor,
    process_offers,
    set_debug_mode,
    shutdown_executor,
)
from filament_assistant.core.search.history import (
    SearchHistoryEntry,
    load_history,
    save_search,
    time_ago,
)

logger = logging.getLogger(__name__)

_HEX_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
_startup_error: str | None = None


# ── App lifecycle ─────────────────────────────────────────────────────────────

@app.on_startup
async def _on_startup() -> None:
    global _startup_error
    try:
        get_settings()
        init_executor()
        logger.info("Filament Assistant started")
    except Exception as exc:
        _startup_error = str(exc)
        logger.error("Startup error: %s", exc)


@app.on_shutdown
def _on_shutdown() -> None:
    shutdown_executor(wait=False)


# ── Helpers ───────────────────────────────────────────────────────────────────

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
                # Product thumbnail
                if ranked.offer.images:
                    ui.image(ranked.offer.images[0].url).style(
                        "width:52px;height:52px;object-fit:cover;"
                        "border-radius:6px;flex-shrink:0"
                    )
                # Extracted colour swatch
                ui.element("div").style(
                    f"width:28px;height:28px;flex-shrink:0;"
                    f"background:{ranked.color.hex};"
                    "border-radius:4px;border:1px solid rgba(0,0,0,0.15)"
                )
                # Name + price
                with ui.column().classes("flex-1 gap-0 min-w-0"):
                    ui.label(ranked.offer.name).classes(
                        "font-medium text-sm leading-tight"
                    ).style("overflow:hidden;text-overflow:ellipsis;white-space:nowrap")
                    if ranked.offer.price:
                        p = ranked.offer.price
                        ui.label(f"{p.amount} {p.currency}").classes("text-xs text-gray-500")
                # ΔE badge — green < 5, orange < 12, red ≥ 12
                de = ranked.delta_e
                badge_color = "green" if de < 5 else ("orange" if de < 12 else "red")
                ui.badge(f"ΔE {de:.1f}", color=badge_color)
                ui.link("Open ↗", ranked.offer.url, new_tab=True).classes(
                    "text-xs text-blue-500 flex-shrink-0"
                )

            if ranked.debug:
                with ui.expansion("Debug", icon="bug_report").classes("w-full mt-1").props(
                    "dense"
                ):
                    with ui.column().classes("gap-3 p-2"):
                        for dbg in ranked.debug:
                            with ui.row().classes("items-start gap-3 flex-wrap"):
                                if dbg.fg_image_b64:
                                    ui.image(
                                        f"data:image/png;base64,{dbg.fg_image_b64}"
                                    ).style(
                                        "width:64px;height:64px;object-fit:cover;"
                                        "border-radius:4px;border:1px solid rgba(0,0,0,0.1)"
                                    )
                                if dbg.color:
                                    with ui.column().classes("gap-1"):
                                        ui.element("div").style(
                                            f"width:24px;height:24px;"
                                            f"background:{dbg.color.hex};"
                                            "border-radius:3px;"
                                            "border:1px solid rgba(0,0,0,0.15)"
                                        )
                                        ui.label(dbg.color.hex).classes(
                                            "text-xs font-mono text-gray-500"
                                        )
                                        ui.label(
                                            f"conf {dbg.color.confidence:.2f}"
                                        ).classes("text-xs text-gray-400")
                                ui.label(dbg.url.split("/")[-1]).classes(
                                    "text-xs text-gray-400 self-center"
                                ).style("word-break:break-all")


# ── Pages ─────────────────────────────────────────────────────────────────────

@ui.page("/")
async def main_page() -> None:
    ui.page_title("Filament Color Finder")

    dark = ui.dark_mode()
    if app.storage.user.get("dark_mode", False):
        dark.enable()

    def _toggle_dark() -> None:
        dark.toggle()
        app.storage.user["dark_mode"] = dark.value

    if _startup_error:
        with ui.column().classes("w-full max-w-2xl mx-auto p-4 gap-4"):
            ui.label("Filament Color Finder").classes("text-2xl font-bold")
            with ui.card().classes("w-full p-4 bg-red-50"):
                ui.label("Configuration error").classes("text-lg font-semibold text-red-700")
                ui.label(_startup_error).classes("text-sm text-red-600 font-mono")
                ui.label(
                    "Copy .env.example to .env, fill in your Allegro API credentials, "
                    "then restart the app."
                ).classes("text-sm text-gray-600 mt-2")
        return

    filters = await _load_filters()

    with ui.column().classes("w-full max-w-2xl mx-auto p-4 gap-4"):
        # ── Header ──────────────────────────────────────────────────────────
        with ui.row().classes("w-full items-center justify-between"):
            ui.label("Filament Color Finder").classes("text-2xl font-bold")
            with ui.row().classes("items-center gap-2"):
                ui.button(icon="dark_mode", on_click=_toggle_dark).props(
                    "flat round dense"
                ).tooltip("Toggle dark mode")
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

                # ── Allegro colour filter ────────────────────────────────────
                with ui.column().classes("w-full gap-2"):
                    ui.label("Allegro colour filter").classes("text-sm text-gray-500")
                    color_mode = ui.radio(
                        {"off": "Off", "auto": "Auto", "manual": "Manual"},
                        value="off",
                    ).props("inline")

                    color_opts = (
                        {v.id: v.name for v in filters.colors} if filters else {}
                    )
                    color_select = (
                        ui.select(
                            options=color_opts,
                            multiple=False,
                            label="Colour",
                            clearable=True,
                        )
                        .classes("w-full")
                        .props("outlined")
                    )
                    color_select.visible = False

                    auto_color_label = ui.label("").classes("text-xs text-blue-500")
                    auto_color_label.visible = False

                def _update_color_filter() -> None:
                    mode = color_mode.value
                    color_select.visible = mode == "manual"
                    auto_color_label.visible = mode == "auto"
                    if mode == "auto" and filters and filters.colors:
                        match = closest_allegro_colour(
                            hex_input.value or "#FF0000", filters.colors
                        )
                        auto_color_label.set_text(
                            f"Matched: {match.name}" if match else "No colour match"
                        )

                hex_input.on_value_change(lambda _: _update_color_filter())
                color_mode.on_value_change(lambda _: _update_color_filter())

                with ui.column().classes("w-full gap-1"):
                    threshold_label = ui.label("Max ΔE: 10.0").classes("text-sm text-gray-500")
                    threshold_slider = ui.slider(min=1, max=30, step=0.5, value=10.0).classes(
                        "w-full"
                    )
                    threshold_slider.on_value_change(
                        lambda e: threshold_label.set_text(f"Max ΔE: {e.value:.1f}")
                    )

                find_btn = ui.button("Find matching filaments").classes("w-full")

        # ── Recent searches ──────────────────────────────────────────────────
        history_container = ui.column().classes("w-full gap-1")

        def _render_history() -> None:
            history_container.clear()
            entries = load_history()
            if not entries:
                return
            with history_container:
                ui.label("Recent searches").classes("text-xs text-gray-400")
                with ui.row().classes("flex-wrap gap-2"):
                    for entry in entries[:8]:
                        with ui.element("div").classes(
                            "flex items-center gap-1 px-2 py-1 rounded border "
                            "border-gray-200 cursor-pointer hover:bg-gray-50"
                        ).on("click", lambda _, e=entry: _restore(e)):
                            ui.element("div").style(
                                f"width:10px;height:10px;background:{entry.target_hex};"
                                "border-radius:2px;flex-shrink:0"
                            )
                            ui.label(
                                f"{entry.result_count} result"
                                f"{'s' if entry.result_count != 1 else ''}"
                                f" · {time_ago(entry.timestamp)}"
                            ).classes("text-xs text-gray-600")

        def _restore(entry: SearchHistoryEntry) -> None:
            hex_input.set_value(entry.target_hex)
            brands_select.set_value(entry.brand_ids)
            types_select.set_value(entry.type_ids)
            threshold_slider.set_value(entry.threshold)
            threshold_label.set_text(f"Max ΔE: {entry.threshold:.1f}")

        _render_history()

        # ── Status + results ─────────────────────────────────────────────────
        status_label = ui.label("").classes("text-sm text-gray-500 self-center")
        spinner = ui.spinner(size="lg").classes("self-center")
        spinner.visible = False
        results_col = ui.column().classes("w-full gap-2")

        # Load-more button — shown after first search if more results exist
        load_more_row = ui.row().classes("w-full justify-center gap-3 items-center")
        load_more_row.visible = False
        with load_more_row:
            load_more_btn = ui.button("Load more").props("outline")
            load_more_count = ui.label("").classes("text-sm text-gray-500")

    # Mutable state shared between run_search and run_load_more
    state: dict = {
        "offset": 0,
        "total_count": 0,
        "target_hex": "#FF5733",
        "brand_ids": [],
        "type_ids": [],
        "color_ids": [],
        "threshold": 10.0,
        "result_count": 0,
    }

    async def _stream_offers(offers: list, clear_first: bool) -> int:
        if clear_first:
            results_col.clear()
        count = 0
        async for ranked in process_offers(offers, state["target_hex"], state["threshold"]):
            count += 1
            _render_result(ranked, results_col)
            status_label.text = f"Found {count} match{'es' if count != 1 else ''} so far…"
            await asyncio.sleep(0)
        return count

    async def run_search() -> None:
        target_hex: str = hex_input.value or "#FF0000"

        if not _HEX_RE.match(target_hex):
            ui.notify("Invalid hex colour — expected format #rrggbb", type="warning")
            return

        brand_ids: list[str] = list(brands_select.value or [])
        type_ids: list[str] = list(types_select.value or [])
        threshold: float = float(threshold_slider.value)
        max_offers = get_settings().max_offers

        # Resolve colour filter
        if color_mode.value == "auto" and filters and filters.colors:
            match = closest_allegro_colour(target_hex, filters.colors)
            color_ids: list[str] = [match.id] if match else []
        elif color_mode.value == "manual" and color_select.value:
            color_ids = [color_select.value]
        else:
            color_ids = []

        state.update(
            target_hex=target_hex,
            brand_ids=brand_ids,
            type_ids=type_ids,
            color_ids=color_ids,
            threshold=threshold,
            offset=0,
            total_count=0,
            result_count=0,
        )

        find_btn.disable()
        load_more_row.visible = False
        spinner.visible = True
        status_label.text = "Fetching offers from Allegro…"

        try:
            offers = []
            page_size = 60
            last_total = 0
            async with AllegroClient(get_settings()) as client:
                while len(offers) < max_offers:
                    page = await search_offers(
                        client,
                        brand_ids=brand_ids or None,
                        type_ids=type_ids or None,
                        color_ids=color_ids or None,
                        limit=min(page_size, max_offers - len(offers)),
                        offset=len(offers),
                    )
                    offers.extend(page.offers)
                    last_total = page.total_count
                    if len(page.offers) < page_size or len(offers) >= page.total_count:
                        break
                    status_label.text = f"Fetched {len(offers)} / {page.total_count} offers…"

            state["offset"] = len(offers)
            state["total_count"] = last_total

            status_label.text = f"Analysing {len(offers)} offers…"
            count = await _stream_offers(offers, clear_first=True)
            state["result_count"] = count

            if count:
                plural = "s" if count != 1 else ""
                status_label.text = f"Done — {count} matching offer{plural} found."
            else:
                status_label.text = "No matching offers found. Try a higher ΔE threshold."

            # Show "Load more" if Allegro has more pages beyond what we fetched
            if state["offset"] < last_total:
                load_more_btn.set_text(
                    f"Load more ({state['offset']} of {last_total} fetched)"
                )
                load_more_count.text = ""
                load_more_row.visible = True

            # Persist to search history
            brand_names = [brand_opts.get(bid, bid) for bid in brand_ids]
            type_names = [type_opts.get(tid, tid) for tid in type_ids]
            save_search(SearchHistoryEntry(
                timestamp=datetime.now(UTC).isoformat(),
                target_hex=target_hex,
                brand_ids=brand_ids,
                brand_names=brand_names,
                type_ids=type_ids,
                type_names=type_names,
                threshold=threshold,
                result_count=count,
            ))
            _render_history()

        except Exception as exc:
            logger.exception("Search failed")
            ui.notify(str(exc), type="negative", position="top")
            status_label.text = "Search failed — check credentials and try again."
        finally:
            spinner.visible = False
            find_btn.enable()

    async def run_load_more() -> None:
        load_more_btn.disable()
        spinner.visible = True
        status_label.text = "Loading more offers…"

        try:
            async with AllegroClient(get_settings()) as client:
                page = await search_offers(
                    client,
                    brand_ids=state["brand_ids"] or None,
                    type_ids=state["type_ids"] or None,
                    color_ids=state["color_ids"] or None,
                    limit=60,
                    offset=state["offset"],
                )

            state["offset"] += len(page.offers)
            state["total_count"] = page.total_count

            new_count = await _stream_offers(page.offers, clear_first=False)
            state["result_count"] += new_count

            total_loaded = state["offset"]
            total_avail = state["total_count"]
            status_label.text = (
                f"Showing results for {total_loaded} of {total_avail} fetched offers."
            )

            if total_loaded < total_avail:
                load_more_btn.set_text(f"Load more ({total_loaded} of {total_avail} fetched)")
                load_more_btn.enable()
            else:
                load_more_row.visible = False

        except Exception as exc:
            logger.exception("Load more failed")
            ui.notify(str(exc), type="negative", position="top")
            load_more_btn.enable()
        finally:
            spinner.visible = False

    find_btn.on_click(run_search)
    load_more_btn.on_click(run_load_more)

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

    dark = ui.dark_mode()
    if app.storage.user.get("dark_mode", False):
        dark.enable()

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

        ui.label("Debug").classes("text-lg font-semibold")
        ui.switch(
            "Enable debug panels on results",
            value=get_debug_mode(),
            on_change=lambda e: set_debug_mode(e.value),
        )

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
    port = int(os.environ.get("PORT", "8080"))
    ui.run(
        title="Filament Color Finder",
        host="0.0.0.0",
        port=port,
        reload=False,
        show=False,
        dark=False,
        storage_secret=os.environ.get("STORAGE_SECRET", "filament-assistant-dev-secret"),
    )
