import os
import csv
import pytest
import safe_parse
import mqtt_executor
import metrics
import camera_capture

def test_mqtt_defense_in_depth(monkeypatch):
    monkeypatch.setenv("MQTT_DRY_RUN", "true")
    monkeypatch.setenv("ENABLED_DEVICES", "Light1,Light2")

    # Valid execution
    assert mqtt_executor.execute("Light1", "On") is True
    assert mqtt_executor.execute("light2", "off") is True

    # Invalid device rejected
    assert mqtt_executor.execute("Fan1", "On") is False
    assert mqtt_executor.execute("Light5", "On") is False

    # Invalid command rejected
    assert mqtt_executor.execute("Light1", "Toggle") is False
    assert mqtt_executor.execute("Light1", "Dim") is False

def test_metrics_csv_generation(tmp_path, monkeypatch):
    test_log_dir = tmp_path / "logs"
    test_metrics_file = test_log_dir / "metrics.csv"
    monkeypatch.setattr(metrics, "LOGS_DIR", str(test_log_dir))
    monkeypatch.setattr(metrics, "METRICS_FILE", str(test_metrics_file))

    with metrics.stage("test_stage", command_id="cmd123", note="testing"):
        x = sum([i for i in range(1000)])

    assert test_metrics_file.exists()
    with open(test_metrics_file, mode="r", encoding="utf-8") as f:
        reader = list(csv.reader(f))
        assert len(reader) >= 2
        header = reader[0]
        row = reader[1]
        assert header == ["timestamp_iso", "run_id", "platform", "command_id", "stage", "duration_ms", "note"]
        assert row[3] == "cmd123"
        assert row[4] == "test_stage"
        assert row[6] == "testing"

def test_camera_validation_error(monkeypatch):
    # Set invalid camera index to trigger clean error
    monkeypatch.setenv("CAMERA_INDEX", "999")
    with pytest.raises(RuntimeError) as exc_info:
        camera_capture.capture_scene()
    assert "Cannot open camera" in str(exc_info.value)
