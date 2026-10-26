# 同一凍結入力に対する分類runのオフライン安定性比較

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

`scripts/compare_classification_stability.py` は、明示した完了済みrunだけを読み、同じ作品・expression・入力・分類設定に対する出力の変動を観測する。通信、追加LLM呼び出し、再分類、人手annotationの作成は行わない。分類の正解率、意味の正しさ、quoteの妥当性、人手gold、確率校正はこの比較から求めない。

## 入力と機械的照合

各runは既存のread-only observerで個別に照合する。固定WorkInputのキー重複、選択数・完了数・結果数、pending/failed/stopped、各結果のschema、WorkInput hash、taxonomy/prompt/model/CLI version、job key/config/status、結果indexと個別durable結果の一致を確認する。taxonomy、schema、promptの実ファイルhashはrun configに一致する必要がある。freeze manifestを指定した場合は入力ファイルSHAを照合する。run間の入力SHAとcanonical config SHAは同一でなければ比較を拒否する。並列度はmanifestの実行情報であり、分類configには含まれない。

既存observerの根拠参照・引用文字列に対する構造検証は再利用する。引用の意味や、その引用からラベルが正しいかは裁定しない。本比較の差分明細にはquote/summaryを複製せず、ラベル状態とraw confidenceだけを記録する。

各入力・manifest・index・durable結果のSHAとrun IDはJSON/HTMLのprovenanceに保持する。ディレクトリの一括探索をせず、指定されたrunからだけ読み出す。本番runや未完了runを自動選択する経路はない。出力ディレクトリは新規作成に限り、既存の観測レポートは上書きしない。

## 分母と4状態

各作品×taxonomyラベルを、次の4状態に区別する。

| 状態 | 定義 |
| --- | --- |
| `present` | ラベルが出力され、decisionがpresent |
| `absent` | ラベルが出力され、decisionがabsent |
| `explicit_unknown` | ラベルが出力され、decisionがunknown |
| `unreported` | そのラベルがlabels配列に出力されていない |

`missing_axes` のunknownは軸単位の情報として別の遷移表に記録する。未報告ラベルをabsentやexplicit unknownへ変換しない。present/absentをknownと呼び、以下の分母を区別する。

| 指標 | 分子 / 分母 |
| --- | --- |
| known union一致率 | 同じpresent/absent状態の枠数 / 片方以上でknownの枠数 |
| both known一致率 | 同じpresent/absent状態の枠数 / 両方でknownの枠数 |
| 作品のknown集合一致率 | `(label_id, decision)`集合が完全一致する入力数 / 片方以上にknownがある入力数 |
| 3run共通known状態率 | 全runで同じpresent/absent状態の枠数 / いずれかのrunでknownの枠数 |

両方が未報告などのneither-known枠は、件数として保持するが一致率の分母には含めない。known集合が両方空の作品も作品一致率の分母外とする。分母0の場合はnullにし、100%を返さない。both known一致率はcoverageの変化を取り除いた限定的な観測なので、known→unreported、unreported→known、known↔explicit unknown、present↔absentの件数を併記する。

confidenceの差は両方knownの同一ラベル枠だけで、右run−左runを計算する。平均符号付き差、平均/中央値/p95/最大絶対差、同値/増加/減少件数を保持する。同じdecisionの枠に限った統計も別に保持する。raw confidenceはモデルの自己申告値であり、これらの差はBrier/ECEや確率校正を表さない。

集計は全体、軸、固定入力の原言語、許可された入力資料の最深層別に出す。資料深度の優先順位はexcerpt→synopsis→note→factsで、factsはそれ以前の3種類の許可資料がない入力を指す。原言語は`original_language`を使い、翻訳先言語から推測しない。細分類の小さな件数には推論・信頼区間を付けない。同一作品内のラベルは独立試行でもない。

## 実行方法

以下は実際に完了したpilot100の3run比較。出力先が既存の場合は、別の新規出力名を指定する。

```powershell
uv run python scripts/compare_classification_stability.py `
  --inputs data/poc-frozen-v2/pilot100.jsonl `
  --freeze-manifest data/poc-frozen-v2/manifest.json `
  --run 2=data/benchmarks/poc-frozen-v2/benchmark-v1/concurrency-2 `
  --run 4=data/benchmarks/poc-frozen-v2/benchmark-v1/concurrency-4 `
  --run 8=data/benchmarks/poc-frozen-v2/benchmark-v1/concurrency-8 `
  --output data/evaluation/poc-frozen-v2/classification-stability-concurrency-2-4-8
```

出力は`classification-stability.json`と`classification-stability.html`。HTMLには軸・原言語・資料深度の比較、4状態の遷移、軸unknownの遷移、状態差分の作品一覧、provenanceを表示する。タイトル等はHTML escapeする。JSONには各作品のknown集合一致率、3run集計、confidence詳細、品質未検証のnull項目も保持する。

APIは`compare_runs(inputs_path, {name: run_dir, ...}, freeze_manifest_path=...)`と`write_comparison(report, output_dir)`。同一入力の任意の2run以上に対応する。将来の全2000作品比較にも同じ照合条件が必要であり、今回の100作品の結果を全2000作品へ外挿しない。

## 2026-10-12の実観測

対象は同じpilot100、各run selected/completed100、pending/failed0、stopped=false。共通modelはgpt-6.1-sol、CLIは0.162.1、batch5、reasoning medium、web disabled、sandbox read-only。

| run | run ID | knownがある作品 | present | absent | explicit unknown | unreported |
| --- | --- | ---: | ---: | ---: | ---: | ---: |
| 並列2 | ce9e704d30a84a0c932bdbe007725234 | 52/100 | 190 | 0 | 1 | 7009 |
| 並列4 | 1ad08eb6a41a42bf9495f142246da4c6 | 53/100 | 189 | 0 | 0 | 7011 |
| 並列8 | 9b50884bb0054ed2aa0b3e23224d41c4 | 52/100 | 189 | 0 | 0 | 7011 |

7200登録枠のうち、3runのknown unionは214枠。全runで同じknown状態だった枠は167で、167/214=78.04%。同じknown状態はすべてpresentであり、absentを判定した枠は今回ない。多数決ラベルやconsensus正解データは作成していない。

| 比較（左→右） | 同じknown / known union | union一致率 | both known | known→未報告 / 未報告→known | 作品known集合一致 | confidence平均絶対差 / 最大 |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| 2→4 | 176/203 | 86.70% | 176/176 | 14 / 13 | 33/53=62.26% | 0.01193 / 0.07 |
| 2→8 | 172/207 | 83.09% | 172/172 | 18 / 17 | 31/52=59.62% | 0.00959 / 0.07 |
| 4→8 | 173/205 | 84.39% | 173/173 | 16 / 16 | 32/53=60.38% | 0.01214 / 0.08 |

両runがknownの枠はすべて同じ状態だったが、報告有無は27、35、32枠で変わった。both knownの100%を全ラベルの安定性や正解率と解釈できない。2→4と2→8にはexplicit unknown→unreportedが各1枠あり、4→8にはない。present/absent反転およびknown↔explicit unknownは全ペア0枠。

軸別のknown union一致枠/分母は次のとおり。styleにはknownがなく、一致率はnullである。

| 軸 | 2→4 | 2→8 | 4→8 |
| --- | ---: | ---: | ---: |
| genre | 52/60 | 52/59 | 52/57 |
| theme | 48/58 | 49/56 | 47/56 |
| structure | 17/20 | 15/20 | 16/20 |
| protagonist | 36/39 | 36/45 | 36/46 |
| style | 0/0（null） | 0/0（null） | 0/0（null） |
| atmosphere | 23/26 | 20/27 | 22/26 |

軸のunknown↔decided変化もJSON/HTMLに保持している。例えば2→8のstructureはdecided→unknown2作品・unknown→decided2作品、protagonistはunknown→decided2作品。styleはすべてのrunで100作品が軸unknownであり、これを一致成功に数えない。

| 原言語の区分 | 作品数 | 2→4 | 2→8 | 4→8 |
| --- | ---: | ---: | ---: | ---: |
| ja | 35 | 57/67 | 57/67 | 55/65 |
| en | 35 | 72/84 | 72/87 | 73/86 |
| 他原言語・不明の各区分 | 各2～3 | 個別にJSON/HTMLへ記録 | 同左 | 同左 |

jaのknown union不一致は各ペア10枠、enは12/15/13枠。他原言語はar/bn/de/es/fr/hi/it/ko/pt/ru/zh、およびunknownを個別に記録する。ar/pt/unknownは全ペアknown union0のため一致率nullである。koはペアによって分母0または1であり、言語別の安定性を保証できる試行数はない。

| 許可資料の深度 | 作品数 | 2→4 | 2→8 | 4→8 |
| --- | ---: | ---: | ---: | ---: |
| facts | 43 | 11/11 | 11/11 | 11/11 |
| note | 26 | 22/28 | 22/29 | 20/29 |
| synopsis | 31 | 143/164 | 139/167 | 142/165 |

facts43作品のうち既知ラベルが生じた範囲は11枠に限られ、表の一致を全43作品の分類成功と解釈できない。synopsisに報告有無の変化が21/28/23枠あったことは観測できるが、資料の意味的品質や並列度が原因だったかは確認していない。各並列度1runのみで、モデルの確率的変動と並列度の効果を分離する実験ではない。

固定pilot SHAは`58f64f2e2fb368bd480023492db3ffb75e4ddb5be5b3017629c52a2d91ba2db7`、共通config SHAは`ada5d4ea77cc81336472f2502e4ee14cace6258c8d86cefa1f96d7a432a44a11`。全runのschema/taxonomy/promptとdurable結果のSHAはレポート内に保持している。

人手goldは0件。`accuracy`/`precision`/`semantic_correctness`/`calibration`/`ece`/`brier`はnull、`human_quality_verified`はfalseのまま。判定安定性の機械観測を行ったことは、計画④の人手品質評価を完了したことを意味しない。

## テスト

初回はscript未実装によるcollection failureを確認してから実装した。synthetic fixtureは構造テスト専用で、人手goldへ取り込まない。

```powershell
uv run pytest tests/test_classification_stability.py tests/test_classification_summary.py -q
```

4状態・present/absent反転・未報告による分母水増し防止、neither-knownのみの場合のnull、既知集合の空入力除外、confidence差、原言語/深度区分、未完了run拒否、個別には有効でも異なるconfigのrun拒否、明示していないrunの非読取り、入力/元結果不変、HTML escapeを確認する。
