import json
from core.pbip.authoring.pbir_executor import write_visual, delete_visual_files


def test_write_visual_creates_folder_and_file(tmp_path):
    pages = tmp_path / "pages" / "page1" / "visuals"
    pages.mkdir(parents=True)
    visual_dict = {"name": "abc123", "visual": {"visualType": "card"}}
    result_path = write_visual(pages_visuals_dir=pages, visual_dict=visual_dict)
    assert result_path.exists()
    assert (result_path / "visual.json").exists()
    data = json.loads((result_path / "visual.json").read_text(encoding="utf-8"))
    assert data["name"] == "abc123"


def test_write_visual_creates_visuals_dir_if_missing(tmp_path):
    pages_dir = tmp_path / "visuals"
    visual_dict = {"name": "xyz999", "visual": {"visualType": "slicer"}}
    result_path = write_visual(pages_visuals_dir=pages_dir, visual_dict=visual_dict)
    assert (result_path / "visual.json").exists()


def test_delete_visual_files_removes_folder(tmp_path):
    v = tmp_path / "abc123"
    v.mkdir()
    (v / "visual.json").write_text("{}", encoding="utf-8")
    delete_visual_files(v)
    assert not v.exists()


def test_delete_visual_files_noop_if_missing(tmp_path):
    delete_visual_files(tmp_path / "nonexistent")
