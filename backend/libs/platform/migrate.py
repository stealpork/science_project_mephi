import argparse
from pathlib import Path

from alembic import command
from alembic.config import Config

from libs.platform.config import get_settings


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["upgrade", "current", "check", "revision"])
    parser.add_argument("--message", default="initial_schema")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    settings = get_settings()
    config = Config()
    config.set_main_option("script_location", str(root / "migrations"))
    config.set_main_option(
        "version_locations",
        str(root / "services" / settings.service_name / "migrations" / "versions"),
    )
    config.set_main_option("sqlalchemy.url", settings.database_url.replace("%", "%%"))
    if args.action == "upgrade":
        command.upgrade(config, "head")
    elif args.action == "revision":
        command.revision(config, message=args.message, autogenerate=True)
    else:
        getattr(command, args.action)(config)


if __name__ == "__main__":
    main()
