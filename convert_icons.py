#!/usr/bin/env python3
"""
convert_icons.py

Batch-converts a directory of .tga icon files (e.g. extracted from a
Petroglyph Mega-Texture via the MTD Editor) into .png files, with
filenames normalized to lowercase so they match what
generate_glossary.py's icon_src() builds from each unit's Icon_Name tag.

USAGE
    python3 convert_icons.py --input tga_folder --output images

Requires Pillow: pip install --break-system-packages Pillow
"""

import argparse
import os
import sys

try:
    from PIL import Image
except ImportError:
    print("Pillow is required. Install it with:\n"
          "  pip install --break-system-packages Pillow", file=sys.stderr)
    sys.exit(1)


def convert_all(input_dir, output_dir, ext="png"):
    os.makedirs(output_dir, exist_ok=True)
    converted, failed = 0, []

    for fname in sorted(os.listdir(input_dir)):
        if not fname.lower().endswith(".tga"):
            continue
        stem = os.path.splitext(fname)[0].lower()
        src_path = os.path.join(input_dir, fname)
        dst_path = os.path.join(output_dir, f"{stem}.{ext}")

        try:
            with Image.open(src_path) as img:
                # Normalize mode: keep alpha if present, otherwise plain RGB.
                # TGAs from these tools are commonly paletted, RGB, or RGBA.
                if img.mode in ("RGBA", "LA") or (img.mode == "P" and "transparency" in img.info):
                    img = img.convert("RGBA")
                else:
                    img = img.convert("RGB")
                img.save(dst_path)
            converted += 1
        except Exception as e:
            failed.append((fname, str(e)))

    return converted, failed


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--input", required=True, help="Directory containing extracted .tga files.")
    ap.add_argument("--output", required=True, help="Directory to write converted images into.")
    ap.add_argument("--ext", default="png", help="Output extension (default: png).")
    args = ap.parse_args()

    if not os.path.isdir(args.input):
        print(f"Input directory not found: {args.input}", file=sys.stderr)
        sys.exit(1)

    converted, failed = convert_all(args.input, args.output, args.ext)

    print(f"Converted {converted} file(s) into {args.output}")
    if failed:
        print(f"\n{len(failed)} file(s) failed to convert:")
        for fname, err in failed:
            print(f"  ! {fname}: {err}")


if __name__ == "__main__":
    main()
