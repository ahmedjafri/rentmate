import json
from pathlib import Path
from typing import Any, Dict, Tuple

_AUTOMATIONS_DIR = Path(__file__).parent.parent / "automations"


def _load_automations() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    default_config: Dict[str, Any] = {"checks": {}}
    check_meta: Dict[str, Any] = {}
    for path in sorted(_AUTOMATIONS_DIR.glob("*.json")):
        data = json.loads(path.read_text())
        key = data["key"]
        default_config["checks"][key] = data["default_config"]
        check_meta[key] = {k: v for k, v in data.items() if k not in ("key", "default_config")}
    return default_config, check_meta


_DEFAULT_AUTOMATION_CONFIG, _CHECK_META = _load_automations()
