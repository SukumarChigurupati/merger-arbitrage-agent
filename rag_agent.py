# rag_agent.py
# AI Merger Arbitrage Analyst (RAG)

import os
import json
import argparse
from typing import List, Dict, Any

from dotenv import load_dotenv

from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.documents import Document
from langchain_core.output_parsers import StrOutputParser
from langchain_groq import ChatGroq

# ---------------- CONFIG ----------------

load_dotenv()

MODEL_NAME = os.getenv("OPENAI_MODEL", "gpt-4o")
PERSIST_DIR = "merger_db"
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"
TOP_K = 6

llm = ChatGroq(
    model="llama-3.3-70b-versatile",
    temperature=0.2,
)


# ---------------- VECTOR DB ----------------

embeddings = HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
vectordb = Chroma(persist_directory=PERSIST_DIR, embedding_function=embeddings)

# ✅ SIMPLE, ERROR-FREE RETRIEVER
retriever = vectordb.as_retriever(search_kwargs={"k": TOP_K})

# ---------------- PROMPT ----------------

SYSTEM_PROMPT = """
You are a precise M&A analyst focused on SEC 8-K Exhibit 2.1.
Use ONLY the information found in the retrieved context.
If something is not found, say so.
"""

USER_PROMPT = ChatPromptTemplate.from_messages(
    [
        ("system", SYSTEM_PROMPT),
        (
            "user",
            """Question:
{question}

Context:
{context}

Instructions:
1. Return a clear Markdown answer.
2. Then return a JSON code block summarizing deal terms.
3. Then list the file names used as citations."""
        ),
    ]
)

# ---------------- HELPERS ----------------


def format_docs(docs: List[Document]) -> str:
    parts = []
    for i, d in enumerate(docs, 1):
        src = d.metadata.get("source", "unknown")
        parts.append(f"[Doc {i}] ({src})\n{d.page_content}")
    return "\n\n".join(parts)


def collect_sources(docs: List[Document]) -> List[str]:
    out = []
    for d in docs:
        src = d.metadata.get("source", "unknown")
        if src not in out:
            out.append(src)
    return out


def answer_question(question: str) -> Dict[str, Any]:
    docs = retriever.invoke(question)
    context = format_docs(docs)
    sources = collect_sources(docs)

    chain = USER_PROMPT | llm | StrOutputParser()
    response = chain.invoke({"question": question, "context": context})

    return {
        "answer_markdown": response,
        "sources": sources
    }


# ---------------- CLI ----------------

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-q", "--question")
    args = parser.parse_args()

    if not os.path.isdir(PERSIST_DIR):
        raise SystemExit(
            "❌ Vector DB not found. Run build_vector_db.py first.")

    if not os.getenv("GROQ_API_KEY"):
        raise SystemExit("❌ GROQ_API_KEY missing in .env")

    if args.question:
        out = answer_question(args.question)
        print(out["answer_markdown"])
        print("\n--- CITATIONS ---")
        for s in out["sources"]:
            print("•", s)
    else:
        print("Interactive mode: type a question or 'exit'")
        while True:
            q = input("Q> ").strip()
            if q.lower() in ("exit", "quit"):
                break
            out = answer_question(q)
            print(out["answer_markdown"])
            print("\n--- CITATIONS ---")
            for s in out["sources"]:
                print("•", s)


if __name__ == "__main__":
    main()
