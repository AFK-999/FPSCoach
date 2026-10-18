from fps_coach import env_repair


def test_project_root_points_at_repo() -> None:
    root = env_repair.project_root()
    assert (root / "pyproject.toml").exists()
    assert (root / "python" / "fps_coach").exists()


def test_healthy_environment_needs_no_repair() -> None:
    # Running the test suite from the repo means the package resolves locally,
    # so a freshly recorded environment should not request a repair.
    env_repair.ensure_environment(verbose=False)
    assert env_repair.package_location_is_stale() is False
