import json
import sys
from unittest.mock import patch


def test_run_command_saves_response(tmp_path, capsys):
    from enki.scripts import gemini_review

    with patch("enki.scripts.gemini_review.ENKI_ROOT", tmp_path), \
         patch("enki.scripts.gemini_review.run_api_review", return_value={"bead_decisions": [], "proposal_decisions": []}), \
         patch.object(sys, "argv", ["gemini_review", "--run", "alpha"]):
        gemini_review.main()

    out = capsys.readouterr().out
    assert "Saved response to:" in out

    files = list((tmp_path / "reviews").glob("response-*.json"))
    assert len(files) == 1
    payload = json.loads(files[0].read_text())
    assert payload == {"bead_decisions": [], "proposal_decisions": []}


def test_run_command_without_project(tmp_path):
    from enki.scripts import gemini_review

    captured = {}

    def _fake_run(project=None):
        captured["project"] = project
        return {"bead_decisions": [], "proposal_decisions": []}

    with patch("enki.scripts.gemini_review.ENKI_ROOT", tmp_path), \
         patch("enki.scripts.gemini_review.run_api_review", side_effect=_fake_run), \
         patch.object(sys, "argv", ["gemini_review", "--run"]):
        gemini_review.main()

    assert captured["project"] is None
