# 2,020入力の分類実行記録 v2

2026-10-12。元2,000作品と新canonical古典原文20作品を対象に、relevance v2の実行を開始した。現在の状態は実行中であり、最終件数・再利用件数・追加使用量はterminal後に監査する。

## 旧1,000入力の終了と再検証

旧run `poc-frozen-v2-production-v1`はexit 0、selected 1,000 / completed 975 / held 25 / pending 0、停止理由なし。所要4,040.044秒、CLI 222回、作品attempt 1,110回、再試行110回、p50 68.963秒・p95 123.224秒だった。旧validatorでの構造有効率は97.5%であり、固定99% gateは未達として保持する。

観測使用量はinput 6,073,392、output 636,715、reasoning output 33,622、cached input 2,425,728。`turn.completed` eventに由来し、API課金額ではない。25 heldはいずれもknown判定に関連度数値を必須とする旧validatorのエラーで、prompt/schemaが許容する中心性不明のnullとの不整合だった。

本体でv2を採用し、全655テスト成功を確認した。元runをterminal cache migrationで再検証し、新namespaceへ1,000件をimportした。975件は旧completedのbytesを保持、25件は有効なraw finalからJSON値を保持して抽出した。全batchのschema/identity/根拠・quote/locator、final/event整合性を検証し、旧保護ファイル3,668件と結果値1,000件の不変を独立監査した。移行中の追加LLM/CLI呼び出しは0、新usageは空。旧構造有効率を書き換える処理ではない。

- [旧terminal監査](../data/evaluation/poc-frozen-v2/production-v1-terminal-audit/audit.json)
- [移行原本](../data/runs/poc-combined-production-v2/migration-manifest.json)、[独立移行監査](../data/evaluation/poc-frozen-v2/production-v2-cache-migration-audit/audit.json)
- [契約変更・採用記録](relevance-v2-development-verification.md)、[移行手順](classification-cache-migration-guide.md)

## 新しい実行条件

[実行context](../data/execution-contexts/poc-combined-production-v2-v1/manifest.json)に、入力bytes、ソースmanifest、10件のコード・設定artifact、新run config、処理上限を保存した。入力内の旧processing_contextは入力provenanceとして保持し、新実行policyと分離している。preparerの状態は準備時点の`prepared_not_started`のまま、実際の起動観測は別の`launch-observation.json`に保存した。

| 項目 | 固定値 |
| --- | --- |
| 入力数・SHA | 2,020 / `bfd575c1ec9da9e589f75f4deaa298ea875257c3de8b5ed82b23a85365ec472c` |
| model / CLI | `gpt-6.1-sol` / 実測`codex-cli 0.162.1` |
| validation | version 2.0 / SHA `7e8cbcc99d3ac45c1481161fab37b4758c789ef0d68e60a64e66c72c274f7838` |
| prompt / taxonomy | 各version 1.0、変更なし |
| parallel / batch | 8 / 5 |
| timeout / run limit | 900秒 / 7,200秒 |
| observed token limit | input + output 8,000,000、投入済み処理の完了分は超過し得る |
| 制限時の動作 | 最初のrate limitで新規投入を停止 |
| 起動確認 | session 66128、2026-10-12 03:00 UTC時点でlive |

起動直前に全10 artifact・入力・manifest・CLI binaryのSHAと、移行cacheのconfigを再照合した。実行中は分類コード、prompt、schema、taxonomy、入力を変更しない。モデル記憶・追加Web検索・資料の再収集を分類根拠へ混ぜない。

```powershell
uv run --offline recommend-book classify `
  --input data/production-inputs/poc-combined-v1-v1/ordered-inputs.jsonl `
  --output data/runs/poc-combined-production-v2 `
  --model gpt-6.1-sol --concurrency 8 --batch-size 5 --timeout 900 `
  --max-run-seconds 7200 --max-observed-tokens 8000000 --stop-on-rate-limit `
  > data/runs/poc-combined-production-v2/cli-console.log
```

旧1,000件はcache importの計数であり、runnerの実再利用件数・新規1,020入力の完了件数は最終manifestから確認する。古典20件は新canonical ID/input hashの結果を要求し、旧原文runのIDを付け替えない。

## 資源と品質の確認範囲

旧1,000実行の[資源観測](../data/evaluation/poc-frozen-v2/production-v1-resource-observation/summary.json)は335 sample、うち334が生存targetを含んだ。最大sampleのnative Codex数は8、native working set合計1,329,111,040 bytes、schedulerと子を含む合計1,393,811,456 bytes。不正・欠損metric groupは2件を除外した。これらは間欠sampleの最大値で、厳密なpeakや全子プロセスのCPU総量ではない。新runも別のresource fileで観測する。

人手goldは0件。Schemaや引用所属が有効でも、意味的支持・作品全体の正しさ・関連度精度・校正済み信頼度を証明しない。固定400の校正100は数量不足のため、実known予測・confidence帯・実行contextと、全400から分離した候補poolを確認して追加レビューの範囲を決める。主要ラベルの品質gateと人手名寄せ評価は未完了である。

## 終了後の反映・診断処理

分類runを変更しない新規6ファイルで、[派生SQLiteへの反映](classification-database-import-guide.md)と[追加校正候補の診断](calibration-candidate-diagnostic.md)を実装した。両担当の停止後、本体で全**769 tests passed / 28.98秒**を確認。rootの独立レビューでWindows runnerのnative separatorを誤拒否する不整合を発見し、runnerと同じPath→strのfixtureでREDを確認して修正した。元runner・入力・実runは変更していない。

取り込み処理は元DBの全テーブル値を保持し、selected入力・source version・work/expression・完了/保留状態・結果と根拠を新しい6テーブルへ保存する。診断は固定main2,000のcalibration100と全400groupから分離したpoolに限定し、追加古典20はidentityを検証して件数へ加えない。known予測の件数と信頼度帯を数量上限として出力し、精度や人手truthを生成しない。

どちらも実handleのterminal終了を観測したcallerが保存する共通proofを要求する。現時点の検証は合成fixtureであり、実runへの適用・派生DB公開・実際の候補診断は終了後に残る。固定400の選定・分割・blind packを維持し、人手未入力の状態を保持する。
