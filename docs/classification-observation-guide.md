# 完了済み分類runの観測

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

`scripts/summarize_classification_run.py` は固定WorkInputと完了済みrunを読取り専用で再照合し、JSON・HTMLへ観測結果を出力する。人手goldを受け取る機能は持たず、意味の正しさ・precision/recall/F1・summary忠実性・関連度精度・校正・ECE/Brierは常にnull、人手gold件数0を記録する。これは実行結果の構造と判断範囲の観測であり、人手精度評価ではない。

## 実行

```powershell
uv run python scripts/summarize_classification_run.py --inputs data/poc-frozen-v2/pilot100.jsonl --run data/benchmarks/poc-frozen-v2/benchmark-v1/concurrency-2 --freeze-manifest data/poc-frozen-v2/manifest.json --output data/evaluation/poc-frozen-v2/classification-observation-concurrency-2-v2
```

既存出力directoryを上書きしないため、再実行時は新しい出力名を指定する。旧2,000作品runを観測する場合も、`--inputs data/poc-frozen-v2/work-inputs.jsonl` と対応するrunを指定して同じ処理を使える。未完了・中断・失敗を含むrunは拒否する。今回読み込んだrunは完了済み2並列だけであり、4/8並列の未完了ファイルを読んだり変更したりしていない。追加LLM実行、DB・runner・prompt・モデル・freeze・review packの変更は行わない。

## 再照合とprovenance

入力rawファイルSHA-256をfreeze manifestの `work_inputs_sha256` または `pilot_inputs_sha256` と照合する。result indexのID集合・件数、manifestのselected/completed/failed/pending/stopped、job key・work/expression・input hash・config・completed status、個別durable resultとの一致を検査する。各結果は本番validatorでJSON Schema、taxonomy、入力hash、根拠ID、locator、引用の存在、判定scopeを再検証する。

taxonomyのcanonical JSON hash、schemaファイルhash、promptファイルhashはrun configと一致する必要がある。検証用artifactが更新されてrun当時と異なる場合は、そのまま現行仕様で判定せずエラーにする。別checkout等で当時の検証artifactを用意して再実行する。出典の権利policy ID・許可フラグは固定入力の宣言として集計し、独立した法的義務履行確認へ置き換えない。

観測中の入力・manifest・index・個別結果の変更も検出する。観測JSONにinput/index/manifest/configのSHA、run ID、実行config、freeze manifest SHAと照合キー、100件のdurable結果のhash集合digest、検証artifactのhash、CLIが宣言したusage・経過時間・並列数を記録する。CLI usageはAPI課金の証拠として扱わない。

## 指標

| 指標 | 分母・意味 |
|---|---|
| 軸の判断作品率 | 各軸でpresent/absentを1件以上出した入力数 / 固定入力数 |
| ラベル判断率 | emitted present/absent / 固定入力数×taxonomy登録ラベル数 |
| unknown | explicit unknownラベル、missing_axesに示された軸unknown、未出力ラベル枠を別に数える。未出力をabsentへ補完しない |
| 原言語別 | 固定入力のoriginal_languageのみを使用。英語の表示題・target_language・資料言語から原言語を推測しない |
| 資料種類別 | llm_input_allowed=trueの資料がある入力を種類ごとに集計。複数資料への所属は重なる |
| 資料深度別 | excerpt、synopsis、noteの優先順、いずれもない場合facts。語数・資料品質・人手判断可能性の尺度ではない |
| raw confidence | emitted present/absentだけの件数・平均・min/max・中央値・p95・0.5/0.7/0.9/0.95以上件数。自己申告値であり確率校正ではない |
| 根拠 | reference数、異なるinput/evidence ID数、資料kind・policy ID・directness、quoteとsummaryの有無を観測 |
| 引用一致 | whitespace正規化後に固定許可資料内へ引用文字列が存在するか。文脈やラベルの正しさは未評価 |

JSONには各stratumの6軸内訳も保存する。HTMLは軸・原言語・資料種類・深度ごとの主要集計、引用・summaryの先頭50参照（各240文字まで、切詰めflag付き）、provenanceを表示する。sampleに含まれない原文は元runと固定入力に保持される。表示はHTML escapeし、モデル・資料内の文字列をscriptとして実行しない。

## 実100作品・2並列の観測結果

2026年10月12日。run ID `ce9e704d30a84a0c932bdbe007725234`、model `gpt-6.1-sol`、CLI `codex-cli 0.162.1`、batch size5、2並列、経過763.83秒。100/100結果を固定元・job・durable result・本番validatorと再照合した。

最終成果物は `data/evaluation/poc-frozen-v2/classification-observation-concurrency-2-v2/classification-observation.json` と `.html`。初版directoryは保持し、v2ではHTMLに軸unknown/全軸unknownの件数を明示した。観測JSONの集計内容は初版と同一である。

入力SHAは `58f64f2e2fb368bd480023492db3ffb75e4ddb5be5b3017629c52a2d91ba2db7`、result index SHAは `d7141239d52d95577614ebfb875bfd56022eaafd51380e43a32b5cf7a1c2d33c`、manifest SHAは `183a449133917d791e70d2bf65b6f5a90b7f081e5c394ad410a731d60170277f`、config SHAは `ada5d4ea77cc81336472f2502e4ee14cace6258c8d86cefa1f96d7a432a44a11`。

何らかの判断をした作品52/100、全軸unknown48/100、human review flag93/100。emitted present190・absent0・explicit unknown1、未出力ラベル枠7,009。taxonomy全7,200枠の判断190/7,200=2.64%である。absent0は、残りのラベルが作品に存在しないという意味ではない。

| 軸 | 判断作品数 / 100 | 軸unknown作品数 | 判断ラベル数 / 1,200 |
|---|---:|---:|---:|
| genre | 44 | 56 | 57 |
| theme | 30 | 70 | 54 |
| structure | 14 | 86 | 18 |
| protagonist | 28 | 72 | 37 |
| style | 0 | 100 | 0 |
| atmosphere | 21 | 79 | 24 |

日本語は19/35作品（54.29%）、英語22/35作品（62.86%）で何らかの判断を出した。その他の各言語は2〜3作品の少数であり、ヒンディー語・中国語の2/2判断も精度の成功を示さない。原言語別の全categoryと軸内訳はJSONに残している。

| 最深資料 | 判断作品数 / 対象 | 作品判断率 | 判断ラベル数 |
|---|---:|---:|---:|
| facts | 11 / 43 | 25.58% | 11 |
| note | 10 / 26 | 38.46% | 26 |
| synopsis | 31 / 31 | 100.00% | 153 |

資料種類別はbibliographic52/100、description50/98、subject35/44、note19/35、synopsis31/31で何らかの判断を出した。資料種類・原言語・作品には相関と重複があるため、これらの判断率から資料の因果効果や意味の正しさを判断しない。excerpt入力は0件であり、style判断0件の観測を原言語文体精度の測定へ置き換えない。

190判断のraw confidence平均0.9289、中央値0.95、p95 0.99、0.9以上142件、0.95以上98件。これらの値に対応する実precision・ECE・Brierは人手goldなしのためnullである。

根拠参照210件、異なるinput/evidence組73件、direct172・indirect38。参照資料はdescription2・subject29・note36・synopsis143。引用あり160件すべてで文字列存在を確認し、summaryあり68件（summaryのみ50、quoteとの併記18）であった。summary忠実性・引用の文脈・分類判断の正しさは人手未評価である。policy IDはwikidata-cc0 31参照・wikipedia-screened-prose179参照で、条件付き義務の独立確認は別の監査手順に残る。

## TDD記録

最初に未実装scriptのFileNotFoundErrorを確認した。追加テストではmanifest jobsのwork IDを入れ替えても受理される問題で1 failed / 7 passedを確認し、job keyとinputだけでなく個々のwork/expression一致を検査して修正した。最終8 passed。未知・変更input hash、未完了run、件数欠損、durable/index不一致、job config不一致、unknownと未出力の分離、summaryのみ参照を正しさへ昇格しないこと、HTML escapeを確認している。test fixtureは実データのhuman goldに採用しない。
