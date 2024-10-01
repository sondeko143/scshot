from typing import Any

from scshot import Output, Settings, display_results


def test_min_settings():
    settings = Settings(
        google_translate_api_project_name="project-id/name",
        target_window_title="Game.exe",
    )
    assert settings.google_translate_api_project_name == "project-id/name"
    assert settings.target_window_title == "Game.exe"


def test_default_display_code(capsys: Any):
    settings = Settings(
        google_translate_api_project_name="project-id/name",
        target_window_title="Game.exe",
    )
    results = [Output(
        original=f"orig-{i}",
        translated=f"translated-{i}",
        left=1,
        top=2,
        right=3,
        bottom=4,
    ) for i in range(2)]
    display_results(results, settings.display_code)
    captured = capsys.readouterr()
    assert captured.out == """orig-0
->translated-0

orig-1
->translated-1

"""