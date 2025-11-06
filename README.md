# 🏛️ Merger Arbitrage Agent  
### AI-Powered SEC 8-K Exhibit 2.1 Analyzer (2020–2025 M&A Deals)

This project extracts, processes, and analyzes **Exhibit 2.1 (Merger Agreements)** from SEC EDGAR 8-K filings for major M&A deals between **2020–2025**.

It fully automates:

✅ Downloading 8-K filings  
✅ Extracting Exhibit 2.1  
✅ Converting HTM → PDF  
✅ Building metadata for AI  
✅ Preparing datasets for LLM agents  
✅ Deploying the agent using **LangChain** or **Copilot Studio**

This serves as a **portfolio-ready, interview-ready project** demonstrating:  
**Python engineering • SEC EDGAR API • PDF automation • AI agents • LangChain • Azure AI Search • Copilot Studio integration**

---

# 🚀 Project Architecture

## ✅ 1. Input: M&A Deal Lists (CSV/XLSX)

Mergers list includes:
- **2020–2025 major deals**
- **2024–2025 latest high-value deals**

Each row includes:
- Announce Date  
- Acquirer / Target  
- Tickers  
- (Optional) CIK  

---

## ✅ 2. EDGAR Exhibit Downloader

Main script:

```
edgar_apidownloader.py
```

Capabilities:
- CIK lookup / fallback to ticker
- Searches **Form 8-K** (+ Amendments)
- Smart date-window scanning
- Auto-detects **Exhibit 2.1**
- Downloads HTM
- Converts HTM → PDF
- Creates clean directory per company
- Outputs logs for missing exhibits or bad tickers

---

# 📂 Output Structure

After running the downloader, your folder structure will look like:

```
EDGAR_EXHIBITS_2020_2025/
├── ADI/
│   ├── EX-2.1__0001193125-20-192918__d934725dex21.htm
│   ├── EX-2.1__0001193125-20-192918__d934725dex21.pdf
├── AMD/
├── PFE/
├── tickersnotfound.txt
└── missingexhibit2.1.txt
```

And similarly:

```
EDGAR_EXHIBITS_2024_2025/
├── ALK/
├── COF/
├── CSCO/
├── tickersnotfound.txt
└── missingexhibit2.1.txt
```

---

# 📦 Installation

```bash
git clone https://github.com/SukumarChigurupati/merger-arbitrage-agent.git
cd merger-arbitrage-agent

python -m venv .venv
.\.venv\Scripts\activate

pip install -r requirements.txt
```

---

# ▶️ Running the Downloader

Example for **2020–2025 Deals:**

```bash
python edgar_apidownloader.py ^
  --input-xlsx Mergers2020_2025.xlsx ^
  --save-dir EDGAR_EXHIBITS_2020_2025 ^
  --filer both ^
  --window_days 60 ^
  --filing 8-K ^
  --include_amends
```

Example for **2024–2025 Deals:**

```bash
python edgar_apidownloader.py ^
  --input-xlsx Mergers2024_2025.xlsx ^
  --save-dir EDGAR_EXHIBITS_2024_2025 ^
  --filer both ^
  --window_days 60 ^
  --filing 8-K ^
  --include_amends
```

---

# 🧠 Metadata Builder

Creates JSONL for AI model training.

```bash
python build_metadata.py
```

Output:

```
ex21_metadata.jsonl
```

This contains:
- Acquirer  
- Target  
- CIK  
- Status  
- First 5,000 characters of the Exhibit text  
- File path to HTM/PDF  

---

# 🤖 AI Agent Options

## ✅ LangChain / LangGraph Agent

Capabilities:
- Load metadata + PDFs
- Create embeddings from Exhibit 2.1
- Semantic search
- Q&A on merger agreements
- Summaries → Risks → Conditions → Purchase price
- Clause extraction

Technologies:
- OpenAI GPT-5
- FAISS / Azure AI Search
- LangChain RetrievalQA

---

## ✅ Copilot Studio Agent (No-Code)

You can also deploy:
- Upload metadata + PDFs to **Azure Blob**
- Index using **Azure AI Search**
- Connect index → Copilot Studio
- Build “Merger Arbitrage Analyst” chatbot
- Ask:
  - “What is the breakup fee in the AMD–Xilinx deal?”  
  - “Summarize covenants in the Pfizer–Seagen agreement”  
  - “Compare two merger agreements”

---

# ✅ Why This Project Matters

This project is **resume-ready** because it shows:

✅ Real SEC Data Engineering  
✅ Real M&A Knowledge  
✅ AI Agent Development  
✅ Azure & LangChain Experience  
✅ PDF + HTM Parsing  
✅ Metadata pipelines  
✅ GitHub-ready portfolio

Recruiters + interviewers LOVE this because it is:  
**real-world • complex • financial • AI-powered • end-to-end.**

---

# ✅ Author

**Sukumar Chigurupati**  
GitHub: https://github.com/SukumarChigurupati
