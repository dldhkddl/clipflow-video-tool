from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_creator_theme_is_enabled_and_uses_dark_design_tokens():
    html = (ROOT / "src/clipflow/templates/index.html").read_text(encoding="utf-8")
    css = (ROOT / "src/clipflow/static/app.css").read_text(encoding="utf-8")

    assert 'class="creator-theme"' in html
    assert "--creator-coral:#ff5e62" in css
    assert "--creator-purple:#8b5cf6" in css
    assert ".panel-card" in css
    assert ".creator-appbar" in css
    assert ".direct-studio" in css
    assert ".creation-panel" in css
    assert ".result-panel" in css
    assert "width:min(1480px,100%)" in css
    assert "@media(max-width:900px)" in css


def test_creator_theme_keeps_accessible_motion_and_focus_rules():
    css = (ROOT / "src/clipflow/static/app.css").read_text(encoding="utf-8")

    assert ":focus-visible" in css
    assert "prefers-reduced-motion:reduce" in css
