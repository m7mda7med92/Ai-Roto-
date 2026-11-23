"""Frame sources: sequence patterns, colour conversion, video, proxies."""
import pytest

cv2 = pytest.importorskip("cv2")
np = pytest.importorskip("numpy")

from ai_engine.media.frame_source import (MediaError, open_source, resolve_sequence, to_rgb8,  # noqa: E402
                                          write_jpeg, write_mask_png)
from helpers import synthetic_plate  # noqa: E402


def test_sequence_patterns_resolve_to_the_same_frames(tmp_path):
    pattern, _ = synthetic_plate(tmp_path / "seq", count=4, first=1001)
    folder = tmp_path / "seq"
    expected = {1001, 1002, 1003, 1004}
    for spec in ("plate.####.png", "plate.%04d.png", "plate.$F4.png", "plate.1002.png"):
        assert set(resolve_sequence(str(folder / spec))) == expected, spec
    (folder / "other.1001.png").write_bytes(b"x")
    assert set(resolve_sequence(pattern)) == expected


def test_image_sequence_source(tmp_path):
    pattern, _ = synthetic_plate(tmp_path / "seq", count=3, first=10, width=64, height=48)
    with open_source(pattern) as source:
        assert source.info.kind == "sequence"
        assert (source.info.first_frame, source.info.last_frame) == (10, 12)
        assert (source.info.width, source.info.height) == (64, 48)
        image = source.read(11)
        assert image.shape == (48, 64, 3) and image.dtype == np.uint8
        with pytest.raises(MediaError):
            source.read(99)


def test_to_rgb8_handles_bit_depths_and_channels():
    bgr16 = np.full((4, 4, 3), 65535, np.uint16)
    assert to_rgb8(bgr16).max() == 255
    linear = np.full((4, 4, 3), 0.18, np.float32)
    assert 110 < int(to_rgb8(linear)[0, 0, 0]) < 125  # sRGB of 18% grey ~ 118
    gray = np.zeros((4, 4), np.uint8)
    assert to_rgb8(gray).shape == (4, 4, 3)
    bgra = np.zeros((4, 4, 4), np.uint8)
    bgra[..., 0] = 255  # blue in BGR order
    assert tuple(to_rgb8(bgra)[0, 0]) == (0, 0, 255)


def test_video_source_numbers_frames_from_first_frame(tmp_path):
    path = tmp_path / "clip.avi"
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 24, (64, 48))
    if not writer.isOpened():
        pytest.skip("no video encoder in this OpenCV build")
    for i in range(5):
        writer.write(np.full((48, 64, 3), i * 40, np.uint8))
    writer.release()
    with open_source(str(path), first_frame=1001) as source:
        assert source.info.kind == "video"
        assert source.frames() == [1001, 1002, 1003, 1004, 1005]
        assert abs(int(source.read(1004)[0, 0, 0]) - 120) < 12
        assert abs(int(source.read(1002)[0, 0, 0]) - 40) < 12  # seek backwards


def test_unsupported_and_missing_media(tmp_path):
    with pytest.raises(MediaError):
        open_source(str(tmp_path / "notes.txt"))
    with pytest.raises(MediaError):
        open_source(str(tmp_path / "missing.####.png"))


def test_jpeg_proxy_is_downscaled_and_mask_png_roundtrips(tmp_path):
    rgb = np.zeros((400, 800, 3), np.uint8)
    assert write_jpeg(tmp_path / "p.jpg", rgb, max_size=200) == (200, 100)
    assert cv2.imread(str(tmp_path / "p.jpg")).shape == (100, 200, 3)
    alpha = np.zeros((10, 20), np.uint8)
    alpha[2:5, 3:9] = 255
    write_mask_png(tmp_path / "sub" / "m.png", alpha)
    assert (cv2.imread(str(tmp_path / "sub" / "m.png"), cv2.IMREAD_UNCHANGED) == alpha).all()
