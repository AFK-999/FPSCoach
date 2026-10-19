from fps_coach import window_enum


def test_list_windows_returns_a_list() -> None:
    result = window_enum.list_windows()
    assert isinstance(result, list)
    # On Windows each entry has the expected shape; on other platforms it's empty.
    for item in result:
        assert {"hwnd", "title", "pid", "process"} <= set(item)


def test_get_window_rect_handles_bad_handle() -> None:
    # Never raises; returns None for an unknown handle (and on non-Windows).
    assert window_enum.get_window_rect(0) is None


def test_start_capture_accepts_window_origin() -> None:
    # The backend facade must accept the new optional left/top args without the
    # native module present (it should raise NativeCoreUnavailable, not TypeError).
    from fps_coach.backend import CoreBackend, NativeCoreUnavailable

    backend = CoreBackend()
    if backend.capture is None:  # no native core in this environment
        raised = False
        try:
            backend.start_capture(0, 0, 640, 360, 60, left=100, top=50)
        except NativeCoreUnavailable:
            raised = True
        assert raised
