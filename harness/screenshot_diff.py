from typing import Optional


def compute_diff(path1: str, path2: str, threshold: int = 10) -> float:
    """
    Returns fraction of pixels that differ significantly (0.0–1.0).
    Returns 0.0 if Pillow is not installed or either image fails to load.
    threshold: minimum per-channel delta to count a pixel as changed.
    """
    try:
        from PIL import Image, ImageChops
    except ImportError:
        return 0.0
    try:
        img1 = Image.open(path1).convert("RGB")
        img2 = Image.open(path2).convert("RGB")
        if img1.size != img2.size:
            img2 = img2.resize(img1.size, Image.LANCZOS)
        diff = ImageChops.difference(img1, img2)
        total_pixels = img1.size[0] * img1.size[1]
        # Use .tobytes() (stable API) over .getdata() — getdata is deprecated
        # in Pillow 10.x for removal in Pillow 14 (2027-10-15).  We fetched
        # 3-channel RGB above so each pixel is exactly 3 bytes.
        raw = diff.tobytes()
        bands = len(diff.getbands())  # 3 for RGB
        changed = sum(
            1 for i in range(0, len(raw), bands)
            if max(raw[i:i + bands]) > threshold
        )
        return round(changed / total_pixels, 4)
    except Exception:
        return 0.0
