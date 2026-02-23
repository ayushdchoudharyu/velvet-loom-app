import io
import zipfile
from pathlib import Path
import tempfile

import streamlit as st
from PIL import Image, ImageDraw, ImageFont
import uuid
import random
import json
from datetime import datetime
import csv

from pil_art_pipeline import process_image, stylize_image, enhance_image, compute_phash, remove_background, soft_proof_cmyk, calculate_max_print_size, adjust_warmth, adjust_contrast


st.set_page_config(page_title="Madhubani Photo → Digital Art", layout="wide")


def create_checkerboard_background(pil_rgba: Image.Image, checker_size: int = 20) -> Image.Image:
    """Create a checkerboard background behind transparent image for inspection."""
    img_w, img_h = pil_rgba.size
    checker = Image.new("RGB", (img_w, img_h), (255, 255, 255))
    pixels = checker.load()
    
    # Create checkerboard pattern (gray and light gray)
    for y in range(img_h):
        for x in range(img_w):
            if ((x // checker_size) + (y // checker_size)) % 2 == 0:
                pixels[x, y] = (200, 200, 200)
            else:
                pixels[x, y] = (220, 220, 220)
    
    # Composite the RGBA image on top
    if pil_rgba.mode == "RGBA":
        checker.paste(pil_rgba, (0, 0), pil_rgba)
    else:
        checker.paste(pil_rgba, (0, 0))
    return checker


def display_side_by_side(col1, col2, img1, img2, label1: str, label2: str):
    """Display two images side-by-side with labels."""
    with col1:
        st.image(img1, caption=label1, use_container_width=True)
    with col2:
        st.image(img2, caption=label2, use_container_width=True)


def make_transparent(pil_img: Image.Image, tolerance: int = 240) -> Image.Image:
    rgba = pil_img.convert("RGBA")
    datas = rgba.getdata()
    new_data = []
    for item in datas:
        r, g, b, a = item
        if r >= tolerance and g >= tolerance and b >= tolerance:
            new_data.append((255, 255, 255, 0))
        else:
            new_data.append((r, g, b, a))
    rgba.putdata(new_data)
    return rgba


def add_batch_watermark(pil_img: Image.Image, text: str, color: str = "#d4af37") -> Image.Image:
    out = pil_img.convert("RGBA")
    txt = Image.new("RGBA", out.size, (255, 255, 255, 0))
    draw = ImageDraw.Draw(txt)
    try:
        font = ImageFont.truetype("arial.ttf", max(14, out.size[0] // 30))
    except Exception:
        font = ImageFont.load_default()
    bbox = draw.textbbox((0, 0), text, font=font)
    text_w = bbox[2] - bbox[0]
    text_h = bbox[3] - bbox[1]
    margin = 12
    pos = (out.size[0] - text_w - margin, out.size[1] - text_h - margin)
    # semi-transparent text
    draw.text(pos, text, fill=color + "AA", font=font)
    return Image.alpha_composite(out, txt)


def create_garment_mockup(design_img: Image.Image, placement: str, shirt_color: tuple = (200, 200, 200)) -> Image.Image:
    """Create a garment mockup with design placed on back, front, or pocket."""
    # Create a garment silhouette with customizable shirt color
    mockup = Image.new("RGB", (800, 1000), shirt_color)
    draw = ImageDraw.Draw(mockup)
    
    # Adjust outline color based on shirt brightness
    brightness = (shirt_color[0] + shirt_color[1] + shirt_color[2]) / 3
    outline_color = (50, 50, 50) if brightness > 128 else (200, 200, 200)
    lighter_shade = tuple(min(255, c + 30) for c in shirt_color)
    
    # Draw t-shirt outline (simple rectangle with sleeve nubs)
    # Body
    draw.rectangle([100, 150, 700, 900], fill=lighter_shade, outline=outline_color, width=3)
    # Left sleeve
    draw.rectangle([20, 200, 100, 400], fill=lighter_shade, outline=outline_color, width=3)
    # Right sleeve
    draw.rectangle([700, 200, 780, 400], fill=lighter_shade, outline=outline_color, width=3)
    # Neck opening (small circle)
    draw.ellipse([350, 130, 450, 170], fill=shirt_color, outline=outline_color, width=2)
    
    # Resize design to fit placement area
    if placement == "back":
        # Center back print (large)
        design_resized = design_img.resize((400, 500), Image.LANCZOS)
        x_pos = (800 - 400) // 2
        y_pos = 250
        label_y = 800
    elif placement == "front":
        # Center front print (large)
        design_resized = design_img.resize((400, 500), Image.LANCZOS)
        x_pos = (800 - 400) // 2
        y_pos = 250
        label_y = 800
    else:  # pocket
        # Small pocket print (top-right area)
        design_resized = design_img.resize((120, 140), Image.LANCZOS)
        x_pos = 550
        y_pos = 280
        label_y = 500
    
    # Paste design onto mockup
    if design_resized.mode == "RGBA":
        mockup.paste(design_resized, (x_pos, y_pos), design_resized)
    else:
        mockup.paste(design_resized, (x_pos, y_pos))
    
    # Add label text
    draw.text((400, label_y), placement.upper() + " PRINT", fill=(50, 50, 50), font=ImageFont.load_default())
    
    return mockup


def generate_repeating_pattern(design_img: Image.Image, tile_size: int = 200, num_tiles: int = 6) -> Image.Image:
    """Create an all-over repeating pattern from the design for sleeves/side panels."""
    # Create a pattern by tiling the design element
    import random
    
    # Extract a smaller element (top-left quadrant) as the repeating unit
    w, h = design_img.size
    tile_crop = design_img.crop((0, 0, w//2, h//2))
    tile_crop = tile_crop.resize((tile_size, tile_size), Image.LANCZOS)
    
    # Create large canvas and tile the pattern with slight rotation/offset for naturalistic look
    canvas_size = tile_size * num_tiles
    pattern = Image.new("RGBA", (canvas_size, canvas_size), (255, 255, 255, 0))
    
    for row in range(num_tiles):
        for col in range(num_tiles):
            x = col * tile_size
            y = row * tile_size
            # Slight random offset for organic feel
            offset_x = random.randint(-10, 10)
            offset_y = random.randint(-10, 10)
            
            try:
                pattern.paste(tile_crop, (x + offset_x, y + offset_y), tile_crop)
            except:
                # If paste fails, just place without alpha
                pattern.paste(tile_crop, (x + offset_x, y + offset_y))
    
    return pattern


def process_and_package(files, batch_start: int, gold_color: str, make_png_transparent: bool, dtg_preset: str, make_unique: bool=False, variants_per_image:int=1, uniqueness_strength:int=5, hq_vector:bool=False, remove_bg: bool=False, show_soft_proof: bool=False):
    out_dir = Path("output/streamlit")
    out_dir.mkdir(parents=True, exist_ok=True)
    results = []
    idx = batch_start
    # DTG presets: name -> (width, height, dpi)
    presets = {
        "4500x5400_300dpi": (4500, 5400, 300),
        "3200x3200_300dpi": (3200, 3200, 300),
        "2000x2000_300dpi": (2000, 2000, 300)
    }
    target = presets.get(dtg_preset, presets["4500x5400_300dpi"])
    target_w, target_h, target_dpi = target

    for uploaded in files:
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tf:
            tf.write(uploaded.getbuffer())
            tf.flush()
            inp_path = Path(tf.name)
            # run base pipeline (enhance + base stylize + svg)
            res = process_image(inp_path, out_dir, keep_svg=True, hq_vector=hq_vector)
            stylized_path = res["stylized"]
            # load enhanced for variants
            enhanced_path = res.get("enhanced")
            base_img = Image.open(stylized_path).convert("RGBA")
            
            # Apply background removal if requested
            if remove_bg:
                base_img_rgb = Image.open(stylized_path).convert("RGB")
                base_img = remove_background(base_img_rgb)
            
            # Apply soft proofing if requested
            if show_soft_proof:
                base_img_rgb = base_img.convert("RGB") if base_img.mode != "RGB" else base_img
                base_img_proofed = soft_proof_cmyk(base_img_rgb)
                base_img = base_img_proofed.convert("RGBA")
            
            # always save the base stylized as a result
            watermark_text = f"Batch#{idx}"
            img = add_batch_watermark(base_img, watermark_text, gold_color)
            # DTG resize for base
            img = img.convert("RGBA")
            img_w, img_h = img.size
            scale = min(target_w / img_w, target_h / img_h)
            new_w = int(img_w * scale)
            new_h = int(img_h * scale)
            img_resized = img.resize((new_w, new_h), resample=Image.LANCZOS)
            canvas = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
            canvas.paste(img_resized, ((target_w - new_w)//2, (target_h - new_h)//2), img_resized)
            if make_png_transparent:
                canvas = make_transparent(canvas)
            out_name = f"{Path(uploaded.name).stem}_batch{idx}_{dtg_preset}.png"
            save_path = out_dir / out_name
            canvas.convert("RGB").save(save_path, optimize=True, dpi=(target_dpi, target_dpi))
            results.append(save_path)

            # generate unique variants if requested
            if make_unique:
                # prepare enhanced source if available, else use base stylized as input
                if enhanced_path and Path(enhanced_path).exists():
                    source_img = Image.open(enhanced_path).convert("RGB")
                else:
                    source_img = Image.open(stylized_path).convert("RGB")
                for v in range(variants_per_image):
                    # randomized stylize params influenced by uniqueness_strength
                    s = uniqueness_strength
                    opts = {
                        "color_boost": max(0.8, 1.0 + (random.uniform(-0.2, 0.4) * s/10)),
                        "edge_strength": max(0.1, min(1.0, 0.3 + random.uniform(-0.2, 0.6) * s/10)),
                        "bilateral_iter": max(1, min(4, int(1 + round(random.uniform(0, 3) * s/10))))
                    }
                    variant_img = stylize_image(enhance_image(source_img), **opts).convert("RGBA")
                    uid = str(uuid.uuid4())
                    variant_img = add_batch_watermark(variant_img, uid, gold_color)
                    # DTG resize
                    iw, ih = variant_img.size
                    sc = min(target_w / iw, target_h / ih)
                    nw = int(iw * sc)
                    nh = int(ih * sc)
                    vimg = variant_img.resize((nw, nh), resample=Image.LANCZOS)
                    canvas_v = Image.new("RGBA", (target_w, target_h), (255, 255, 255, 255))
                    canvas_v.paste(vimg, ((target_w - nw)//2, (target_h - nh)//2), vimg)
                    if make_png_transparent:
                        canvas_v = make_transparent(canvas_v)
                    vname = f"{Path(uploaded.name).stem}_batch{idx}_var{v+1}_{dtg_preset}_{uid[:8]}.png"
                    vpath = out_dir / vname
                    canvas_v.convert("RGB").save(vpath, optimize=True, dpi=(target_dpi, target_dpi))
                    # write simple metadata for the variant
                    phash = compute_phash(canvas_v.convert("RGB"))
                    meta = {
                        "id": uid,
                        "input_file": str(uploaded.name),
                        "generated": datetime.utcnow().isoformat() + 'Z',
                        "phash": phash,
                        "outputs": {"variant": vname}
                    }
                    (out_dir / f"{Path(vname).stem}_metadata.json").write_text(json.dumps(meta, indent=2), encoding='utf-8')
                    results.append(vpath)
            idx += 1
    # zip results
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in results:
            zf.write(p, arcname=p.name)
    zip_buffer.seek(0)
    # build CSV manifest
    manifest_path = build_manifest(out_dir, results, dtg_preset)
    return results, zip_buffer, manifest_path


def build_manifest(out_dir: Path, produced_paths: list, dtg_preset: str, price: str = "", title: str = "", description: str = "", tags: str = "") -> Path:
    rows = []
    for p in produced_paths:
        name = Path(p).name
        stem = Path(p).stem
        uid = ""
        phash = ""
        # look for metadata files matching the stem
        meta_files = list(out_dir.glob(f"{stem}_metadata.json"))
        if not meta_files:
            # try variants naming
            prefix = stem.split("_batch")[0]
            meta_files = list(out_dir.glob(f"{prefix}*_metadata.json"))
        if meta_files:
            try:
                m = json.loads(meta_files[0].read_text(encoding='utf-8'))
                uid = m.get('id', '')
                phash = m.get('phash', '')
            except Exception:
                pass
        if not uid:
            uid = str(uuid.uuid4())
        if not phash:
            try:
                phash = compute_phash(Image.open(p).convert("RGB"))
            except Exception:
                phash = ""
        rows.append({
            "filename": name,
            "uuid": uid,
            "phash": phash,
            "dtg_preset": dtg_preset,
            "price": price,
            "title": title,
            "description": description,
            "tags": tags
        })
    manifest_path = out_dir / "manifest.csv"
    with open(manifest_path, 'w', newline='', encoding='utf-8') as cf:
        writer = csv.DictWriter(cf, fieldnames=['filename', 'uuid', 'phash', 'dtg_preset', 'price', 'title', 'description', 'tags'])
        writer.writeheader()
        for r in rows:
            writer.writerow(r)
    return manifest_path


st.title("Madhubani Photo → Digital Print Assets")
st.markdown("Upload phone photos of Madhubani art — the app will denoise, stylize, vectorize, watermark, and produce print-ready PNGs.")

with st.sidebar:
    st.header("Batch Options")
    batch_start = st.slider("Starting batch number", min_value=1, max_value=1000, value=1)
    gold_color = st.color_picker("Gold color", value="#d4af37")
    transparent = st.checkbox("Make white background transparent (for DTG)", value=False)
    dtg_preset = st.selectbox("DTG export preset", options=["4500x5400_300dpi", "3200x3200_300dpi", "2000x2000_300dpi"], index=0)
    print_placement = st.selectbox("Preview print placement", options=["back", "front", "pocket"], index=0, help="Choose where to show design on garment mockup")
    
    # Quality & Print Options
    st.markdown("---")
    st.subheader("Print Quality Options")
    remove_bg = st.checkbox("Remove background (isolate design)", value=False, help="Use AI to remove background for luxury 'no-sticker' look")
    show_soft_proof = st.checkbox("Show soft proof (print preview)", value=False, help="Simulate actual CMYK cotton print colors")
    
    run_button = st.button("Process Uploads")
    # optional high-quality vector
    hq_vector = st.checkbox("High-quality vector (Potrace)", value=False)
    make_unique = st.checkbox("Make unique variants automatically", value=False)
    variants_per_image = st.slider("Variants per image", 1, 10, 3, key="sb_variants_per_image")
    uniqueness_strength = st.slider("Uniqueness strength", 1, 10, 5, key="sb_uniqueness_strength")
    # allow user to upload a custom madhubani processor
    st.markdown("---")
    st.write("Optional: upload `madhubani_pro.py` to use your own processing function (must expose `process_image`)")
    custom_proc = st.file_uploader("Upload madhubani_pro.py", type=["py"], key="madhubani_upload")
    integrate_custom = False
    if custom_proc is not None:
        saved = Path("madhubani_pro.py")
        with open(saved, "wb") as f:
            f.write(custom_proc.getbuffer())
        st.success("Uploaded madhubani_pro.py — it will be used for processing (if compatible).")
        integrate_custom = True
    # telemetry opt-out helper
    st.markdown("---")
    if st.button("Disable Streamlit telemetry for this user"):
        cfgDir = Path.home() / ".streamlit"
        cfgDir.mkdir(parents=True, exist_ok=True)
        (cfgDir / "config.toml").write_text('[browser]\n    gatherUsageStats = false\n', encoding='utf-8')
        st.success("Telemetry opt-out written to ~/.streamlit/config.toml")
    # prepare repo for Streamlit Cloud
    if st.button("Prepare repo files for Streamlit Cloud"):
        # write a minimal README and instructions
        Path("DEPLOY_STREAMLIT.md").write_text("Deploy this repo to Streamlit Cloud by pushing to GitHub, then selecting 'New app' and pointing to this repo. Ensure requirements.txt contains Streamlit.")
        st.success("Wrote DEPLOY_STREAMLIT.md — push the repo to GitHub to deploy.")

uploaded_files = st.file_uploader("Upload phone photos (multiple allowed)", accept_multiple_files=True, type=["png", "jpg", "jpeg"])
if 'processed' not in st.session_state:
    st.session_state.processed = {}

# If files were uploaded, auto-run the digital conversion and produce stylized outputs
if uploaded_files:
    # reset processed if file list changed
    current_names = [f.name for f in uploaded_files]
    if set(current_names) != set(st.session_state.processed.keys()):
        st.session_state.processed = {}
    with st.spinner("Digitizing and refining uploads — doing denoise, color-correct, stylize"):
        for uploaded in uploaded_files:
            if uploaded.name in st.session_state.processed:
                continue
            with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tf:
                tf.write(uploaded.getbuffer())
                tf.flush()
                inp_path = Path(tf.name)
            # run the full pipeline (default stylize)
            res = process_image(inp_path, Path("output/streamlit"), keep_svg=True)
            # load stylized preview
            stylized_img = Image.open(res["stylized"]).convert("RGBA")
            st.session_state.processed[uploaded.name] = {
                "res": res,
                "stylized_img": stylized_img,
                "input_name": uploaded.name
            }

    # show the generated digital/stylized outputs first
    st.header("Digitalized results (auto-generated)")
    
    # Color grading controls (persistent for all images)
    st.subheader("🎨 Artist Color Grading Controls")
    col_warmth, col_contrast = st.columns(2)
    with col_warmth:
        warmth_adjust = st.slider("Warmth", -100, 100, 0, help="Negative = cooler/bluer, Positive = warmer/yellow")
    with col_contrast:
        contrast_adjust = st.slider("Contrast", -100, 100, 0, help="Negative = flatter, Positive = punchier")
    
    # Display results for each uploaded image
    for name in st.session_state.processed:
        entry = st.session_state.processed[name]
        stylized_rgb = entry["stylized_img"].convert("RGB")
        
        # Apply color grading if adjusted
        if warmth_adjust != 0:
            stylized_rgb = adjust_warmth(stylized_rgb, warmth_adjust)
        if contrast_adjust != 0:
            stylized_rgb = adjust_contrast(stylized_rgb, contrast_adjust)
        
        st.subheader(f"📸 {name}")
        
        # Get image metrics
        w, h = stylized_rgb.size
        max_w_300dpi, max_h_300dpi = calculate_max_print_size(w, h, 300)
        w_300dpi, h_300dpi = calculate_max_print_size(w, h, 300)
        st.caption(f"Resolution: {w}×{h}px | At 300 DPI: {w_300dpi:.1f}\" × {h_300dpi:.1f}\" | Export: Exporting at 300 DPI for professional print")
        
        # Create two-column layout for before/after options
        col_digital, col_proofs = st.columns(2)
        
        with col_digital:
            st.subheader("Digital Version")
            st.image(stylized_rgb, caption="Screen-ready version (vibrant)", use_container_width=True)
            buf = io.BytesIO()
            stylized_rgb.save(buf, format="PNG")
            st.download_button(label="Download Digital", data=buf.getvalue(), file_name=f"{Path(name).stem}_digital.png", mime="image/png", key=f"digital_{name}")
        
        with col_proofs:
            st.subheader("Print Proofs")
            
            # Soft Proof (CMYK simulation)
            if show_soft_proof:
                st.write("**Soft Proof** (CMYK colors on cotton)")
                proofed = soft_proof_cmyk(stylized_rgb)
                st.image(proofed, caption="How it will actually print", use_container_width=True)
                st.caption("Colors duller due to fabric dye absorption", help="This is a simulation of how colors look on cotton fabric")
                buf_proof = io.BytesIO()
                proofed.save(buf_proof, format="PNG")
                st.download_button(label="Download Soft Proof", data=buf_proof.getvalue(), file_name=f"{Path(name).stem}_softproof.png", mime="image/png", key=f"proof_{name}")
            else:
                st.info("Toggle 'Show soft proof' in sidebar to preview actual print colors")
        
        # Background removal with inspection
        if remove_bg:
            st.subheader("🔍 Mask Inspection - Background Removed")
            try:
                no_bg = remove_background(stylized_rgb)
                # Show on checkerboard for edge inspection
                nobg_checked = create_checkerboard_background(no_bg)
                st.image(nobg_checked, caption="Design on checkerboard (verify edges are clean)", use_container_width=True)
                st.caption("Checkerboard shows transparency - verify peacock edges are clean and not cut")
                buf_nobg = io.BytesIO()
                no_bg.save(buf_nobg, format="PNG")
                st.download_button(label="Download No-Background (PNG with transparency)", data=buf_nobg.getvalue(), file_name=f"{Path(name).stem}_nobg.png", mime="image/png", key=f"nobg_{name}")
            except Exception as e:
                st.error(f"Background removal failed: {e}")

    st.markdown("---")

    # Garment Preview (t-shirt mockup)
    st.subheader("👕 Garment Preview - See design placement on T-shirt")
    
    # T-shirt color picker
    shirt_color_hex = st.color_picker("Choose T-Shirt Color", value="#c8c8c8", help="Test how your transparent PNG looks on dark (black, maroon) vs. light fabrics")
    # Convert hex to RGB tuple
    shirt_color_rgb = tuple(int(shirt_color_hex.lstrip('#')[i:i+2], 16) for i in (0, 2, 4))
    
    try:
        sample_name = list(st.session_state.processed.keys())[0]
        sample_entry = st.session_state.processed[sample_name]
        sample_design = sample_entry["stylized_img"].convert("RGB")
        
        # Create mockup for each placement option
        preview_cols = st.columns(3)
        placements = ["back", "front", "pocket"]
        
        for col, placement in zip(preview_cols, placements):
            with col:
                mockup = create_garment_mockup(sample_design, placement, shirt_color=shirt_color_rgb)
                st.image(mockup, caption=f"{placement.upper()} PRINT", use_container_width=True)
    except Exception as e:
        st.error(f"Unable to generate garment preview: {e}")

    st.markdown("---")
    
    # Pattern generator - LUXURY FEATURE (make prominent)
    st.subheader("✨ All-Over Pattern Generator (Luxury Feature)")
    st.markdown("""
    **High-Fashion Positioning**: Madhubani motifs are perfect for seamless repeating patterns.  
    Use this for:
    - Sleeve prints with repeating peacocks  
    - Side panel coverage  
    - Full fabric prints (sarees, bedsheets)  
    - Premium streetwear designs with tiling  
    """)
    
    col_pattern_size, col_pattern_tiles = st.columns(2)
    with col_pattern_size:
        pattern_tile_size = st.slider("Pattern tile size (px)", 100, 400, 200, key="pattern_tile_size")
    with col_pattern_tiles:
        pattern_num_tiles = st.slider("Number of tiles (3×3 to 10×10)", 3, 10, 6, key="pattern_num_tiles")
    
    col_gen_pattern, col_tile_preview = st.columns(2)
    with col_gen_pattern:
        if st.button("Generate All-Over Print Pattern", key="gen_pattern"):
            try:
                sample_name = list(st.session_state.processed.keys())[0]
                sample_entry = st.session_state.processed[sample_name]
                sample_design = sample_entry["stylized_img"].convert("RGB")
                
                pattern = generate_repeating_pattern(sample_design, tile_size=pattern_tile_size, num_tiles=pattern_num_tiles)
                st.image(pattern, caption=f"Repeating Pattern ({pattern_num_tiles}×{pattern_num_tiles} grid)", use_container_width=True)
                
                # Download pattern
                buf_pattern = io.BytesIO()
                pattern.save(buf_pattern, format="PNG")
                st.download_button(
                    label="📥 Download Full Pattern",
                    data=buf_pattern.getvalue(),
                    file_name=f"{Path(sample_name).stem}_allover_pattern.png",
                    mime="image/png"
                )
                st.success("✅ Pattern generated! Use for tiling on sleeves, side panels, or full-body prints.")
            except Exception as e:
                st.error(f"Pattern generation failed: {e}")
    
    with col_tile_preview:
        st.write("**Tile Preview (3×3 grid)**")
        if st.button("Preview as 3×3 Tile", key="preview_tile"):
            try:
                sample_name = list(st.session_state.processed.keys())[0]
                sample_entry = st.session_state.processed[sample_name]
                sample_design = sample_entry["stylized_img"].convert("RGB")
                
                # Generate small 3x3 preview
                preview_pattern = generate_repeating_pattern(sample_design, tile_size=150, num_tiles=3)
                st.image(preview_pattern, caption="3×3 Tile Preview", use_container_width=True)
            except Exception as e:
                st.error(f"Tile preview failed: {e}")

    st.markdown("---")

    # Design Variations
    st.subheader("🎨 Suggested Design Variations (Drastically Different Styles)")
    st.write("Choose from 5 completely different artistic interpretations of your design:")
    try:
        sample_name = list(st.session_state.processed.keys())[0]
        if not sample_name:
            st.warning("No images uploaded yet. Upload photos to see design variations.")
        else:
            sample_entry = st.session_state.processed[sample_name]
            if not isinstance(sample_entry, dict):
                st.error(f"Invalid data structure returned: {type(sample_entry)}. Expected dict.")
            else:
                sample_img = sample_entry["stylized_img"].convert("RGB")
                variants = []
                # Dramatically different design variations with wide parameter ranges
                params_grid = [
                    # V1: Bold, high-contrast, strong edges
                    {"sigma_s": 80, "sigma_r": 0.3, "bilateral_iter": 4, "color_boost": 1.7, "edge_canny": (40, 50), "edge_strength": 0.9},
                    # V2: Soft, watercolor-like, muted colors
                    {"sigma_s": 40, "sigma_r": 0.6, "bilateral_iter": 1, "color_boost": 0.8, "edge_canny": (140, 150), "edge_strength": 0.2},
                    # V3: Painterly, detailed edge work, vibrant
                    {"sigma_s": 70, "sigma_r": 0.4, "bilateral_iter": 3, "color_boost": 1.5, "edge_canny": (70, 80), "edge_strength": 0.7},
                    # V4: Minimalist, very smooth, faded colors
                    {"sigma_s": 30, "sigma_r": 0.7, "bilateral_iter": 2, "color_boost": 0.7, "edge_canny": (190, 200), "edge_strength": 0.15},
                    # V5: Graphic, heavy outlines, saturated colors
                    {"sigma_s": 60, "sigma_r": 0.35, "bilateral_iter": 5, "color_boost": 1.8, "edge_canny": (30, 40), "edge_strength": 0.95}
                ]
                style_names = ["🔥 Bold & Graphic", "🎨 Watercolor Soft", "🖼️ Painterly", "✨ Minimalist", "🖍️ Pop Art Heavy"]
                
                for i, (opts, style_name) in enumerate(zip(params_grid, style_names)):
                    enhanced_img = enhance_image(sample_img)
                    variant_img = stylize_image(enhanced_img, **opts)
                    variant_img = add_batch_watermark(variant_img, f"V{i+1}", gold_color)
                    variants.append((f"variant_{i+1}.png", variant_img, style_name))
                
                cols_v = st.columns(len(variants))
                for c, (name, im, style) in zip(cols_v, variants):
                    with c:
                        st.image(im, caption=style, use_container_width=True)
                        buf = io.BytesIO()
                im.convert("RGBA").save(buf, format="PNG")
                st.download_button(label=f"💾 Download", data=buf.getvalue(), file_name=name, mime="image/png", key=f"var_{name}")
    except Exception as e:
        st.error(f"Unable to generate design variations: {e}")

    st.markdown("---")

    # After auto-generation, show edit/export controls below
    st.header("Edit & Export (start from generated digital version)")

    # reuse uploaded_files order
    file_iter = uploaded_files

else:
    file_iter = []

# Batch export button (keeps previous behavior)
if run_button and uploaded_files:
    with st.spinner("Processing images — this may take a minute per photo"):
        # if custom processor available, try to import and use it
        custom_module = None
        if integrate_custom:
            try:
                import importlib.util
                spec = importlib.util.spec_from_file_location("madhubani_pro", "madhubani_pro.py")
                custom_module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(custom_module)
                st.info("Using uploaded madhubani_pro.py for processing (if compatible).")
            except Exception as e:
                st.warning(f"Failed to load madhubani_pro.py: {e}")
                custom_module = None

        results = []
        zipbuf = None
        manifest = None
        if custom_module and hasattr(custom_module, "process_image"):
            # attempt to use custom processor
            proc = custom_module.process_image
            for uploaded in uploaded_files:
                with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tf:
                    tf.write(uploaded.getbuffer())
                    tf.flush()
                    inp_path = Path(tf.name)
                try:
                    out = Path("output/streamlit")
                    out.mkdir(parents=True, exist_ok=True)
                    proc(inp_path, out, keep_svg=True)
                    # collect stylized if available
                    styl = out / f"{Path(uploaded.name).stem}_stylized.png"
                    if styl.exists():
                        results.append(styl)
                except Exception as e:
                    st.warning(f"Custom processor failed for {uploaded.name}: {e}")
            # build zip and manifest for custom results
            out = Path("output/streamlit")
            manifest = build_manifest(out, results, dtg_preset)
            zipbuf = io.BytesIO()
            with zipfile.ZipFile(zipbuf, "w", zipfile.ZIP_DEFLATED) as zf:
                for p in results:
                    zf.write(p, arcname=p.name)
            zipbuf.seek(0)
        else:
            results, zipbuf, manifest = process_and_package(uploaded_files, batch_start, gold_color, transparent, dtg_preset, make_unique, variants_per_image, uniqueness_strength, hq_vector, remove_bg, show_soft_proof)
    st.success(f"Processed {len(results)} images")
    cols = st.columns(2)
    for p in results:
        with cols[0]:
            st.image(str(p), caption=p.name, use_container_width=True)
        with cols[1]:
            with open(p, "rb") as f:
                data = f.read()
            st.download_button(label=f"Download {p.name}", data=data, file_name=p.name, mime="image/png")
    st.download_button("Download all as ZIP", data=zipbuf.getvalue(), file_name="madhubani_batch.zip", mime="application/zip")
    try:
        if manifest is not None and Path(manifest).exists():
            with open(manifest, "rb") as mf:
                mdata = mf.read()
            st.download_button("Download CSV manifest", data=mdata, file_name="manifest.csv", mime="text/csv")
    except Exception:
        pass

    st.markdown("### Suggested design variations")
    # generate extra quick variants for the first uploaded file to suggest more designs
    try:
        sample = uploaded_files[0]
        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(sample.name).suffix) as tf:
            tf.write(sample.getbuffer())
            tf.flush()
            sample_path = Path(tf.name)
        # build a small grid of parameter variations
        variants = []
        params_grid = [
            {"color_boost": 1.05, "edge_strength": 0.4, "bilateral_iter": 1},
            {"color_boost": 1.2, "edge_strength": 0.6, "bilateral_iter": 2},
            {"color_boost": 1.5, "edge_strength": 0.8, "bilateral_iter": 3},
            {"color_boost": 0.9, "edge_strength": 0.3, "bilateral_iter": 1},
            {"color_boost": 1.3, "edge_strength": 0.7, "bilateral_iter": 2}
        ]
        for i, opts in enumerate(params_grid):
            # run processing but only stylize (skip re-enhance to be faster)
            base = load_path = sample_path
            img0 = Image.open(load_path).convert("RGB")
            enhanced_img = enhance_image(img0)
            variant_img = stylize_image(enhanced_img, **opts)
            # watermark with variant index
            variant_img = add_batch_watermark(variant_img, f"V{i+1}", gold_color)
            variants.append((f"variant_{i+1}.png", variant_img))
        # display variants
        cols_v = st.columns(len(variants))
        for c, (name, im) in zip(cols_v, variants):
            with c:
                st.image(im, caption=name, use_container_width=True)
                buf = io.BytesIO()
                im.convert("RGBA").save(buf, format="PNG")
                st.download_button(label=f"Download {name}", data=buf.getvalue(), file_name=name, mime="image/png")
    except Exception:
        st.info("Unable to generate suggestions for previews.")

st.markdown("---")
if file_iter:
    for uploaded in file_iter:
        st.subheader(uploaded.name)
        cols = st.columns([1, 2])
        with cols[0]:
            st.image(uploaded, use_container_width=True)
        with cols[1]:
            with st.expander("Edit & Export"):
                cb_color_boost = st.slider(f"Color boost ({uploaded.name})", 0.5, 2.0, 1.1, 0.05, key=f"cb_{uploaded.name}")
                cb_edge_strength = st.slider(f"Edge strength ({uploaded.name})", 0.0, 1.0, 0.5, 0.05, key=f"es_{uploaded.name}")
                cb_bilateral_iter = st.slider(f"Smoothing passes ({uploaded.name})", 0, 4, 2, key=f"bi_{uploaded.name}")
                cb_gold = st.color_picker(f"Gold color ({uploaded.name})", value=gold_color, key=f"gc_{uploaded.name}")
                cb_transparent = st.checkbox(f"Make background transparent ({uploaded.name})", value=transparent, key=f"tr_{uploaded.name}")
                cb_dtg = st.selectbox(f"DTG preset ({uploaded.name})", options=["4500x5400_300dpi", "3200x3200_300dpi", "2000x2000_300dpi"], index=0, key=f"dtg_{uploaded.name}")
                if st.button(f"Apply edits and export {uploaded.name}", key=f"apply_{uploaded.name}"):
                    with st.spinner("Applying edits..."):
                        # write temp file
                        with tempfile.NamedTemporaryFile(delete=False, suffix=Path(uploaded.name).suffix) as tf:
                            tf.write(uploaded.getbuffer())
                            tf.flush()
                            inp_path = Path(tf.name)
                        stylize_opts = {
                            "color_boost": cb_color_boost,
                            "edge_strength": cb_edge_strength,
                            "bilateral_iter": cb_bilateral_iter
                        }
                        # re-run pipeline for this image
                        out_dir = Path("output/streamlit")
                        out_dir.mkdir(parents=True, exist_ok=True)
                        res = process_image(inp_path, out_dir, keep_svg=True, stylize_opts=stylize_opts)
                        img = Image.open(res["stylized"]).convert("RGBA")
                        img = add_batch_watermark(img, f"{uploaded.name}", cb_gold)
                        # DTG resize and save
                        presets = {
                            "4500x5400_300dpi": (4500, 5400, 300),
                            "3200x3200_300dpi": (3200, 3200, 300),
                            "2000x2000_300dpi": (2000, 2000, 300)
                        }
                        tw, th, tdpi = presets.get(cb_dtg, presets["4500x5400_300dpi"])
                        iw, ih = img.size
                        scale = min(tw / iw, th / ih)
                        nw = int(iw * scale)
                        nh = int(ih * scale)
                        img = img.resize((nw, nh), resample=Image.LANCZOS)
                        canvas = Image.new("RGBA", (tw, th), (255, 255, 255, 255))
                        canvas.paste(img, ((tw - nw) // 2, (th - nh) // 2), img)
                        if cb_transparent:
                            canvas = make_transparent(canvas)
                        out_name = f"{Path(uploaded.name).stem}_edited_{cb_dtg}.png"
                        save_path = out_dir / out_name
                        canvas.convert("RGB").save(save_path, optimize=True, dpi=(tdpi, tdpi))
                        with open(save_path, "rb") as f:
                            data = f.read()
                        st.image(save_path, caption="Edited result", use_container_width=True)
                        st.download_button(label=f"Download {out_name}", data=data, file_name=out_name, mime="image/png")

st.markdown("---")
st.write("Advanced: the app uses your existing processing code from `pil_art_pipeline.py` (denoise, stylize, SVG, pHash). If you have `madhubani_pro.py` and want it integrated, I can adapt the app to call it instead.")
