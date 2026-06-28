import logging

from filament_assistant.ui.app import create_app


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)-35s %(levelname)-8s %(message)s",
        datefmt="%H:%M:%S",
    )
    create_app()
