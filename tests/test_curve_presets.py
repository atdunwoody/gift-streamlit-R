"""Never silently replace missing Shiny defaults with different example curves."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import curve_presets


def test_shiny_defaults_remain_explicitly_unavailable_without_source_files(tmp_path, monkeypatch):
    monkeypatch.setattr(curve_presets, "ROOT", tmp_path)
    result = curve_presets.gift_shiny_default()
    assert result["label"] == "GIFT Shiny Default"
    assert result["available"] is False
    assert "depth" not in result
    assert "no substitute" in result["curveNote"]


def test_shiny_csv_preset_reads_values_directly_and_validates_units_structure(tmp_path, monkeypatch):
    monkeypatch.setattr(curve_presets, "ROOT", tmp_path)
    folder = tmp_path / "inputs" / "curves"
    folder.mkdir(parents=True)
    for kind, content in (
        ("depth", "depth,suit\n0,0\n1,1\n"),
        ("velocity", "velocity,suit\n0,1\n2,0\n"),
        ("substrate", "lower,upper,suit\n0,1000,0.4\n")):
        (folder/f"gift_shiny_default_{kind}.csv").write_text(content)
    result = curve_presets.gift_shiny_default()
    assert result["available"] is True
    assert result["id"] == "gift_shiny_default"
    assert result["depth"] == [[0., 0.], [1., 1.]]
    assert result["velocity"] == [[0., 1.], [2., 0.]]
    assert result["substrate"] == [[0., 1000., .4]]
    (folder/"gift_shiny_default_depth.csv").write_text("depth,suit\n0,2\n1,1\n")
    result = curve_presets.gift_shiny_default()
    assert result["available"] is False and "invalid" in result["curveNote"]
