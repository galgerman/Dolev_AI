"""Start just the monitoring web server (no scraping, no Playwright)."""
import sys
import pathlib

sys.path.insert(0, str(pathlib.Path(__file__).parent.parent / "src"))

import uvicorn
from dolev_ai.main import Agent, _load_config
from dolev_ai.web.server import create_app

db_path = pathlib.Path(__file__).parent.parent / "data" / "dolev.db"
db_path.parent.mkdir(exist_ok=True)
agent = Agent(_load_config(), dry_run=True)

app = create_app(
    event_bus=agent.event_bus,
    session_factory=agent.session_factory,
    threshold=agent.threshold,
    agent_control=agent,
)

if __name__ == "__main__":
    print("Dashboard: http://localhost:8000")
    uvicorn.run(app, host="127.0.0.1", port=8000, log_level="info")
