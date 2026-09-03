from __future__ import annotations

import json
import os

import requests


class HomeAssistantService:
    DEFAULT_ENTITIES = {
        "wohnzimmer": "light.wohnzimmer",
        "schlafzimmer": "light.schlafzimmer",
        "flur": "light.flur",
    }

    def __init__(self):
        self.url = os.getenv("HOME_ASSISTANT_URL", "").rstrip("/")
        self.token = os.getenv("HOME_ASSISTANT_TOKEN", "")

    def _headers(self) -> dict:
        if not self.url or not self.token:
            raise RuntimeError("HOME_ASSISTANT_URL oder HOME_ASSISTANT_TOKEN fehlt.")
        return {"Authorization": f"Bearer {self.token}", "Content-Type": "application/json"}

    def entities(self) -> dict[str, str]:
        return {name: os.getenv(f"HA_LIGHT_{name.upper()}", entity) for name, entity in self.DEFAULT_ENTITIES.items()}

    def states(self) -> list[dict]:
        response = requests.get(f"{self.url}/api/states", headers=self._headers(), timeout=15)
        response.raise_for_status()
        wanted = set(self.entities().values())
        return [{"entity_id": item["entity_id"], "state": item["state"], "friendly_name": item.get("attributes", {}).get("friendly_name")}
                for item in response.json() if item["entity_id"] in wanted]

    def grow_sensors(self) -> dict:
        """Messwerte des Growcontrollers. Gelesen wird ausschließlich, was in
        ``HA_GROW_SENSORS_JSON`` als ``{"Beschriftung": "sensor.entity_id"}``
        freigegeben ist – dieselbe Allowlist-Logik wie beim Schalten."""
        raw = os.getenv("HA_GROW_SENSORS_JSON", "{}").strip() or "{}"
        try:
            wanted = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("HA_GROW_SENSORS_JSON enthält kein gültiges JSON.") from exc
        if not isinstance(wanted, dict):
            raise RuntimeError("HA_GROW_SENSORS_JSON muss ein JSON-Objekt sein.")
        if not self.url or not self.token or not wanted:
            return {"configured": False, "sensors": []}
        response = requests.get(f"{self.url}/api/states", headers=self._headers(), timeout=15)
        response.raise_for_status()
        states = {item["entity_id"]: item for item in response.json()}
        sensors = []
        for label, entity_id in wanted.items():
            item = states.get(entity_id)
            if item is None:
                continue
            sensors.append({"label": label, "value": item.get("state", "–"),
                            "unit": item.get("attributes", {}).get("unit_of_measurement", "")})
        return {"configured": True, "sensors": sensors}

    def switch_light(self, room: str, state: str) -> str:
        if room not in self.entities() or state not in {"on", "off"}:
            raise ValueError("Erlaubt sind wohnzimmer/schlafzimmer/flur und on/off.")
        service = "turn_on" if state == "on" else "turn_off"
        response = requests.post(f"{self.url}/api/services/light/{service}", headers=self._headers(),
                                 json={"entity_id": self.entities()[room]}, timeout=15)
        response.raise_for_status()
        return f"{room} {state}"

    @staticmethod
    def _action_profiles(group: str) -> dict[str, dict]:
        variable = f"HA_{group.upper()}_ACTIONS_JSON"
        raw = os.getenv(variable, "{}").strip() or "{}"
        try:
            profiles = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"{variable} enthält kein gültiges JSON.") from exc
        if not isinstance(profiles, dict):
            raise RuntimeError(f"{variable} muss ein JSON-Objekt sein.")
        result = {}
        for name, profile in profiles.items():
            if not isinstance(name, str) or not isinstance(profile, dict):
                raise RuntimeError(f"{variable} enthält ein ungültiges Aktionsprofil.")
            if not name or not name.replace("_", "").replace("-", "").isalnum():
                raise RuntimeError(f"{variable} enthält einen ungültigen Aktionsnamen.")
            required = {"domain", "service", "entity_id"}
            if not required <= profile.keys() or not all(isinstance(profile[key], str) and profile[key] for key in required):
                raise RuntimeError(f"{variable}/{name} benötigt domain, service und entity_id.")
            if not profile["domain"].replace("_", "").isalnum() or not profile["service"].replace("_", "").isalnum():
                raise RuntimeError(f"{variable}/{name} enthält einen ungültigen Dienstnamen.")
            result[name] = {"domain": profile["domain"], "service": profile["service"],
                            "entity_id": profile["entity_id"], "data": profile.get("data", {})}
        return result

    def action_status(self) -> dict[str, list[str]]:
        return {group: sorted(self._action_profiles(group)) for group in ("alexa", "grow")}

    def run_profile(self, group: str, action: str) -> str:
        if group not in {"alexa", "grow"}:
            raise ValueError("Erlaubt sind alexa und grow.")
        profiles = self._action_profiles(group)
        if action not in profiles:
            raise ValueError(f"Nicht freigegebene {group}-Aktion: {action}")
        profile = profiles[action]
        payload = {"entity_id": profile["entity_id"]}
        if isinstance(profile["data"], dict):
            payload.update(profile["data"])
        response = requests.post(
            f"{self.url}/api/services/{profile['domain']}/{profile['service']}",
            headers=self._headers(), json=payload, timeout=15,
        )
        response.raise_for_status()
        return f"{group}/{action} ausgeführt"
