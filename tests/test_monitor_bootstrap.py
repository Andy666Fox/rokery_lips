"""Monitoring setup keeps probes attached to the active agent after replacement."""

from pathlib import Path
from unittest.mock import patch

from scripts.bootstrap_monitoring import configure_monitors


def test_reconfigure_moves_probe_to_active_agent_and_keeps_thresholds(tmp_path: Path) -> None:
    environment = tmp_path / "config"
    environment.write_text("MONITORING_EMAIL=test@example.com\nMONITORING_PASSWORD=example\n")
    with patch("scripts.bootstrap_monitoring.Beszel") as factory:
        client = factory.return_value
        client.call.side_effect = [
            {"items": [{"id": "old", "status": "paused"}, {"id": "new", "status": "up"}]},
            {"items": [{"id": "probe", "system": "old", "target": "https://example.com/"}]},
            *[{} for _ in range(11)],
        ]
        configure_monitors(environment, "example.com")
        calls = client.call.call_args_list
        assert calls[2].args == (
            "/api/collections/network_monitors/records/probe",
            {"system": "new"},
        )
        assert calls[2].kwargs == {"method": "PATCH"}
        created = [
            call.args[1]
            for call in calls
            if call.args[0] == "/api/collections/network_monitors/records"
        ]
        assert len(created) == 4
        assert all(record["system"] == "new" for record in created)
        alerts = [call.args[1] for call in calls if call.args[0] == "/api/beszel/user-alerts"]
        assert len(alerts) == 6
        assert all(alert["systems"] == ["new"] and not alert["overwrite"] for alert in alerts)
