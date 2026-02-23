#!/usr/bin/env python3
import argparse
import json
import uuid
from datetime import datetime
from pathlib import Path

from PIL import Image, ImageFilter, ImageEnhance, ImageDraw, ImageFont
import numpy as np
import cv2
import imagehash


def load_image(path: Path) -> Image.Image:
    return Image.open(path).convert("RGB")


def enhance_image(pil: Image.Image) -> Image.Image:
    # Slight sharpening + color enhancement + denoise via OpenCV
    img = np.array(pil)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    # Denoise
    img = cv2.fastNlMeansDenoisingColored(img, None, 10, 10, 7, 21)
    # Convert to LAB and equalize L channel
    lab = cv2.cvtColor(img, cv2.COLOR_BGR2LAB)
    l, a, b = cv2.split(lab)
    l = cv2.equalizeHist(l)
    lab = cv2.merge((l, a, b))
    img = cv2.cvtColor(lab, cv2.COLOR_LAB2BGR)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil2 = Image.fromarray(img)
    # Slight sharpening
    pil2 = pil2.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=3))
    # Color boost
    enhancer = ImageEnhance.Color(pil2)
    pil2 = enhancer.enhance(1.1)
    return pil2


def stylize_image(pil: Image.Image,
                  sigma_s: float = 60,
                  sigma_r: float = 0.4,
                  bilateral_iter: int = 2,
                  bilateral_d: int = 9,
                  bilateral_sigmaColor: int = 75,
                  bilateral_sigmaSpace: int = 75,
                  color_boost: float = 1.1,
                  edge_canny: tuple = (100, 200),
                  edge_strength: float = 0.5) -> Image.Image:
    # Create a stylized digital look by smoothing and adding hand-drawn edges
    img = np.array(pil)
    img = cv2.cvtColor(img, cv2.COLOR_RGB2BGR)
    # Edge-preserving filter
    img = cv2.edgePreservingFilter(img, flags=1, sigma_s=float(sigma_s), sigma_r=float(sigma_r))
    # Bilateral for painterly effect
    for _ in range(int(bilateral_iter)):
        img = cv2.bilateralFilter(img, d=int(bilateral_d), sigmaColor=int(bilateral_sigmaColor), sigmaSpace=int(bilateral_sigmaSpace))
    # Add outlines
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    edges = cv2.Canny(gray, edge_canny[0], edge_canny[1])
    edges = cv2.cvtColor(edges, cv2.COLOR_GRAY2BGR)
    # Darken edges with adjustable strength
    darken = (edges.astype(np.float32) * (edge_strength))
    img = img.astype(np.float32) - darken
    img = np.clip(img, 0, 255).astype(np.uint8)
    img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
    pil_out = Image.fromarray(img)
    # Color boost and slight sharpening
    enhancer = ImageEnhance.Color(pil_out)
    pil_out = enhancer.enhance(float(color_boost))
    pil_out = pil_out.filter(ImageFilter.UnsharpMask(radius=1, percent=120, threshold=2))
    return pil_out


def vectorize_to_svg(pil: Image.Image, svg_path: Path):
    # Convert to grayscale then binary, find contours and build a simple SVG
    arr = np.array(pil.convert("L"))
    # Adaptive threshold for varied lighting
    th = cv2.adaptiveThreshold(arr, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                               cv2.THRESH_BINARY, 15, 9)
    # Find contours
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    h, w = th.shape
    svg_lines = []
    svg_lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" ' \
                     f'width="{w}" height="{h}">')
    svg_lines.append('<rect width="100%" height="100%" fill="white"/>')
    # For each contour, create a path (approximate to reduce points)
    for cnt in contours:
        if cv2.contourArea(cnt) < 100:  # ignore tiny specks
            continue
        epsilon = 0.005 * cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, epsilon, True)
        pts = approx.reshape(-1, 2)
        path = 'M ' + ' L '.join(f'{x} {y}' for x, y in pts) + ' Z'
        svg_lines.append(f'<path d="{path}" fill="black" stroke="none"/>')
    svg_lines.append('</svg>')
    svg_path.write_text('\n'.join(svg_lines), encoding='utf-8')


def vectorize_with_potrace(pil: Image.Image, svg_path: Path):
    """Attempt higher-quality tracing using the potrace Python bindings if available.
    Falls back to `vectorize_to_svg` if potrace isn't installed.
    """
    try:
        import potrace
    except Exception:
        # potrace not available; fallback
        vectorize_to_svg(pil, svg_path)
        return

    arr = np.array(pil.convert("L"))
    # binarize
    _, bw = cv2.threshold(arr, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    # potrace expects 1-bit bitmap
    bmp = potrace.Bitmap(bw)
    path = bmp.trace()
    h, w = bw.shape
    svg_lines = []
    svg_lines.append(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {w} {h}" width="{w}" height="{h}">')
    svg_lines.append('<rect width="100%" height="100%" fill="white"/>')
    for curve in path:
        d = []
        for segment in curve:
            if segment.is_corner:
                c = segment.c
                d.append(f'L {c.x} {c.y}')
            else:
                c1, c2 = segment.c1, segment.c2
                d.append(f'C {c1.x} {c1.y} {c2.x} {c2.y} {segment.end_point.x} {segment.end_point.y}')
        if d:
            svg_lines.append(f'<path d="M {curve.start_point.x} {curve.start_point.y} ' + ' '.join(d) + ' Z' + '" fill="black" stroke="none"/>')
    svg_lines.append('</svg>')
    svg_path.write_text('\n'.join(svg_lines), encoding='utf-8')


def add_watermark(pil: Image.Image, text: str) -> Image.Image:
    out = pil.copy()
    draw = ImageDraw.Draw(out)
    try:
        font = ImageFont.truetype("arial.ttf", 20)
    except Exception:
        font = ImageFont.load_default()
    w, h = out.size
    margin = 10
    # compute text size in a way compatible with newer Pillow versions
    try:
        bbox = draw.textbbox((0, 0), text, font=font)
        text_w = bbox[2] - bbox[0]
        text_h = bbox[3] - bbox[1]
    except Exception:
        try:
            text_w, text_h = font.getsize(text)
        except Exception:
            text_w, text_h = (len(text) * 6, 10)

    pos = (w - text_w - margin, h - text_h - margin)
    # simple rectangle (no alpha) behind the text for readability
    rect_pos = (pos[0] - 6, pos[1] - 4, pos[0] + text_w + 6, pos[1] + text_h + 4)
    draw.rectangle(rect_pos, fill=(255, 255, 255))
    draw.text(pos, text, fill=(0, 0, 0), font=font)
    return out


def compute_phash(pil: Image.Image) -> str:
    return str(imagehash.phash(pil))


def remove_background(pil: Image.Image) -> Image.Image:
    """Remove background from image using rembg library, keeping subject with transparency."""
    try:
        from rembg import remove
        # Convert to RGB, remove background, return with alpha
        rgb_img = pil.convert("RGB")
        result = remove(rgb_img)
        return result
    except ImportError:
        # Fallback if rembg not available
        return pil.convert("RGBA")
    except Exception as e:
        print(f"Background removal failed: {e}, returning original")
        return pil.convert("RGBA")


def soft_proof_cmyk(pil: Image.Image) -> Image.Image:
    """Simulate CMYK color gamut to show how design will actually print on cotton."""
    rgb_array = np.array(pil.convert("RGB"))
    # Convert RGB to CMYK-like desaturation to simulate print limitations
    rgb_normalized = rgb_array.astype(float) / 255.0
    
    # Reduce saturation by ~20% and reduce brightness slightly to simulate dye absorption
    hsv = cv2.cvtColor((rgb_normalized * 255).astype(np.uint8), cv2.COLOR_RGB2HSV).astype(float)
    hsv[:, :, 1] *= 0.75  # Reduce saturation (S channel)
    hsv[:, :, 2] *= 0.92  # Reduce brightness slightly (V channel)
    hsv = np.clip(hsv, 0, 255).astype(np.uint8)
    
    proofed = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    return Image.fromarray(proofed)


def calculate_max_print_size(img_width_px: int, img_height_px: int, dpi: int = 300) -> tuple:
    """Calculate max print size at given DPI without quality loss. Returns (width_inches, height_inches)."""
    # At 300 DPI, 1 inch = 300 pixels
    width_inches = img_width_px / dpi
    height_inches = img_height_px / dpi
    return (width_inches, height_inches)


def adjust_warmth(pil: Image.Image, warmth: float = 0.0) -> Image.Image:
    """
    Adjust image warmth (-100 to +100).
    Negative = cooler/bluer, Positive = warmer/more yellow-orange.
    """
    if warmth == 0:
        return pil
    
    # Convert to HSV to adjust hue
    img = np.array(pil.convert("RGB"))
    hsv = cv2.cvtColor(img, cv2.COLOR_RGB2HSV).astype(float)
    
    # Adjust hue channel for warmth (0-180 in OpenCV HSV)
    # Warmer colors (reds/yellows) are in 0-30 range
    warmth_normalized = warmth / 100.0 * 15  # Scale to hue range
    hsv[:, :, 0] = (hsv[:, :, 0] + warmth_normalized) % 180
    
    hsv = np.clip(hsv, 0, 255).astype(np.uint8)
    result = cv2.cvtColor(hsv, cv2.COLOR_HSV2RGB)
    return Image.fromarray(result)


def adjust_contrast(pil: Image.Image, contrast: float = 0.0) -> Image.Image:
    """
    Adjust image contrast (-100 to +100).
    Negative = flatter, Positive = more punchy/contrasty.
    """
    if contrast == 0:
        return pil
    
    # Map -100 to +100 to enhancement range 0.5 to 2.0
    enhancement_factor = 1.0 + (contrast / 100.0)
    enhancer = ImageEnhance.Contrast(pil)
    return enhancer.enhance(enhancement_factor)


def process_image(input_path: Path, output_dir: Path, keep_svg: bool = True, stylize_opts: dict = None, hq_vector: bool = False):
    output_dir.mkdir(parents=True, exist_ok=True)
    img = load_image(input_path)
    enhanced = enhance_image(img)
    if stylize_opts is None:
        stylize_opts = {}
    stylized = stylize_image(enhanced, **stylize_opts)

    # Create unique id and metadata
    uid = str(uuid.uuid4())
    phash = compute_phash(stylized)
    timestamp = datetime.utcnow().isoformat() + 'Z'

    # Save outputs
    base = output_dir / (input_path.stem + "")
    enhanced_path = output_dir / f"{input_path.stem}_enhanced.png"
    stylized_path = output_dir / f"{input_path.stem}_stylized.png"
    svg_path = output_dir / f"{input_path.stem}.svg"

    # Watermark the stylized image with UID
    stamped = add_watermark(stylized, uid)
    stamped.save(stylized_path, optimize=True)
    enhanced.save(enhanced_path, optimize=True)

    if keep_svg:
        if hq_vector:
            try:
                vectorize_with_potrace(enhanced, svg_path)
            except Exception:
                vectorize_to_svg(enhanced, svg_path)
        else:
            vectorize_to_svg(enhanced, svg_path)

    metadata = {
        "id": uid,
        "input_file": str(input_path.name),
        "generated": timestamp,
        "phash": phash,
        "outputs": {
            "enhanced": str(enhanced_path.name),
            "stylized": str(stylized_path.name),
            "svg": str(svg_path.name) if keep_svg else None
        }
    }
    meta_path = output_dir / f"{input_path.stem}_metadata.json"
    meta_path.write_text(json.dumps(metadata, indent=2), encoding='utf-8')
    return {
        "enhanced": enhanced_path,
        "stylized": stylized_path,
        "svg": svg_path if keep_svg else None,
        "metadata": meta_path
    }


def main():
    parser = argparse.ArgumentParser(description="Convert a photo of art into a clean digital and unique asset.")
    parser.add_argument("input", help="Path to input image file")
    parser.add_argument("-o", "--output", help="Output directory", default="output")
    parser.add_argument("--no-svg", action="store_true", help="Skip SVG/vector output")
    args = parser.parse_args()
    inp = Path(args.input)
    out = Path(args.output)
    if not inp.exists():
        print("Input file not found:", inp)
        return
    results = process_image(inp, out, keep_svg=not args.no_svg)
    print("Generated:")
    for k, v in results.items():
        print(f" - {k}: {v}")


if __name__ == '__main__':
    main()
