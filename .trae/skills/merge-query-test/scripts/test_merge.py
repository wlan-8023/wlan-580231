"""合并查询回归测试：校验多索引检索的排序、来源、编号与边界。

用法（在项目根目录执行）：
    python .trae/skills/merge-query-test/scripts/test_merge.py

退出码：0 全部通过；1 存在失败项。
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[4]  # .trae/skills/<name>/scripts -> 项目根
sys.path.insert(0, str(ROOT / "知识库"))

import tools  # noqa: E402
import rag  # noqa: E402

# (查询, top_k, 限定索引)  限定索引为 None 表示合并全部库
CASES = [
    ("冰箱注意事项", 5, None),
    ("焦耳定律公式", 5, None),
    ("温度", 10, None),                      # 关键用例：应同时命中两个库
    ("除霜", 5, ["冰箱说明书"]),
    ("圆的面积", 3, [str(ROOT / "知识库" / "向量库" / "知识内容")]),
]

LINE = re.compile(r"\[(\d+)\] L2=([\d.]+) \| ([^|]*?) \| ?(.*)")


def parse(text):
    """把工具返回文本解析为 [(编号, 分数, 库名, 章节)]。"""
    rows = []
    for block in text.split("\n\n"):
        m = LINE.match(block.splitlines()[0]) if block.strip() else None
        if m:
            rows.append((int(m.group(1)), float(m.group(2)), m.group(3).strip(), m.group(4).strip()))
    return rows


def main():
    fails = []
    names = {p.name for p in rag.discover_indexes()}
    print(f"发现索引：{sorted(names)}")
    if len(names) < 2:
        fails.append("索引数量不足 2 个，无法验证合并查询")

    merged_hit = False
    print("\n=== 用例 ===")
    for q, k, idxs in CASES:
        rows = parse(tools.search_knowledge(q, top_k=k, indexes=idxs))
        scores = [r[1] for r in rows]
        nums = [r[0] for r in rows]
        srcs = {r[2] for r in rows}
        scope = {Path(i).name for i in idxs} if idxs else names

        problems = []
        if len(rows) != k:
            problems.append(f"条数 {len(rows)} != {k}")
        if scores != sorted(scores):
            problems.append("L2 未升序")
        if nums != list(range(1, len(rows) + 1)):
            problems.append("编号不连续")
        if not srcs <= scope:
            problems.append(f"来源越界 {srcs - scope}")
        if idxs is None and len(srcs) >= 2:
            merged_hit = True

        tag = "PASS" if not problems else "FAIL"
        print(f"  [{tag}] {q!r} k={k} -> {len(rows)} 条, 来源={sorted(srcs)}"
              + ("" if not problems else " | " + "; ".join(problems)))
        fails.extend(f"{q}: {p}" for p in problems)

    if not merged_hit:
        fails.append("没有任何用例同时命中 2 个库，合并查询未生效")

    print("\n=== 边界 ===")
    checks = [
        ("top_k=0 夹取为 1", lambda: len(parse(tools.search_knowledge("温度", 0))) == 1),
        ("top_k=99 夹取为 20", lambda: len(parse(tools.search_knowledge("温度", 99))) == 20),
        ("空 query 抛异常", lambda: _raises(lambda: tools.search_knowledge(""))),
        ("未知库名抛异常", lambda: _raises(lambda: tools.search_knowledge("温度", 3, ["不存在的库"]))),
        ("未创建垃圾目录", lambda: not (ROOT / "知识库" / "向量库" / "不存在的库").exists()),
    ]
    for name, fn in checks:
        ok = False
        try:
            ok = bool(fn())
        except Exception as e:  # noqa: BLE001
            print(f"  [FAIL] {name} -> {e}")
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
        if not ok:
            fails.append(name)

    print("\n=== 结论 ===")
    if fails:
        print(f"FAIL：{len(fails)} 项未通过")
        for f in fails:
            print(f"  - {f}")
        return 1
    print("PASS：全部校验通过")
    return 0


def _raises(fn):
    try:
        fn()
    except Exception:  # noqa: BLE001
        return True
    return False


if __name__ == "__main__":
    raise SystemExit(main())
