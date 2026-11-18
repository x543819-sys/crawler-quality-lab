"""Small launcher: use the isolated local test server only when MySQL is selected."""
import json
import subprocess
import sys
from pathlib import Path

root = Path(__file__).resolve().parent
if "mysql" in sys.argv[1:]:
    config_path = root / "local_config.json"
    config = json.loads(config_path.read_text(encoding="utf-8")) if config_path.exists() else {}
    bootstrap = Path(config.get("mysql_bootstrap", "__not_configured__"))
    if bootstrap.is_file():
        result = subprocess.run([sys.executable, str(bootstrap)])
        if result.returncode:
            sys.exit(result.returncode)
if sys.argv[1:2] == ["dashboard"]:
    command = [sys.executable, "-m", "streamlit", "run", str(root / "dashboard.py"),
               "--server.address", "127.0.0.1", "--server.port", "8501",
               "--browser.gatherUsageStats", "false"]
elif sys.argv[1:2] == ["schedule"]:
    command = [sys.executable, "-u", str(root / "schedule_crawler.py"), *sys.argv[2:]]
elif sys.argv[1:2] == ["scrapy"]:
    command = [sys.executable, "-u", str(root / "run_scrapy.py"), *sys.argv[2:]]
else:
    command = [sys.executable, "-u", str(root / "run_project.py"), *sys.argv[1:]]
sys.exit(subprocess.call(command, cwd=root))
