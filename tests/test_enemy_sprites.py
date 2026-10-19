from fps_coach import enemy_sprites


def test_sprite_files_returns_bundled_pngs() -> None:
    files = enemy_sprites.sprite_files()
    assert isinstance(files, list)
    # The repo ships at least the generated enemy sprites.
    names = {p.name for p in files}
    assert {"enemy_robot.png", "enemy_drone.png"} <= names
    for path in files:
        assert path.suffix == ".png"
        assert path.is_file()
