import os
from pathlib import Path

from streamlit.web import cli


def main() -> None:
    app = str(Path(__file__).with_name("app.py"))
    cli.main_run([app, "--server.address", os.environ.get("CODEFORGE_HOST", "127.0.0.1"),
                  "--server.port", "8501", "--server.headless", "true", "--browser.gatherUsageStats", "false"])


if __name__ == "__main__":
    main()
