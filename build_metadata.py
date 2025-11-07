import json
import pathlib
from bs4 import BeautifulSoup

ROOT = pathlib.Path(".")

# Folders that contain our downloaded exhibits
EXHIBIT_DIRS = [
    ROOT / "EDGAR_EXHIBITS",
    ROOT / "EDGAR_EXHIBITS_2020_2025",
    ROOT / "EDGAR_EXHIBITS_2024_2025",
]

OUTPUT_FILE = ROOT / "ex21_metadata.jsonl"


def extract_text_from_html(path: pathlib.Path) -> str:
    """Extract clean readable text from HTM/HTML exhibit."""
    try:
        html = path.read_text(errors="ignore")
        soup = BeautifulSoup(html, "html.parser")
        text = soup.get_text(" ", strip=True)
        return text[:8000]  # limit to 8K characters
    except Exception:
        return ""


def build_metadata():
    records = []
    total_files = 0

    for folder in EXHIBIT_DIRS:
        if not folder.exists():
            continue

        for company_dir in folder.iterdir():
            if not company_dir.is_dir():
                continue

            ticker = company_dir.name

            for file in company_dir.iterdir():
                if file.suffix.lower() in [".htm", ".html"]:
                    pdf_file = file.with_suffix(".pdf")

                    record = {
                        "ticker": ticker,
                        "source_folder": str(folder.name),
                        "html_file": str(file),
                        "pdf_file": str(pdf_file) if pdf_file.exists() else None,
                        "content": extract_text_from_html(file),
                    }

                    records.append(record)
                    total_files += 1

    # Write JSONL
    with OUTPUT_FILE.open("w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

    print(f"✅ Metadata built successfully!")
    print(f"✅ Total exhibits processed: {total_files}")
    print(f"✅ Output written to: {OUTPUT_FILE}")


if __name__ == "__main__":
    build_metadata()
