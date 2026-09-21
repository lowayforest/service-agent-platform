# 评测集

`initial-20.jsonl` 是三份首批真实样本对应的初始评测基线，包含 PDF 表格、法规、XLSX
目录、无答案拒答和实时问题边界五类问题。

`expanded-30.jsonl` 对应 28 份扩展样本，增加法规、技术标准、历史公共服务数据、碍航物
表格和跨文档比较。两个文件一起运行构成 50 题扩展评测集。

评测集本身可以提交 Git，但每次执行产生的完整回答保存在 `data/evals/`，该目录已被
`.gitignore` 排除。后续使用未公开资料编写评测题时，应先判断题目、标准答案和文件名是否
可以进入版本库；不能公开的评测集也应放在 `data/evals/`。

运行方式：

```bash
python -m scripts.evaluate \
  evals/initial-20.jsonl \
  evals/expanded-30.jsonl \
  --base-url http://127.0.0.1:8001
```

命令返回码为 `0` 表示全部通过，`1` 表示至少一题失败。失败并不一定代表模型错误，也可能
是标准关键词写得过严；必须查看 `data/evals/latest-results.jsonl` 后再判断。
