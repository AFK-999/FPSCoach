from fps_coach.ui.styles import APP_STYLE, PALETTES, build_style


def test_build_style_all_mode_theme_combos() -> None:
    for mode in ("luxurious", "performance"):
        for theme in ("dark", "light"):
            css = build_style(mode, theme)
            assert "%(" not in css  # every token substituted
            assert "QFrame#card" in css
            assert PALETTES[theme]["bg"] in css


def test_unknown_theme_falls_back_to_dark() -> None:
    assert build_style("luxurious", "nope") == build_style("luxurious", "dark")


def test_app_style_is_dark_default() -> None:
    assert APP_STYLE == build_style("luxurious", "dark")
    assert PALETTES["dark"]["bg"] in APP_STYLE
