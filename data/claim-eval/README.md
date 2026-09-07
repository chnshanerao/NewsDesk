# Claim 关系人工评测

`pairs.jsonl` 是从生产证据库按语言和来源角色分层抽样的 300 对冻结样本；
`manifest.json` 保存随机种子与快照哈希。它不是已经完成的人工金标。

两名不同标注员分别复制样本 ID，独立生成 `annotations_a.jsonl` 与
`annotations_b.jsonl`，每行格式为：

```json
{"pair_id":"…","annotator":"匿名标注员ID","label":"support|refute|unknown","notes":"可选"}
```

第三名仲裁员在不知道系统预测的前提下生成 `adjudicated.jsonl`。标注时不要向标注员
分发 `system_predictions.jsonl`。三份文件都必须覆盖全部 300 个 `pair_id`。完成后由人工
负责人在 manifest 写入明确的 `human_attestation`（三名 reviewer ID），再执行：

```bash
./bin/newsdesk validate-claim-eval --dataset data/claim-eval --strict
```

只有样本关系分布达到最低要求、快照未变化、两名标注员身份不同、第三人完成全量仲裁、
所有标签合法且存在人工确认时，`human_adjudicated_gold` 才会变为通过。程序只能验证
结构和显式声明，不能仅凭三个姓名字符串证明真人身份。
