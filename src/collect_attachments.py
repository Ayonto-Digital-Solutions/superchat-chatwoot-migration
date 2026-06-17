"""Collect all attachments from raw/<cv>/attachments/ into one folder.

Copies every attachment to out/attachments/ with a speaking name
(<cv_id>__<original>) and writes a manifest (JSON + CSV) that maps each file to
its conversation, message, role and timestamp (from the PDF reference).

Run: python -m src.collect_attachments
"""
import csv
import json
import logging
import shutil
import subprocess
from pathlib import Path

from . import config
from . import parse_pdf

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s"
)
log = logging.getLogger("collect_attachments")


def _safe(name):
    return "".join(c if c.isalnum() or c in "._-" else "_" for c in name)


def _pdf_preview(pdf_path, previews_dir, base_name):
    """Render the first page of a PDF to PNG (pdftoppm). Returns filename or None."""
    out_prefix = previews_dir / base_name  # pdftoppm appends .png with -singlefile
    try:
        subprocess.run(
            ["pdftoppm", "-png", "-singlefile", "-f", "1", "-l", "1",
             "-scale-to", "1200", str(pdf_path), str(out_prefix)],
            check=True, capture_output=True,
        )
        png = out_prefix.with_suffix(".png")
        return png.name if png.exists() else None
    except Exception as exc:
        log.warning("PDF preview failed for %s: %s", pdf_path, exc)
        return None


def main():
    config.ensure_dirs()
    dest = config.OUT_DIR / "attachments"
    dest.mkdir(parents=True, exist_ok=True)
    previews = dest / "previews"
    previews.mkdir(parents=True, exist_ok=True)

    manifest = []
    copied = missing = 0

    for cv_dir in sorted(d for d in config.RAW_DIR.iterdir() if d.is_dir()):
        cv_id = cv_dir.name
        att_root = cv_dir / "attachments"
        if not att_root.is_dir():
            continue

        # PDF reference: which attachment belongs to which message
        ref = {}  # file_id -> {message_id, role, timestamp, filename}
        pdf = parse_pdf.find_pdf(cv_dir)
        if pdf:
            parsed = parse_pdf.parse(pdf)
            for msg in parsed["messages"]:
                for a in msg["attachments"]:
                    ref[a["file_id"]] = {
                        "message_id": msg["message_id"],
                        "role": msg["role"],
                        "timestamp": msg["timestamp"],
                        "filename": a["filename"],
                    }

        # copy every physical file under attachments/<file_id>/<name>
        for file_id_dir in att_root.iterdir():
            if not file_id_dir.is_dir():
                continue
            file_id = file_id_dir.name
            for src in file_id_dir.iterdir():
                if not src.is_file():
                    continue
                out_name = _safe(f"{cv_id}__{src.name}")
                out_path = dest / out_name
                # avoid collisions by prefixing the file_id if needed
                if out_path.exists():
                    out_name = _safe(f"{cv_id}__{file_id}__{src.name}")
                    out_path = dest / out_name
                shutil.copy2(src, out_path)
                copied += 1

                # PDF attachments: render first page as preview for quick review
                preview = None
                if out_path.suffix.lower() == ".pdf":
                    preview = _pdf_preview(out_path, previews, out_path.stem)

                meta = ref.get(file_id, {})
                manifest.append({
                    "cv_id": cv_id,
                    "file_id": file_id,
                    "original_name": src.name,
                    "local_file": out_name,
                    "preview": preview,
                    "message_id": meta.get("message_id"),
                    "role": meta.get("role"),
                    "timestamp": meta.get("timestamp"),
                })

    # write manifest (JSON + CSV)
    (dest / "manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    if manifest:
        with open(dest / "manifest.csv", "w", newline="", encoding="utf-8") as fh:
            writer = csv.DictWriter(fh, fieldnames=list(manifest[0].keys()))
            writer.writeheader()
            writer.writerows(manifest)

    log.info("Collected %d attachments -> %s (manifest.json/.csv)", copied, dest)
    if missing:
        log.warning("%d referenced attachments were missing on disk", missing)


if __name__ == "__main__":
    main()
