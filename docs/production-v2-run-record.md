# 2,020入力の分類実行記録 v2

2026-10-12。元2,000作品と新canonical古典原文20作品を対象に、relevance v2の実行がexit 0で終了した。全2,020入力の結果を監査し、派生SQLiteへの保存と追加校正候補の診断を実データで実施した。人手goldは0で、意味的精度・関連度精度・信頼度校正は未評価である。

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

起動時の旧1,000件はcache importの計数だった。終了後の最終manifestと保存状態から、runnerの実再利用1,000件、新規完了1,020件を確認した。古典20件も新canonical ID/input hashの結果であり、旧原文runのIDを付け替えていない。

## 全2,020入力の終了監査

同じsession `66128`のexit 0を観測し、run `b2ba55f2a5fa4812adcde9d1692be6d9`の全selected・保存job・durable result・結果indexを照合した。completed 2,020 / failed 0 / pending 0、停止理由なし。構造有効率100%は固定99% gateを満たすが、ラベルの意味的正解率を示すものではない。旧runの97.5%はそのまま保持する。

所要3,152.471秒、CLI 205回、作品attempt 1,025回、再試行5回、p50 70.112秒・p95 128.191秒。新規1,020入力の204 batchと無効結果の再試行1 batchを独立に照合した。raw finalとagent event final、使用量、成功batchと保存結果が一致し、監査した全呼出しのtool callは0だった。

新実行の観測使用量はinput 5,618,075、output 569,126、reasoning output 30,743、cached input 2,603,648。固有205呼出しのeventから集計し、旧cacheの使用量を再加算していない。API課金額や人手評価の値ではない。移行原本、旧保護ファイル3,668件、cacheの結果値1,000件、固定入力、実行用10 artifactと12保存コピー、CLI binaryの不変を確認した。

- [実終了のproof](../data/evaluation/poc-combined-v2/terminal-proof-v1.json)
- [全件・raw・使用量の独立監査](../data/evaluation/poc-combined-v2/terminal-audit-v1.json)
- [分類の観測JSON](../data/evaluation/poc-combined-v2/classification-observation-v1/classification-observation.json)、[HTML](../data/evaluation/poc-combined-v2/classification-observation-v1/classification-observation.html)

## 資源と品質の確認範囲

旧1,000実行の[資源観測](../data/evaluation/poc-frozen-v2/production-v1-resource-observation/summary.json)は335 sample、うち334が生存targetを含んだ。最大sampleのnative Codex数は8、native working set合計1,329,111,040 bytes、schedulerと子を含む合計1,393,811,456 bytes。不正・欠損metric groupは2件を除外した。新runの[観測](../data/evaluation/poc-combined-v2/resource-observation-v1/summary.json)は258 sample、欠損metric group 0、最大native数8、native working set合計1,308,921,856 bytes、schedulerと子を含む合計1,373,982,720 bytes。どちらも間欠sampleの最大値で、厳密なpeakや全子プロセスのCPU総量ではない。observer session `46830`もexit 0を確認した。

3,194ラベルを出力し、何らかの判定があるのは962作品（47.62%）、全軸unknownは1,058作品。全145,440 taxonomy slot中の判定は2.20%で、省略slotをabsentに変換していない。present 3,194 / absent 0。文体は原文のある古典20作品で31ラベル、原文のないmain2,000はunknown。関連度は数値3,145 / null 49で、known判定中の数値付与率は98.47%。この付与率と作品・ラベルcoverageは別の値である。

根拠参照3,510、quote 2,416件はすべて固定許可入力と一致したが、意味的支持・作品全体の正しさ・関連度精度・校正済み信頼度を証明しない。人手goldは0件で、主要ラベルの品質gateと人手名寄せ評価は未完了である。

## 終了後の反映・診断処理

分類runを変更しない新規6ファイルで、[派生SQLiteへの反映](classification-database-import-guide.md)と[追加校正候補の診断](calibration-candidate-diagnostic.md)を実装した。両担当の停止後、本体で全**769 tests passed / 28.98秒**を確認。rootの独立レビューでWindows runnerのnative separatorを誤拒否する不整合を発見し、runnerと同じPath→strのfixtureでREDを確認して修正した。元runner・入力・実runは変更していない。

取り込み処理は元DBの全テーブル値を保持し、selected入力・source version・work/expression・完了/保留状態・結果と根拠を新しい6テーブルへ保存する。診断は固定main2,000のcalibration100と全400groupから分離したpoolに限定し、追加古典20はidentityを検証して件数へ加えない。known予測の件数と信頼度帯を数量上限として出力し、精度や人手truthを生成しない。

どちらも実handleのterminal終了を観測したcallerが保存する共通proofを要求する。合成fixtureでの検証後、実終了を確認した共通proofを使い、両処理を実データへ適用した。固定400の選定・分割・blind packを維持し、人手未入力の状態を保持している。

2026-10-12 03:31 UTCに、取り込み処理の入力検証を実データで読み取り専用実行した。元combined DBから全2,020 WorkInputを再構成し、固定入力の全input hash、source version、work/expression所属の一致を確認した。SQLite integrity checkは`ok`、foreign key errorは0。元DB・入力・入力manifest・実行contextのbytesは処理前後で不変、実行用10 artifactも固定SHAと一致した。[事前検証記録](../data/development/poc-v2-postprocessing-preflight-v1/preflight.json)に保存している。この検証では実分類結果・terminal proofを読まず、分類DBへの取り込みや校正候補診断は実施していない。

終了後に作成した[派生DB](../data/poc-combined-classified-v2-v1/dataset.sqlite)のSHAは`dce291b9ede240a26347ab7a1308e30425cccdd240a72a78956006318b35b0a0`。新6テーブルにrun 1 / inputs 2,020 / source refs 2,637 / input evidence 6,657 / labels 3,194 / label evidence 3,510を保存した。rootの[独立SQL監査](../data/evaluation/poc-combined-v2/derived-database-audit-v1.json)で元20テーブルの値multiset、全2,020結果値、全ラベルのJSON・数値・nullの一致、FK0・integrity=okを確認した。calibrated値はすべてnull、人手確認済みの値は追加していない。派生DBは分類監査用snapshotとして扱い、後から作品graphを再構築しない。

[実予測の候補診断](../data/evaluation/poc-combined-v2/calibration-candidates-v1/diagnostic.json)はmain2,000と固定calibration100を対象とし、全400から分離したfresh poolは1,600作品 / 1,034 connected groups。既存calibrationの同一scope上限はmetadata 16、実known scored 10で、必要な40件に届かない。追加poolを含め、同一contextの40件・少なくとも1 binの20件という数量条件を満たし得るのは、英語SF（既存4＋pool75、追加known scored最低36）と日本語恋愛（既存5＋pool36、追加最低35）の2ラベルだけだった。evidence/work truth levelの両方に同じ候補が現れるが、4ラベルや倍の標本として数えない。

この数量上限は、人手truthが判定可能であることやwhole groupでの追加配分を保証しない。[診断要約](../data/evaluation/poc-combined-v2/calibration-candidates-summary-v1.json)に保存し、calibratorのfit・追加partition割当・人手注釈の生成は0。閾値を下げず、実人手の正解と必要な追加校正資料を用意してから④の精度・校正を検証する。

追加数量をwhole groupで補う条件も[試算](../data/evaluation/poc-combined-v2/calibration-whole-group-capacity-v1.json)した。全eligible fresh groupを対象とした0/1 covering DPでは、SF36件・恋愛35件の追加known scoredを同時に確保する最小費用は74作品 / 35 groups。これは人手truthが判定可能だと仮定した数量上限の設計で、追加partitionの割当や回答生成は行っていない。既存calibration100の実人手判定と、他ラベルの不足は別に残る。
