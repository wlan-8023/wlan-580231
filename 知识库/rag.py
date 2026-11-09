"""极简 LangChain RAG：清洗 -> 分块 -> Embedding -> FAISS 向量库 -> 检索

输入识别：
  - Word（.docx）：调用本地 tools（tools.py）按标题层级切块
  - Q/A 数据集（每行 Q:...A:...）：每条问答一个块
  - Markdown（以 --- 分隔）：每个分隔块一条向量

用法（默认路径已指向 知识库/数据文件 与 知识库/向量库）：
    python rag.py build                                        # 默认 test.md -> 向量库/知识内容
    python rag.py build --source 数据文件/冰箱说明书.md --index 向量库/冰箱说明书
    python rag.py ask "焦耳定律公式"                              # 默认读取 向量库/知识内容
"""
import os
import re
import argparse
import importlib.util
import pathlib
from contextlib import contextmanager

os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
os.environ.setdefault("HF_HUB_OFFLINE", "1")

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from langchain_community.embeddings import FastEmbedEmbeddings
from langchain_community.vectorstores import FAISS

HERE = pathlib.Path(__file__).resolve().parent
MODEL = "BAAI/bge-small-zh-v1.5"

# 默认值，可被 main() 中的 --source / --index 覆盖
# 数据文件与向量库已迁移至 知识库/数据文件 与 知识库/向量库
DATA_DIR = HERE / "数据文件"
VECTOR_DIR = HERE / "向量库"
SOURCE = DATA_DIR / "test.md"
INDEX_DIR = VECTOR_DIR / "知识内容"


def _load_tools():
    """按需载入本地 tools 库，复用其中的文档清洗与切块能力。"""
    spec = importlib.util.spec_from_file_location("local_tools", HERE / "tools.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def clean_and_split():
    """清洗并分块，返回 Document 列表。"""
    docs = []

    # Word 文档：调用 tools 库完成清洗与按标题切块
    if SOURCE.suffix.lower() == ".docx":
        tools = _load_tools()
        items = tools.clean(tools.read_docx(SOURCE))
        for c in tools.chunk(items):
            docs.append(Document(
                page_content="\n".join(c["lines"]),
                metadata={"section": c["title"]},
            ))
        return docs

    text = SOURCE.read_text(encoding="utf-8").strip()

    if "\n---\n" in text:
        # Markdown：每个 --- 分隔块一条向量，块首标题作 section
        for part in re.split(r"\n---\n", text):
            part = part.strip()
            if not part:
                continue
            section = ""
            m = re.match(r"^#{1,6}\s+(.*)", part)
            if m:
                section = m.group(1).strip()
            docs.append(Document(page_content=part, metadata={"section": section}))
        return docs

    # Q/A 数据集：逐行解析，每条问答一个块（超长由切分器兜底）
    section = ""
    for raw in text.splitlines():
        line = re.sub(r"\s+", " ", raw).strip()
        if line.startswith("## "):
            section = line[3:].strip()
        elif line.startswith("Q:") and "A:" in line:
            docs.append(Document(page_content=line, metadata={"section": section}))
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=200, chunk_overlap=20, separators=["\n", "。", "；", "，", " ", ""]
    )
    return splitter.split_documents(docs)


def embedder():
    return FastEmbedEmbeddings(model_name=MODEL)


@contextmanager
def _in_dir(path):
    """切到目标目录后使用相对路径读写。

    Windows 版 FAISS 的 C++ 层无法打开含中文的绝对路径，
    因此进入目录后只用 'index.faiss' 这类相对文件名。
    """
    path = pathlib.Path(path).resolve()
    path.mkdir(parents=True, exist_ok=True)
    old = os.getcwd()
    os.chdir(path)
    try:
        yield "."
    finally:
        os.chdir(old)


def build():
    chunks = clean_and_split()
    emb = embedder()
    with _in_dir(INDEX_DIR):
        FAISS.from_documents(chunks, emb).save_local(".")
    print(f"已构建向量库：{len(chunks)} 个块 -> {INDEX_DIR}")


def discover_indexes():
    """列出 向量库 下所有已建好的子索引目录。"""
    if not VECTOR_DIR.exists():
        return []
    return sorted(p for p in VECTOR_DIR.iterdir() if (p / "index.faiss").exists())


def search(query, k=4, indexes=None):
    """多索引合并检索，返回 [{score, index, section, content}]，按 L2 距离升序。"""
    emb = embedder()
    indexes = [pathlib.Path(i) for i in indexes] if indexes else discover_indexes()
    if not indexes:
        raise RuntimeError(f"未找到向量库：{VECTOR_DIR}")

    hits = []
    for p in indexes:
        with _in_dir(p):
            store = FAISS.load_local(".", emb, allow_dangerous_deserialization=True)
        for doc, score in store.similarity_search_with_score(query, k=k):
            hits.append({
                "score": float(score),
                "index": p.name,
                "section": doc.metadata.get("section", ""),
                "content": doc.page_content,
            })
    hits.sort(key=lambda x: x["score"])
    return hits[:k]


def ask(query, k=4, indexes=None):
    for i, h in enumerate(search(query, k, indexes), 1):
        print(f"[{i}] L2={h['score']:.3f} | {h['index']} | {h['section']}\n{h['content']}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="建库")
    b.add_argument("--source", default=str(SOURCE))
    b.add_argument("--index", default=str(INDEX_DIR))

    p = sub.add_parser("ask", help="检索（默认合并查询 向量库 下所有索引）")
    p.add_argument("query")
    p.add_argument("-k", type=int, default=4)
    p.add_argument("--index", nargs="+", default=None)

    args = parser.parse_args()

    if args.cmd == "build":
        SOURCE = pathlib.Path(args.source)
        INDEX_DIR = pathlib.Path(args.index)
        build()
    else:
        try:
            ask(args.query, args.k, args.index)
        except Exception as e:
            raise SystemExit(str(e))