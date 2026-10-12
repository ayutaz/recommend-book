# 書誌補充後の差分分類・推薦 v3 実行記録

2026-10-13。Open Library補充後の変更268作品をSol CLIで再分類し、2,020作品の新しい推薦snapshotへ反映した。終了した実プロセスと全結果を照合した。人手による分類・名寄せ品質、関連度精度、信頼度校正、世界の小説への推薦品質は未検証。

## 差分分類の実行

分類のnative sessionは`25985`、exit codeは0。実際に分類へ渡した入力pathは **`data/production-inputs/poc-openlibrary-enriched-v1-delta-v1/ordered-inputs.jsonl`**。推薦生成へ渡す訂正後v2入力とraw bytes・SHAが一致する。v2を分類開始前に作成した記録ではない。

| 項目 | 実測値 |
| --- | --- |
| 対象 / 完了 / 失敗 / 未完了 | 268 / 268 / 0 / 0 |
| 再利用 / 再試行 | 0 / 0 |
| Sol | `gpt-6.1-sol`、medium reasoning |
| CLIが報告したversion | `codex-cli 0.162.1` |
| batch / 開始・終了並列数 | 5 / 8・8 |
| CLI呼出し | 54 |
| 経過時間 | 1,027.00秒（約17分） |
| 入力 / 出力tokens | 1,572,857 / 206,213 |
| reasoning / cached input tokens | 10,369 / 235,520 |
| Windowsプロセス群のsampled memory最大 | 1,197.1 MiB |

使用量はCLIの`turn.completed`イベントによる観測値で、請求額ではない。memoryはlive descendantsを含む77回のサンプルであり、厳密なpeakではない。終了状態は`stopped=false`。実結果は[run manifest](../data/runs/poc-openlibrary-enriched-v1-delta-v1/manifest.json)、[機械観測](../data/evaluation/poc-openlibrary-enriched-v1-delta-v1/run-observation-v1.json)、[終了proof](../data/evaluation/poc-openlibrary-enriched-v1-delta-v1/terminal-proof-v1.json)に保持した。

採用ラベルは808、すべて`present`かつ`work` scope。genre321、theme186、structure82、protagonist113、atmosphere106、style0。268作品中207作品にラベルがある。関連度数値769 / null39、根拠参照980。文体を書誌情報から補作していない。null関連度の推薦用重み1.0は既存方針を維持し、raw confidenceを重みに使わない。

## CLI表記の事後訂正

v1準備では、起動前のraw観測`codex-cli 0.162.1`を誤って`0.162.1`へ短縮して指定した。そのためv1の準備configとactual run configは一致しない。元v1と実結果・job keyを修正せず、実観測の文字列を明示した **新v2 namespace** を実行後に作った。

- ordered268、旧順序のall-inputs2020、各old/new input hash、core10のbytesはv1/v2で同一。
- promptの`output_identity.cli_version`だけが異なる。268件すべてでその一箇所のみの差を照合した。単作品promptは実batch stdinの捕捉記録ではない。
- v2のconfigはactual run configと完全一致する。v1全287ファイルは前後不変で、LLM再実行と結果のmetadata付替えは0。
- proofの`execution_context_binding_kind`は`post_run_cli_observation_correction`。`cli_profile_sha256_before/after`は **訂正したcontextの終了監査前後**を指し、v2の起動前snapshotを示さない。
- 許可されていないOpen Library description112件は、v2の許可input/promptへの混入0。書誌の利用条件を本文へ広げていない。

詳しくは[準備と訂正の監査](enrichment-classification-preparation.md)を参照する。独立レビューでも訂正の時系列・scopeを確認し、追加blockingはなかった。

| 対象 | SHA256 |
| --- | --- |
| 差分ordered268（v1/v2共通） | `5099df8aa72166dfefea1359c1c58f306330f32d5e7be470ee754a5a7c1dd5aa` |
| actual run manifest | `51eff82668742e280d12c81cc3a4785272779e3b93d16578e1ac799a6f92a7f0` |
| actual results | `6848a34c6c0163554e78d3c9c45b2bb8c4e25735305660b288d9a3e2253dabbb` |
| 訂正後v2準備manifest | `86dfc45be984bba4ddd4b07da735037d3b9fa2ce05f8da66dbd61c0428ae3ed4` |
| 別correction audit | `94d60919ba1763dc07858a1773617feebde59b5cf5740f5b217f86e577e70049` |
| terminal proof | `9f7fad4e111917081faa78b62df0959c4f789f1c6d015524115474ec10eb86cb` |

## 推薦生成

以下をnative session`40537`で実行し、exit code 0を観測した。出力は新しい`data/recommendation-poc-v3`へ保存し、v1/v2を上書きしていない。

```powershell
uv run --offline recommend-book recommend `
  --world-database data/poc-combined-classified-v2-v1/dataset.sqlite `
  --narou-database data/reference-code/narou-personal-recommender/data/narou.sqlite `
  --narou-annotations data/reference-code/narou-personal-recommender/data/processed/content-llm/annotations.json `
  --enriched-database data/poc-openlibrary-enriched-v1/dataset.sqlite `
  --delta-inputs data/production-inputs/poc-openlibrary-enriched-v1-delta-v2/ordered-inputs.jsonl `
  --delta-manifest data/production-inputs/poc-openlibrary-enriched-v1-delta-v2/manifest.json `
  --delta-results data/runs/poc-openlibrary-enriched-v1-delta-v1/results.jsonl `
  --delta-run-manifest data/runs/poc-openlibrary-enriched-v1-delta-v1/manifest.json `
  --delta-terminal-proof data/evaluation/poc-openlibrary-enriched-v1-delta-v1/terminal-proof-v1.json `
  --output data/recommendation-poc-v3 --limit 100
```

SQLの作品集合・旧immutable source versions・全crosswalk・input hash・actual completed jobs・引用所属を確認した。変更268作品の分類を置換し、不変1,752作品を保持した。代表canonical IDが変わった97作品も対応を確認した。旧分類と新分類のunionや、旧結果の新IDへの付替えはない。新書誌DBへ分類テーブルは書かず、[world-works snapshot](../data/recommendation-poc-v3/world-works.json)として保存する。

| 件数 | 推薦v2 | 推薦v3 |
| --- | ---: | ---: |
| 世界作品 | 2,020 | 2,020 |
| 推薦に使える分類特徴あり | 962 | 994 |
| 分類特徴なし | 1,058 | 1,026 |
| 正の類似がありスコア算出可能 | 673 | 696 |
| スコア算出不能 | 1,347 | 1,324 |
| 保存した推薦候補 | 100 | 100 |
| 対象の本人実評価とID一致 | 0 | 0 |

増加した情報量を分類精度や推薦精度の改善と呼ばない。本人の世界作品の履歴入力は0件で、全2,020作品と推薦100件の読書状態はunknown。対象作品の本人実評価も作成していない。

元のなろう335作品の5分割評価は、v2/v3で全fold・全方式の順位、score、status、指標、eligible pool、split membershipが同一。共通分類の個人方式nDCG@10=0.5472 / 高評価率@10=0.52、人気順0.6340 / 0.56という結果は変わらない。native14テーマの根拠provenanceを保持する修正により、診断の根拠件数とそのcoverage集計が変わった。入力/split SHAの変更をsplit membershipの変更として扱わない。

## 独立監査と動作確認

[v3独立監査](../data/evaluation/recommendation-poc-v3-v1/audit.json)でsource10・code11・output8のraw SHAを全件照合し、overlay/historyが参照する各1,006件のsource SHAも一致した。全world IDが新SQLiteのresolved canonical集合に一致し、実run全971ファイルもoverlay照合前後不変。v3 manifest SHAは`00105ee6f8cd1fe0bb8d563b0eba5bb2b317276796e32977fa726678ad676a8b`。

[synthetic履歴の全97ID確認](../data/evaluation/recommendation-poc-v3-v1/synthetic-canonical-history-audit-v1.json)では、実SQL対応を再照合したうえで、旧IDを既読97件とした場合に新IDをすべて除外（eligible1,923）、未読確認済み97件とした場合にその97件のみ採用、unknown97件では未読確認済み0件、旧未読/new unknownの別名競合でも0件を確認した。これは共通候補除外処理のsimulationで、本人履歴・正解データ・推薦品質の証拠ではない。元作品・DB・コードは不変。

Chromeで[実HTML](../data/recommendation-poc-v3/recommendations.html)の100 article、日本語分類、著者・年代、推薦理由と出典リンクを確認した。「未読確認済み」は0件と案内を表示し、「すべて」へ戻すと一覧が復帰する。画面検証はローカルloopbackで行い、実評価入力・外部公開は行っていない。

ローカル本体テストは`uv run --offline pytest -q`、**973 passed / 48.56秒**、native session`10590` exit0。CLI表記差の新fixture、overlay、履歴対応、出典・権利・分類・推薦の既存テストを含む。GitHub hosted CIは未実行。

## 嗜好テーマの対応不足

[定義比較の集計](../data/evaluation/recommendation-poc-v3-v1/feature-bridge-semantic-audit-v1.json)では、参照335作品のnative14テーマ・1,085出現に対し、共通分類は6次元・433出現。未対応11テーマを現72ラベルの定義と比較し、無条件に意味同値となる対応は見つからなかった。

例えば、nativeの「無双・強さ」と社会的な権力、能力の再評価とアイデンティティは別概念である。comedyのcentral40件と世界側humorous72作品は、持続・反復する笑いの根拠を要する。explorationのcentral41件とjourney89/adventure113作品は、連続移動と危険な冒険という条件を区別して確認する必要がある。secondaryの復讐45件・恋愛48件も、現分類が求める中心性を自動で満たしたとしない。

この診断で対応設定・taxonomy・順位・分割は変更していない。新しい推薦概念または条件付き対応を検討する場合は別versionで根拠を再確認し、既存native14次元への拡張だけでは改善しなかった過去評価も比較に残す。件数の多さから性能向上を推定しない。

## 残る完了条件

1. blind20→400作品と名寄せ400ペアの人手判定は0件。分類precision/recall/F1、関連度MAE・根拠の意味的支持・名寄せ品質・信頼度校正を検証するには実注釈が必要。
2. 特徴なし1,026作品と、特徴があっても嗜好に共通する特徴がない作品の内訳を確認し、利用条件を満たす資料から補充する。新入力への分類は別versionとし、旧固定評価packへ付け替えない。
3. 人気順を下回る原因を[誤差分析](recommendation-error-analysis.md)で切り分け、開発用評価で改善案を比較する。全foldを見た探索結果を未使用holdoutの成功としない。
4. 世界の小説の実既読・未読・評価履歴が得られた場合に明示IDで接続し、対象分野の推薦品質を検証する。既存のなろう評価だけでは対象分野の精度を確定できない。
5. 品質、資料の利用条件、処理コストを確認して1万〜3万作品へ拡張する。現PoCの機械成立を10万作品の品質保証へ一般化しない。
