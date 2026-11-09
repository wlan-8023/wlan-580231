"""本地 tools 库 —— 文档清洗与切块能力封装为 MCP Tool（自包含，零第三方依赖）。

MCP 协议：stdio + JSON-RPC 2.0，仅标准库。

由 MCP 客户端拉起（见 .trae/mcp.json）：
    python tools.py

也可作为库导入：
    from tools import read_docx, read_md, clean, chunk, render_md
"""
import json
import re
import sys
import zipfile
import importlib.util
import xml.etree.ElementTree as ET
from pathlib import Path

W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"

# 末尾起草附言特征（命中即删除该行及其之后所有行）
DRAFT_MARKERS = ("如果你想要", "我可以再给你")

_H1 = {"heading1": 1, "heading2": 2, "heading3": 3,
       "标题1": 1, "标题2": 2, "标题3": 3, "1": 1, "2": 2, "3": 3}


def read_docx(path):
    """读取 docx，返回 [(level, text), ...]。level: 0=正文，1/2/3=标题层级。"""
    z = zipfile.ZipFile(path)
    root = ET.fromstring(z.read("word/document.xml"))
    items = []
    for p in root.iter(W + "p"):
        style = ""
        ppr = p.find(W + "pPr")
        if ppr is not None:
            ps = ppr.find(W + "pStyle")
            if ps is not None:
                style = ps.get(W + "val") or ""
        text = "".join(t.text or "" for t in p.iter(W + "t"))
        level = _H1.get(style.lower(), 0)
        if level == 0 and re.match(r"^\d+\.\s", text.strip()):
            level = 1  # 数值章节编号兜底识别
        items.append((level, text))
    return items


def read_md(path):
    """读取 markdown，依据 '#' 数量识别层级。"""
    items = []
    for raw in Path(path).read_text(encoding="utf-8").splitlines():
        m = re.match(r"^(#{1,6})\s+(.*)", raw)
        if m:
            items.append((min(len(m.group(1)), 6), m.group(2).strip()))
        else:
            items.append((0, raw))
    return items


def clean(items):
    """清洗：去空行、压缩空白、剔除末尾起草附言。"""
    out = []
    for level, text in items:
        text = re.sub(r"\s+", " ", text).strip()
        if not text:
            continue
        if text.startswith(DRAFT_MARKERS):
            break
        out.append((level, text))
    return out


def chunk(items):
    """按标题切块，返回 [{level, title, lines}]。"""
    chunks, cur = [], None
    for level, text in items:
        if level > 0:
            if cur and (cur["lines"] or cur["title"]):
                chunks.append(cur)
            cur = {"level": level, "title": text, "lines": []}
        elif text == "---":
            if cur and (cur["lines"] or cur["title"]):
                chunks.append(cur)
                cur = None
        else:
            if cur is None:
                cur = {"level": 1, "title": "", "lines": []}
            cur["lines"].append(text)
    if cur and (cur["lines"] or cur["title"]):
        chunks.append(cur)
    return chunks


def render_md(chunks, sep="---"):
    blocks = []
    for c in chunks:
        parts = []
        if c["title"]:
            parts.append("#" * min(c["level"], 6) + " " + c["title"])
        parts.extend(c["lines"])
        blocks.append("\n".join(parts))
    return ("\n\n" + sep + "\n\n").join(blocks)


def clean_and_chunk(input_path, fmt="md", out_path=None):
    """对外能力入口：清洗并切分文档，返回结果文本（或写入 out_path）。"""
    p = Path(input_path)
    if not p.exists():
        raise FileNotFoundError(f"文件不存在: {input_path}")
    items = read_docx(p) if p.suffix.lower() == ".docx" else read_md(p)
    chunks = chunk(clean(items))
    if fmt == "jsonl":
        result = "\n".join(json.dumps(c, ensure_ascii=False) for c in chunks)
    else:
        result = render_md(chunks)
    if out_path:
        Path(out_path).write_text(result, encoding="utf-8")
        return f"已写入 {out_path}（共 {len(chunks)} 个 chunk）"
    return result + f"\n\n[共 {len(chunks)} 个 chunk]"


# ---------- 向量库检索（复用 rag.py，按需加载） ----------

_RAG = None


def _rag():
    """惰性载入同目录 rag.py，避免 MCP 启动时就加载 langchain。"""
    global _RAG
    if _RAG is None:
        spec = importlib.util.spec_from_file_location("rag_local", Path(__file__).with_name("rag.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _RAG = mod
    return _RAG


def search_knowledge(query, top_k=5, indexes=None):
    """检索本地向量库：默认合并 知识库/向量库 下全部索引，返回格式化命中结果。"""
    if not query or not str(query).strip():
        raise ValueError("query 不能为空")
    try:
        k = int(top_k) if top_k is not None else 5
    except (TypeError, ValueError):
        k = 5
    k = max(1, min(k, 20))
    rag = _rag()
    dirs = None
    if indexes:
        # 允许只传库名（如 "冰箱说明书"），自动拼到 向量库 目录下
        dirs = []
        for i in indexes:
            p = Path(i)
            if not p.exists():
                p = rag.VECTOR_DIR / str(i)
            dirs.append(str(p))
    hits = rag.search(str(query), k=k, indexes=dirs)
    if not hits:
        return "无匹配结果"
    return "\n\n".join(
        f"[{i}] L2={h['score']:.3f} | {h['index']} | {h['section']}\n{h['content']}"
        for i, h in enumerate(hits, 1)
    )


# ---------- MCP Tool ----------

SERV_NAME = "local-tools"
SERV_VERSION = "1.0.0"

TOOLS = [
    {
        "name": "chunk_document",
        "description": (
            "清洗并切分文档：支持 .docx / .md / .txt，识别标题层级、去空行、"
            "压缩空白、剔除末尾起草附言，输出按标题切分的分块结果。"
            "参数 input_path 为文件绝对路径；format 为 md 或 jsonl；"
            "out_path 指定时结果写入该文件，否则以文本返回。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "input_path": {"type": "string", "description": "输入文件的绝对路径"},
                "format": {"type": "string", "enum": ["md", "jsonl"], "default": "md", "description": "输出格式"},
                "out_path": {"type": "string", "description": "可选，指定后结果写入该文件路径"},
            },
            "required": ["input_path"],
        },
    },
    {
        "name": "search_knowledge",
        "description": (
            "检索本地向量库（RAG）：默认合并查询 知识库/向量库 下所有索引，"
            "按相似度返回最相关的分块。参数 query 为自然语言问题；"
            "top_k 为返回条数（1-20，默认 5）；indexes 可指定库名或路径以限定范围。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "检索问题或关键词"},
                "top_k": {"type": "integer", "default": 5, "minimum": 1, "maximum": 20,
                          "description": "返回条数"},
                "indexes": {
                    "type": "array",
                    "items": {"type": "string"},
                    "description": "可选，指定向量库（库名如 冰箱说明书，或目录路径）；不传则合并全部",
                },
            },
            "required": ["query"],
        },
    },
]


def _send(obj):
    sys.stdout.write(json.dumps(obj, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def _error(msg_id, code, message):
    _send({"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}})


def main():
    for s in (sys.stdin, sys.stdout, sys.stderr):
        try:
            s.reconfigure(encoding="utf-8")
        except Exception:
            pass

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
        except Exception:
            _error(None, -32700, "Parse error")
            continue

        method = msg.get("method")
        has_id = "id" in msg

        if method == "initialize":
            params = msg.get("params") or {}
            _send({
                "jsonrpc": "2.0",
                "id": msg.get("id"),
                "result": {
                    "protocolVersion": params.get("protocolVersion", "2024-11-05"),
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": SERV_NAME, "version": SERV_VERSION},
                },
            })
        elif method == "notifications/initialized":
            pass
        elif method == "ping":
            if has_id:
                _send({"jsonrpc": "2.0", "id": msg["id"], "result": {}})
        elif method == "tools/list":
            if has_id:
                _send({"jsonrpc": "2.0", "id": msg["id"], "result": {"tools": TOOLS}})
        elif method == "tools/call":
            if not has_id:
                continue
            params = msg.get("params") or {}
            name = params.get("name")
            args = params.get("arguments") or {}
            try:
                if name == "chunk_document":
                    text = clean_and_chunk(
                        input_path=args.get("input_path"),
                        fmt=args.get("format") or "md",
                        out_path=args.get("out_path"),
                    )
                elif name == "search_knowledge":
                    text = search_knowledge(
                        query=args.get("query"),
                        top_k=args.get("top_k"),
                        indexes=args.get("indexes"),
                    )
                else:
                    _error(msg["id"], -32602, f"未知工具: {name}")
                    continue
                _send({
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"content": [{"type": "text", "text": text}], "isError": False},
                })
            except Exception as e:
                _send({
                    "jsonrpc": "2.0",
                    "id": msg["id"],
                    "result": {"content": [{"type": "text", "text": f"执行失败: {e}"}], "isError": True},
                })
        else:
            if has_id:
                _error(msg["id"], -32601, f"未实现的方法: {method}")


if __name__ == "__main__":
    main()