import os
import json
from pathlib import Path
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.document_loaders import TextLoader
from langchain_community.embeddings import HuggingFaceEmbeddings
from langchain_chroma import Chroma

DB_DIR = "merger_db"


def load_metadata():
    """Load metadata records from JSONL."""
    records = []
    with open("ex21_metadata.jsonl", "r", encoding="utf-8") as f:
        for line in f:
            records.append(json.loads(line))
    return records


def load_documents():
    """Load exhibit files (HTM + HTML + TXT) into memory."""
    docs = []
    base_dirs = ["EDGAR_EXHIBITS_2020_2025", "EDGAR_EXHIBITS_2024_2025"]

    for base in base_dirs:
        if not Path(base).exists():
            continue
        for root, _, files in os.walk(base):
            for file in files:
                if file.lower().endswith((".htm", ".html", ".txt")):
                    full_path = os.path.join(root, file)
                    loader = TextLoader(
                        full_path, encoding="utf-8", autodetect_encoding=True)
                    docs.extend(loader.load())

    return docs


def main():
    print("✅ Loading metadata…")
    _ = load_metadata()  # not used yet, but keeps flow ready for enrichment

    print("✅ Loading documents (HTM/HTML/TXT)…")
    docs = load_documents()
    print(f"✅ Loaded {len(docs)} documents")

    splitter = RecursiveCharacterTextSplitter(
        chunk_size=1500, chunk_overlap=200)
    chunks = splitter.split_documents(docs)
    print(f"✅ Split into {len(chunks)} text chunks")

    embeddings = HuggingFaceEmbeddings(
        model_name="sentence-transformers/all-MiniLM-L6-v2")

    print("✅ Building Chroma vector DB…")
    _ = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=DB_DIR
    )

    print(f"\n✅ Vector DB created & saved at: {DB_DIR}/")
    print("✅ Ready for use in RAG agent!")


if __name__ == "__main__":
    main()
