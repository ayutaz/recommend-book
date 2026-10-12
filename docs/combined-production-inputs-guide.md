# Combined 2020 の新規本番入力

2026-10-12、`data/production-inputs/poc-combined-v1-v1/ordered-inputs.jsonl` を新規生成した。これは入力準備のみ。実行中の本番 1k、元2k入力、`data/runs`、共通モデル/runner/prompt/schema/taxonomy/sources/jobs を変更せず、stage2・LLM・Web は起動していない。

```powershell
uv run python scripts/prepare_combined_production_inputs.py --output data/production-inputs/poc-combined-v1-v1
```

実行 exit code は0。元 `data/production-inputs/poc-frozen-v2-v1/ordered-inputs.jsonl` の **2000行・順序・改行・4523088 bytes を完全保持**し、combined DB の `prose-work-inputs.jsonl` にある new canonical 20行・113725 bytes を末尾に byte-identical に追加した。全体は2020行・4636813 bytes。最初の1000行も元本番入力と同一。

## 検証と品質 scope

元ordered manifest、frozen processing context、その6snapshot/現行ファイル、combined manifest が列挙する全 durable outputs のSHAを確認した。main 2000 は frozen input と、新2020全件は combined input と work ID・input hash・source-version refs が一致する。crosswalk20のcanonical ID/hash/active version refs/immutable旧版保持/人手falseを照合した。旧 `ws:*` ID の採用、同一ID重複、旧分類の再結合は禁止し、上書き・入力tree内出力・改行未終端の元行も拒否する。元行を再serializeして修復しない。

`input-comparison.json` は2020件のID/hash/source refs、`prose-id-crosswalk.source.json` はcombined crosswalkのbyte copy。新入力は8接続/12保留の本文scope・権利・locatorを含むcombined WorkInputをそのまま継承する。分類結果のコピー・ID書換え・有効分類への移植はない。

main review400のpack自己SHA、ordered manifestへのbinding、元WorkInputとの一致、split_manifestの自己SHAを検証した。main review/splits の内容・membershipは不変。追加20へmainのpartitionを新規割当していない。追加20の品質評価は別scopeで、**独立holdoutではない**。正規化display nameによるmainとの完全一致は今回0件だが、alias/authorityの独立性を確認した結果ではない。追加20の中でも Austen、Dickens、Stevensonなど同じ著者の複数作品がある。`supplement-evaluation-scope.json` はこの制限と人手評価未了を記録する。

専用TDD8件・既存production入力テスト5件、計 **13 passed**。CRLF/Unicode行の保存、順序・suffix、入力/provenance保護、重複/旧ID/hash/ref/context改変拒否、出力境界、execution context未作成を検証した。

## 入力 provenance と将来の execution version

新dirの `processing-context.json` は **旧 frozen context のbyte copy**で、後続実行の新しいcontextを自動取得・更新していない。manifestの `processing_context_use` は旧入力provenance専用、`execution_context_created=false` / `execution_context_auto_updated=false` を明示する。

rootは、known verdictでも中心性不明なら relevance null を許すprompt/schemaと、旧validationの一律拒否の不整合を調査している。この準備はその分類出力契約を修正・承認・再検証していない。旧contextを後続実行が使用できるとの保証ではない。stage2はrootが旧live終了後に、validation次版・execution context/hash/cacheconfig・旧completed再検証の扱いを別versionで決定してから起動する。新execution versionは入力provenanceと別管理し、この旧contextや元入力を上書きしない。

## SHA の実測

保護40ファイル（元ordered/context/共通models・jobs/combined durable outputs/review資料等）の実行前後SHAは全一致。最終チェックでも全protected/output SHAと2020 hash/ref一致を再確認した。増加中のlive結果へSHA固定・書込みを行っていない。

| 対象 | SHA-256 |
| --- | --- |
| 新2020 ordered inputs | `bfd575c1ec9da9e589f75f4deaa298ea875257c3de8b5ed82b23a85365ec472c` |
| 元2000 ordered prefix | `69179956b2ce41dbf89c7d8bb8ecb187d3760f66935ee20e8e31e7ee9e375305` |
| 追加20 byte suffix | `c07c2a4d4a3a6a9c2da717f6b59091486eea8c858aa6e5c1018af2572ffd68b4` |
| 元ordered manifest | `2346ec20ca1760f7aff3c044694961f421976742fb9d4d70d7c79b7d0b34be9d` |
| 旧context（新dir copyも同一） | `86381c914c1fb31a94e9078ebe7faf77f2685f399485dce0ad028857f3e09984` |
| 元1000行 prefix | `6d5f85b3eb2d50e7a05b33a07d931723c34dab97581c4f78d774ce0857dc55c2` |
| 新manifest | `520a3d80c6daf533fbae0f8e32b25186823b71a7a08c66640bd7ecff32c97d84` |

SQLiteや分類結果は新dirへ複製していない。新dirはversioned入力と監査記録であり、分類実行・精度評価・人手gold・校正の完了を意味しない。
