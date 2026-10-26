# 評価と人手レビューの実行手順

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

2026-10-13追記: 以下は人手注釈を利用する場合の既存機能・手順である。ユーザー工数0で進める現在の工程は[自動完了計画 v2](autonomous-completion-plan-v2.md)に従い、担当が外部の既存注釈と別namespaceのAI silverを処理する。ユーザーによるフォーム入力・exportは必須ではない。旧human packにAI注釈を取り込まない。

## 実装済みの範囲と未完了の範囲

`recommend_book.evaluation` は、分類結果と明示的な正解データを比較する。レビュー用HTML、未入力の作業一覧、固定分割、注釈インポート、JSON/Markdownレポートを生成できる。これらの生成は人手判定の実施を意味しない。実際のレビュー、独立した2名の判定、不一致の裁定、主要ラベルの品質目標達成は、注釈を集めて評価するまで未確認である。

人手注釈がないレポートは `human_validation_pending`、校正器がない場合は `uncalibrated` になる。外部参照との一致やSol同士の一致から、人手精度・作品内容の正しさ・校正済み確率を作らない。

## API

```python
from recommend_book.evaluation import (
    evaluate_results, write_report, create_review_pack, import_annotations,
    create_split_manifest, evaluate_resolution_pairs,
)
```

`evaluate_results(results, gold, taxonomy)` は辞書のリストと共通Pydanticモデルを受ける。分類器の `decision`、`raw_confidence` を使用する。互換入力として `verdict`、`confidence_uncalibrated`、`confidence` も読み取れる。`write_report(report, output_dir)` は `report.json` と `report.md` を書く。詳細な帯別・層別結果はJSONに含まれる。

`create_review_pack(inputs, results, output_dir)` は `review.html`、`review_pack.json`、未入力状態の `review_tasks.csv` を生成する。ラベルは結果と `config/taxonomy.json` から取得する。公開・サーバ起動・送信は行わない。`split_manifest.json` が同じ出力先にあれば、その分割とグループIDを使う。

## 20作品の試行から400作品へ

まず20作品について資料を確認し、入力資料への忠実性の判定時間、判断不能の割合、作品内容の確認に必要な追加資料を記録する。HTMLの作業状態はブラウザのローカルストレージに保存される。エクスポートにはセッションの経過時間が付くが、これは読書時間や作品ごとの実作業時間を自動的に測定した値ではない。試行では開始・終了時刻、中断時間、担当人数を別途記録する。

```powershell
uv run python -m recommend_book.evaluation review --inputs data/evaluation/trial20.jsonl --results data/runs/poc/results.jsonl --output data/evaluation/trial20
```

生成されたHTMLをブラウザで開く。担当IDを入力し、評価対象を「入力資料への忠実性」または「作品内容の正しさ」から選ぶ。各ラベルについて `present / absent / unknown`、関連度、根拠IDを入力する。未判定は空欄のまま残す。`unknown` はレビューした結果として判定不能だった場合に選ぶ。モデル判定は初期状態で隠れており、表示した注釈には独立判定ではないことを記録する。二つの評価対象の作業状態は別々に保存される。

作品内容の正解を付ける場合は、作品を読んだという宣言、または十分な資料を確認した宣言とその参照が必要になる。コードは宣言の形式を検証するが、読了や資料の十分性を機械的に証明できない。この制約を監査記録に残す。

400作品の基本セットは開発150・校正100・最終評価150で固定する。

```powershell
uv run python -m recommend_book.evaluation review --inputs data/processed/work_inputs.jsonl --results data/runs/poc/results.jsonl --output data/evaluation/v1 --split
```

著者名・シリーズの連結成分をグループ化し、同じグループを分割しない。固定シード1729、原言語・年代の層を交互に選ぶ順序を使い、各分割の正確な件数を組み立てる。言語・年代・地域の件数をmanifestに記録する。ただしこれは便宜的な標本であり、世界の小説母集団を代表する層化確率標本ではない。名寄せ済みの著者表記を使う。欠けた著者ID・別名同定の不備まで分割器が解決することはできない。

ちょうど400作品を分割できないグループ構成では、件数やグループを勝手に変更せず失敗する。入力全体のハッシュ、シード、件数、分割内容のハッシュを固定する。既存manifestを異なる入力・シード・件数で上書きできない。変更する場合は新しい評価版・出力先を使う。開発で体系やプロンプトを調整し、校正で帯別校正器を作り、最終評価は凍結後の測定にのみ使用する。

## 注釈の入力契約と検証

人手データの例:

```json
{
  "work_id": "work:example",
  "provenance": "human",
  "reviewer_id": "reviewer-1",
  "truth_level": "evidence",
  "partition": "holdout",
  "labels": [
    {"label_id": "genre.mystery", "decision": "present", "relevance": 0.75,
     "evidence_ids": ["evidence:example"]}
  ]
}
```

例示データを実評価の正解として利用しない。外部参照は `provenance: curated_reference`、LLMによる比較データは `provenance: llm` とする。外部参照には作成方法、出典、対象範囲を別途記録する。外部参照はsilver benchmarkであり、人手正解ではない。

```powershell
uv run python -m recommend_book.evaluation import --annotations human-annotations.json --pack data/evaluation/v1 --output data/evaluation/v1/annotations-reviewer1.jsonl
```

インポートはwork ID、ラベル、根拠ID、値の範囲、分割、エクスポートに含まれるpackハッシュを検証する。未知の作品・ラベル・根拠、分割の変更、同じ担当者の重複はエラーになる。JSONLの不正行は行番号付きで報告する。入力スナップショットの著者・言語・地域を注釈へ取り込む。入力資料への忠実性の画面・根拠IDは `llm_input_allowed: true` の資料だけを使い、作品内容の画面ではローカルの作品資料を表示する。HTMLへの埋め込みはscript終端文字等をエスケープし、書誌・本文・モデル出力の表示に `textContent` を使う。

独立2名の注釈はそのまま混ぜて精度を出さず、不一致を裁定した単一正解にまとめる。評価APIは同じ作品・truth level・partitionの重複正解を拒否する。1名の場合は人数、独立性、時間を空けた再判定、対象範囲の制約を記録する。注釈には裁定者と元の判定を残した外部監査記録を併設する。

## 指標と解釈

```powershell
uv run python -m recommend_book.evaluation evaluate --results data/runs/poc/results.jsonl --gold data/evaluation/v1/adjudicated.jsonl --output data/evaluation/v1/report
```

人手・外部参照・LLMを別集計し、人手の `evidence / work` と `dev / calibration / holdout` を別々に表示する。作品・著者・シリーズのグループが人手データの複数分割に現れた場合は校正漏洩として拒否する。

| 指標 | 計算上の扱い |
|---|---|
| precision / recall / F1 | ラベル別、軸別、micro、macro。人手正例に対するモデルunknown・未出力はFN |
| 支持件数 | ラベルの人手正例数。20件未満は `insufficient_positive_support`。達成保証を付けない |
| 判定coverage | 登録作品のtaxonomy全ラベル枠に対する判定率と、人手が判断可能だった評価枠に対する率を分ける |
| 不適切な断定 | 人手unknownに対してモデルがpresent/absentを出した割合。通常の正誤指標から除外し、別集計 |
| 関連度 | 人手とモデル双方に数値がある判断可能項目のMAE、同順位を平均順位にしたSpearman相関。定数列・3件未満はnull |
| 信頼度 | present/absentの判定の正しさを目的変数とするBrier、10帯のECE、帯別実精度。unknownは対象外 |
| 高信頼採用 | 0.5、0.7、0.9、0.95の帯ごとの採用率、正例precision、採用された人手unknown数 |
| 偏り | 原言語、原作品初出年代、地域ごとの指標。地域は複数所属を許し、合計を母数として扱わない |
| 95%区間 | 割合のWilson区間と著者・シリーズ連結グループを単位にした300回のpercentile bootstrap。固定シード1729 |

Bootstrapはmicro指標、関連度MAE、ECE、Brierの区間を記録する。独立グループが2未満の場合は区間を推定しない。少標本・希少ラベル・便宜的標本の区間から母集団の品質保証はできない。Wilson区間は個別項目単位の記述的区間として、依存を考慮するbootstrapと併記する。

## 校正

校正は人手の `calibration` 分割だけから作る。truth levelごとに、ラベル・原言語・資料種別の同じ範囲で単純な2帯の実精度を学習する。範囲に40項目以上、使用する帯に20項目以上を必要とし、言語・資料種別不明の範囲には作らない。インポート時に実際に表示した資料の種類をソートして `+` で結合した `evidence_kind`（例: `subject+synopsis`）を保存する。直接APIへ正解を渡す場合は同じ規則の値を含める。資料構成を変えた場合は別範囲とする。校正帯にはmodel、prompt、taxonomy、CLI、target languageの実行条件を保持し、異なる実行条件の最終評価へ適用しない。

この小規模な帯別校正器は比較のための初期実装であり、全ラベル・全資料への校正を主張しない。最終評価では適用可能な帯だけを校正してECE/Brierを集計し、対象件数を併記する。未対応の範囲はnull/uncalibratedのまま残る。モデル、プロンプト、分類体系、入力資料が変わった場合は同じ校正器を流用せず、新版で評価する。

2026-10-12の[実行可能性監査](calibration-feasibility-v1.md)では、固定balanced400のcalibration100は同じ原言語・資料種類集合の最大が16作品で、すべてのラベルのscopeが必要な40件に届かないことを確認した。人手注釈を100作品分用意しても、この固定セットだけでは校正器を作れない。元400と品質条件を維持し、追加calibrationを別packとして固定する必要がある。未選択poolから数量上の追加下限は算出したが、実際のknown予測・人手truth・confidence帯・実行contextは未検証で、件数だけでは校正の成立を保証しない。

## 名寄せペアの評価

`evaluate_resolution_pairs(predictions, gold, candidates=None)` は `left_id / right_id / decision`（`same / different / uncertain`）のペアを受ける。人手・担当IDのある300〜500組を計画規模とし、same判定precision/recall/F1、precisionのWilson区間、候補探索の再現率、ペア種別の支持件数を報告する。人手のuncertainは正誤評価対象外、モデルのuncertainは人手sameの未検出になる。300〜500件に届かない場合は計画規模未達を明示する。

ペアだけではクラスタ全体の誤統合率を評価できないため、完全なクラスタ正解がない場合は未評価と記録する。原題・翻訳、異なる訳者、版違い、同名異作品、巻違い、短編と短編集、改作の難例を外部の人手ペアセットで用意する。無関係なペアだけで品質を判定しない。

## テスト記録

最初に `uv run pytest tests/test_evaluation.py -q` を実行し、評価APIが未実装でImportErrorになることを確認した。実装後は、保留正例のFN、人手unknownの断定、silverと人手の分離、作品内容正解の根拠宣言、固定グループ分割、XSS文字列、未知作品拒否、ECE/Brier、校正分割の隔離、空のF1、名寄せ候補漏れ、著者分割漏洩、不正JSONL行を検証している。これらは実装の検証であり、実データでの人手精度検証ではない。
