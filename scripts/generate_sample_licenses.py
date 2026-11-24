# Copyright 2026 Google LLC
# SPDX-License-Identifier: Apache-2.0

"""Generates synthetic SPECIMEN US Driver's License PNG and PDF files for demos."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from PIL import Image, ImageDraw, ImageFont


CARD_WIDTH = 1100
CARD_HEIGHT = 700
DEFAULT_OUTPUT_DIR = Path(__file__).resolve().parent.parent / "sample_docs"

BOLD_FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
REGULAR_FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
MONO_FONT_PATH = Path("/usr/share/fonts/truetype/dejavu/DejaVuSansMono.ttf")


@dataclass(frozen=True)
class SampleLicenseSpec:
    """Specification for a synthetic US Driver's License specimen."""

    stem: str
    state_name: str
    state_code: str
    id_label: str
    dl_number: str
    exp_date: str
    last_name: str
    first_name: str
    address_line1: str
    address_line2: str
    dob: str
    issue_date: str
    license_class: str
    sex: str
    height: str
    eyes: str
    header_bg: tuple[int, int, int]
    accent_color: tuple[int, int, int]
    card_tint: tuple[int, int, int]


SAMPLE_SPECS: tuple[SampleLicenseSpec, ...] = (
    SampleLicenseSpec(
        stem="sample_dl_california_valid",
        state_name="CALIFORNIA",
        state_code="CA",
        id_label="DL",
        dl_number="D8492017",
        exp_date="08/24/2029",
        last_name="RIVERS",
        first_name="ELENA MARIE",
        address_line1="1420 BAYSHORE BLVD APT 4B",
        address_line2="SAN FRANCISCO, CA 94124",
        dob="08/24/1991",
        issue_date="03/15/2023",
        license_class="C",
        sex="F",
        height="5-06",
        eyes="BRN",
        header_bg=(19, 44, 78),
        accent_color=(180, 132, 34),
        card_tint=(246, 248, 252),
    ),
    SampleLicenseSpec(
        stem="sample_dl_texas_expiring",
        state_name="TEXAS",
        state_code="TX",
        id_label="DL",
        dl_number="39104852",
        exp_date="01/15/2027",
        last_name="CHEN",
        first_name="MARCUS WEI",
        address_line1="780 CONGRESS AVE STE 200",
        address_line2="AUSTIN, TX 78701",
        dob="05/12/1986",
        issue_date="01/15/2022",
        license_class="C",
        sex="M",
        height="5-10",
        eyes="BLK",
        header_bg=(26, 54, 93),
        accent_color=(168, 42, 42),
        card_tint=(248, 249, 251),
    ),
    SampleLicenseSpec(
        stem="sample_dl_newyork_expired",
        state_name="NEW YORK",
        state_code="NY",
        id_label="ID",
        dl_number="719304826",
        exp_date="04/02/2025",
        last_name="PATEL",
        first_name="PRIYA ANJALI",
        address_line1="350 FIFTH AVE FL 21",
        address_line2="NEW YORK, NY 10118",
        dob="11/03/1994",
        issue_date="04/02/2020",
        license_class="D",
        sex="F",
        height="5-04",
        eyes="BRN",
        header_bg=(22, 38, 66),
        accent_color=(36, 102, 148),
        card_tint=(245, 247, 250),
    ),
)


def _load_font(path: Path, size: int) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    """Loads a TrueType font if available, falling back to Pillow's default."""
    if path.exists():
        return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _draw_silhouette(draw: ImageDraw.ImageDraw, box: tuple[int, int, int, int], accent: tuple[int, int, int]) -> None:
    """Draws a clean vector portrait silhouette inside the given bounding box."""
    x0, y0, x1, y1 = box
    width = x1 - x0
    height = y1 - y0

    # Background card photo gradient-like frame
    draw.rounded_rectangle(box, radius=10, fill=(228, 234, 242), outline=(185, 194, 206), width=2)

    # Subtle horizon band behind portrait
    draw.rectangle((x0 + 2, y1 - 75, x1 - 2, y1 - 2), fill=(214, 222, 232))

    cx = x0 + width // 2
    head_r = int(width * 0.20)
    head_cy = y0 + int(height * 0.36)

    # Shoulders
    shoulder_box = (
        cx - int(width * 0.36),
        y0 + int(height * 0.56),
        cx + int(width * 0.36),
        y1 + int(height * 0.25),
    )
    draw.pieslice(shoulder_box, start=180, end=360, fill=(98, 114, 134))

    # Head
    draw.ellipse(
        (cx - head_r, head_cy - head_r, cx + head_r, head_cy + head_r),
        fill=(98, 114, 134),
    )

    # Synthetic specimen caption bar at bottom of photo box
    bar_top = y1 - 34
    draw.rectangle((x0 + 2, bar_top, x1 - 2, y1 - 2), fill=accent)
    caption_font = _load_font(BOLD_FONT_PATH, 14)
    draw.text((cx, bar_top + 9), "SYNTHETIC PHOTO", fill=(255, 255, 255), font=caption_font, anchor="mt")


def render_license(spec: SampleLicenseSpec, output_dir: Path) -> tuple[Path, Path]:
    """Renders one synthetic US Driver's License to PNG and companion PDF."""
    output_dir.mkdir(parents=True, exist_ok=True)
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), spec.card_tint)
    draw = ImageDraw.Draw(image)

    # Fonts
    font_state = _load_font(BOLD_FONT_PATH, 42)
    font_subtitle = _load_font(BOLD_FONT_PATH, 18)
    font_badge = _load_font(BOLD_FONT_PATH, 15)
    font_label = _load_font(BOLD_FONT_PATH, 20)
    font_value_lg = _load_font(BOLD_FONT_PATH, 30)
    font_value_md = _load_font(BOLD_FONT_PATH, 25)
    font_value_sm = _load_font(REGULAR_FONT_PATH, 22)
    font_small_meta = _load_font(REGULAR_FONT_PATH, 18)
    font_mono = _load_font(MONO_FONT_PATH, 16)

    # Outer card border & subtle horizontal security guilloche lines
    draw.rounded_rectangle(
        (16, 16, CARD_WIDTH - 16, CARD_HEIGHT - 16),
        radius=22,
        fill=spec.card_tint,
        outline=(198, 205, 214),
        width=3,
    )
    for y in range(170, CARD_HEIGHT - 95, 18):
        draw.line((28, y, CARD_WIDTH - 28, y), fill=(236, 240, 246), width=1)

    # Header band
    draw.rounded_rectangle(
        (20, 20, CARD_WIDTH - 20, 140),
        radius=18,
        fill=spec.header_bg,
    )
    draw.rectangle((20, 118, CARD_WIDTH - 20, 140), fill=spec.header_bg)
    draw.rectangle((20, 140, CARD_WIDTH - 20, 148), fill=spec.accent_color)

    # State title & DRIVER LICENSE subtitle
    draw.text((52, 42), spec.state_name, fill=(255, 255, 255), font=font_state)
    draw.text((54, 96), "USA  DRIVER LICENSE", fill=(212, 224, 240), font=font_subtitle)

    # Specimen banner pill in header right
    pill_box = (CARD_WIDTH - 365, 44, CARD_WIDTH - 48, 86)
    draw.rounded_rectangle(pill_box, radius=8, fill=(255, 255, 255))
    draw.text(
        ((pill_box[0] + pill_box[2]) // 2, pill_box[1] + 12),
        "SPECIMEN - SYNTHETIC DEMO",
        fill=spec.header_bg,
        font=font_badge,
        anchor="mt",
    )
    draw.text(
        (CARD_WIDTH - 48, 100),
        f"CLASS {spec.license_class}   USA",
        fill=(226, 232, 240),
        font=font_subtitle,
        anchor="rt",
    )

    # Left portrait column
    photo_box = (52, 182, 312, 502)
    _draw_silhouette(draw, photo_box, spec.accent_color)

    # Small synthetic signature line under photo
    draw.line((52, 558, 312, 558), fill=(150, 160, 174), width=2)
    draw.text((54, 524), f"{spec.first_name.split()[0]} {spec.last_name}", fill=(62, 74, 92), font=font_small_meta)
    draw.text((54, 566), "HOLDER SIGNATURE (SPECIMEN)", fill=(120, 130, 145), font=_load_font(REGULAR_FONT_PATH, 13))

    # Main structured AAMVA data block on right
    col_x = 356
    val_offset = 66

    # Row 1: DL/ID Number & EXP Date
    draw.text((col_x, 184), spec.id_label, fill=spec.accent_color, font=font_label)
    draw.text((col_x + val_offset, 178), spec.dl_number, fill=(17, 24, 39), font=font_value_lg)

    exp_x = 760
    draw.text((exp_x, 184), "EXP", fill=(180, 38, 38), font=font_label)
    draw.text((exp_x + 64, 178), spec.exp_date, fill=(180, 38, 38), font=font_value_lg)

    # Row 2: Last Name & First Name
    draw.text((col_x, 246), "LN", fill=(82, 96, 114), font=font_label)
    draw.text((col_x + val_offset, 242), spec.last_name, fill=(17, 24, 39), font=font_value_md)

    draw.text((col_x, 294), "FN", fill=(82, 96, 114), font=font_label)
    draw.text((col_x + val_offset, 290), spec.first_name, fill=(17, 24, 39), font=font_value_md)

    # Row 3: Address lines (no prefix label overlap so parser picks up clean address)
    draw.text((col_x + val_offset, 348), spec.address_line1, fill=(31, 41, 55), font=font_value_sm)
    draw.text((col_x + val_offset, 382), spec.address_line2, fill=(31, 41, 55), font=font_value_sm)

    # Divider
    draw.line((col_x, 428, CARD_WIDTH - 52, 428), fill=(210, 218, 228), width=2)

    # Row 4: DOB & ISS Date
    draw.text((col_x, 448), "DOB", fill=spec.header_bg, font=font_label)
    draw.text((col_x + 74, 444), spec.dob, fill=(17, 24, 39), font=font_value_md)

    draw.text((exp_x, 448), "ISS", fill=spec.header_bg, font=font_label)
    draw.text((exp_x + 64, 444), spec.issue_date, fill=(17, 24, 39), font=font_value_md)

    # Row 5: Secondary AAMVA physical descriptors
    draw.text((col_x, 512), f"SEX {spec.sex}", fill=(65, 78, 94), font=font_small_meta)
    draw.text((col_x + 130, 512), f"HGT {spec.height}", fill=(65, 78, 94), font=font_small_meta)
    draw.text((col_x + 280, 512), f"EYES {spec.eyes}", fill=(65, 78, 94), font=font_small_meta)
    draw.text((col_x + 430, 512), "REST NONE", fill=(65, 78, 94), font=font_small_meta)

    # Bottom decorative synthetic barcode & document discriminator strip
    draw.line((44, 608, CARD_WIDTH - 44, 608), fill=(214, 220, 228), width=2)
    bar_x = 52
    pattern = [4, 2, 6, 2, 3, 5, 2, 4, 6, 3, 2, 5, 3, 2, 6, 4, 2, 3, 5, 2, 4, 3, 6, 2, 4, 2, 5, 3]
    for idx, width_unit in enumerate(pattern * 2):
        w = width_unit
        if idx % 2 == 0 and bar_x + w < 520:
            draw.rectangle((bar_x, 622, bar_x + w, 660), fill=(42, 52, 68))
        bar_x += w + 3

    draw.text(
        (CARD_WIDTH - 52, 632),
        f"DD {spec.state_code}-SYNTH-{spec.dl_number}  |  NON-OFFICIAL SPECIMEN",
        fill=(107, 114, 128),
        font=font_mono,
        anchor="rt",
    )

    png_path = output_dir / f"{spec.stem}.png"
    pdf_path = output_dir / f"{spec.stem}.pdf"
    image.save(png_path, format="PNG")
    image.convert("RGB").save(pdf_path, format="PDF", resolution=150.0)
    return png_path, pdf_path


def generate_all(output_dir: Path = DEFAULT_OUTPUT_DIR) -> list[tuple[Path, Path]]:
    """Generates all synthetic driver's license PNG and PDF specimens."""
    return [render_license(spec, output_dir) for spec in SAMPLE_SPECS]


if __name__ == "__main__":
    results = generate_all()
    for png_file, pdf_file in results:
        print(f"Generated {png_file.name} and {pdf_file.name}")
