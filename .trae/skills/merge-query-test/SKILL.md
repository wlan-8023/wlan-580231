---
name: "merge-query-test"
description: "对 知识库/rag.py 与 知识库/tools.py 的多索引合并检索执行固定回归测试，校验分数排序、命中来源、编号连续与参数边界。当用户要求测试合并查询/向量库检索是否正常，或改动 rag.py、tools.py 后需要验证时调用。"
---

# 合并查询测试

对本地 RAG 的多索引合并检索做回归验证，输出通过/失败清单。

## 覆盖范围

- `rag.search()` / `tools.search_knowledge()` 两个入口
- 合并查询：多库分别召回后按 L2 距离统一排序
- 单库查询：按库名、按目录路径两种指定方式
- 边界：`top_k` 越界夹取、空 query、不存在的库名（且不得创建垃圾目录）

## 执行

在项目根目录 `c:\Users\Administrator\Desktop\AItest` 下运行：

```powershell
& 'C:\Users\Administrator\.local\share\python312\python.exe' .trae\skills\merge-query-test\scripts\test_merge.py
```

退出码 `0` 表示全部通过，`1` 表示存在失败项（会逐条打印 FAIL 原因）。

## 校验项

| 校验 | 判定标准 |
|------|----------|
| 返回条数 | 落在 `1..top_k`（过滤门槛可能使结果少于 k） |
| 排序 | L2 距离严格非递减 |
| 编号 | 从 1 连续递增 |
| 来源 | 每条结果的库名必须属于已发现的索引 |
| 合并生效 | 至少一个用例同时命中 ≥2 个库 |
| 限定查询 | 指定索引时，来源不得越界 |
| 检索门槛 | 结果 L2 ≤ `tools.L2_MAX`(0.9)，且与问题有词语重叠 |
| 无答案兜底 | 无关问题必须返回 `tools.UNKNOWN`（「不知道：…」） |
| 边界 | `top_k=0`→1 条；`top_k=99`→20 条 |
| 异常 | 空 query、未知库名必须抛异常，且不新建目录 |

## 前置条件

- `知识库/向量库/` 下至少存在 2 个含 `index.faiss` 的索引目录
- 已安装 `langchain langchain-community faiss-cpu fastembed`，且 embedding 模型已缓存（离线可用）

## 注意

- 只读测试，不写向量库、不修改任何索引文件
- 测试用例中的 `温度` 是跨库合并的关键用例（同时命中 `知识内容` 与 `冰箱说明书`）
- 若新增索引导致用例失败，先在脚本的 `CASES` 中补充适配的查询
