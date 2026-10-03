import pytest
from safe_parse import parse_dict, parse_device_commands, parse_device_counts

def test_parse_dict_valid_json():
    text = '{"Light1": "On", "Light2": "Off"}'
    res = parse_dict(text)
    assert res == {"Light1": "On", "Light2": "Off"}

def test_parse_dict_single_quotes():
    text = "{'Light1': 'On', 'Light2': 'Off'}"
    res = parse_dict(text)
    assert res == {"Light1": "On", "Light2": "Off"}

def test_parse_dict_markdown_block():
    text = "Here is the command:\n```json\n{'Light1': 'On'}\n```\nHope that helps!"
    res = parse_dict(text)
    assert res == {"Light1": "On"}

def test_parse_dict_invalid():
    with pytest.raises(ValueError):
        parse_dict("No dict here")
    with pytest.raises(ValueError):
        parse_dict("")

def test_parse_device_commands_canonical_and_normalization():
    allowed = ["Light1", "Light2"]
    text = '{"light1": "on", "LIGHT2": "OFF"}'
    valid, rejected = parse_device_commands(text, allowed)
    assert valid == {"Light1": "On", "Light2": "Off"}
    assert rejected == {}

def test_parse_device_commands_rejected():
    allowed = ["Light1", "Light2"]
    text = '{"Light1": "On", "Fan1": "On", "Light2": "Toggle"}'
    valid, rejected = parse_device_commands(text, allowed)
    assert valid == {"Light1": "On"}
    assert "Fan1" in rejected
    assert "Light2" in rejected

def test_parse_device_commands_unparseable():
    allowed = ["Light1", "Light2"]
    valid, rejected = parse_device_commands("this is not a dictionary", allowed)
    assert valid == {}
    assert "_raw" in rejected

def test_safe_parse_hostile_inputs():
    allowed = ["Light1", "Light2"]
    hostile_inputs = [
        "__import__('os').system('echo hacked')",
        "{'Light1': __import__('os').system('echo hacked')}",
        "exec('import os; os.system(\"calc\")')",
        "{'Light1': eval('1+1')}",
    ]
    for hostile in hostile_inputs:
        valid, rejected = parse_device_commands(hostile, allowed)
        assert valid == {}
        # Must not execute anything and either reject or fail gracefully

def test_parse_device_counts_valid():
    text = "{'light': 2, 'fan': 1}"
    res = parse_device_counts(text)
    assert res == {"light": 2, "fan": 1}

def test_parse_device_counts_invalid_range():
    with pytest.raises(ValueError):
        parse_device_counts("{'light': 0}")
    with pytest.raises(ValueError):
        parse_device_counts("{'light': 11}")
    with pytest.raises(ValueError):
        parse_device_counts("{'': 2}")
    with pytest.raises(ValueError):
        parse_device_counts("{'light': 'abc'}")
