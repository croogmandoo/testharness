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


def generate_diff_image(path1: str, path2: str, output_path: str, threshold: int = 10) -> Optional[float]:
    """Generate a visual diff heatmap overlay between path1 (baseline) and path2 (current).

    Changed pixels are highlighted in high-contrast magenta (#FF007F) on a dimmed
    grayscale background of the current image, and saved to output_path.
    Returns the diff ratio (0.0-1.0), or None on failure.
    """
    try:
        import os
        from PIL import Image, ImageChops, ImageEnhance
    except ImportError:
        return None
    try:
        img1 = Image.open(path1).convert("RGB")
        img2 = Image.open(path2).convert("RGB")
        if img1.size != img2.size:
            img2 = img2.resize(img1.size, Image.LANCZOS)

        diff = ImageChops.difference(img1, img2)
        total_pixels = img1.size[0] * img1.size[1]
        raw_diff = diff.tobytes()
        bands = len(diff.getbands())

        # Create dimmed grayscale base from img2
        base = ImageEnhance.Color(img2).enhance(0.2)
        base = ImageEnhance.Brightness(base).enhance(0.7)
        base_pixels = bytearray(base.tobytes())

        changed_count = 0
        magenta = (255, 0, 128)
        for i in range(0, len(raw_diff), bands):
            if max(raw_diff[i:i + bands]) > threshold:
                changed_count += 1
                base_pixels[i] = magenta[0]
                base_pixels[i + 1] = magenta[1]
                base_pixels[i + 2] = magenta[2]

        out_img = Image.frombytes("RGB", img1.size, bytes(base_pixels))
        os.makedirs(os.path.dirname(output_path), exist_ok=True)
        out_img.save(output_path, "PNG")
        return round(changed_count / total_pixels, 4)
    except Exception:
        return None
