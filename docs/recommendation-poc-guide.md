# 世界の小説への個人推薦PoC

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

2026-10-13追記: 今後の推薦改善・比較・画面確認・納品は[自動完了計画 v2](autonomous-completion-plan-v2.md)に従い担当が行う。既存335件の評価を使い、新しい履歴や好みの入力をユーザーに要求しない。読書状態が未知の作品は未知のまま表示する。

2026-10-13。既存の①〜④に加え、⑤の嗜好分析・未読除外・推薦評価を実装する。人手による分類精度確認は引き続き未完了。推薦の実行成功を分類品質の承認や、将来の推薦性能の証明とは扱わない。

## 入出力

`recommend-book recommend` は分類済みの世界作品DBと、本人が保存したなろう評価・内容分類を読み取り専用で使う。個人記録・候補・結果はローカルの `data/` に置き、Git管理しない。通信、追加のLLM呼出し、利用者入力の自動収集は行わない。

```powershell
uv run --offline recommend-book recommend `
  --world-database data/poc-combined-classified-v2-v1/dataset.sqlite `
  --narou-database data/reference-code/narou-personal-recommender/data/narou.sqlite `
  --narou-annotations data/reference-code/narou-personal-recommender/data/processed/content-llm/annotations.json `
  --output data/recommendation-poc-next `
  --limit 100
```

出力先は新規ディレクトリを指定する。既存結果を上書きしない。原DB・注釈・対応設定と実装のSHAを前後で検査し、途中変更を検出した場合は公開用の成果物ディレクトリを作らない。

| 出力 | 内容 |
| --- | --- |
| `recommendations.html` | 著者・原言語・年代、推薦理由、作品情報リンク、読書状態による表示切替 |
| `recommendations.json` | 推薦順位・未校正の推定スコア・参照評価との共通特徴・出典 |
| `profile.json` | 既存評価の集計。対応できないなろう固有テーマも別項目で保持 |
| `read-history-audit.json` | 本人が指定した履歴の原記録、検証済みID対応、競合の処理。個人記録なのでGit管理しない |
| `source-domain-evaluation.json` | 共通特徴を使った、既存評価のグループ別5分割検証 |
| `native-theme-evaluation.json` | なろう固有14テーマだけを使う感度確認。元の文章＋テーマ方式の再現とは別 |
| `manifest.json` | 入力・実装・出力のSHA、データ件数、推薦可能率、未検証項目 |

## 好みと作品分類の接続

[対応設定](../config/recommendation-feature-bridge.json)は一部のジャンルと成長・中心的な復讐・恋愛だけを接続する。元の定義と異なるテーマは対応させない。「無双」と社会的な「権力」、育児と「家族」、あらすじの日常感と本文の「穏やかな雰囲気」は自動で同一視しない。主人公の性別にも現72ラベルの対応はない。

なろうのジャンルは提供元の申告値、テーマは既存モデルの分析値として出典を保持する。世界側の分類の人手正解へ転用しない。関連度は特徴の強度に使い、raw confidenceは校正済み確率や重みとして使わない。原文・翻訳に依存する文体は、版を指定しない作品推薦の既定特徴から除外する。

個人スコアは参照評価の平均に、類似する最大20作品の評価偏差をcosine²で重み付けして加える。低評価も使う。正の類似がない作品はスコアnullで、人気や未知ラベルから本人の評価を補完しない。設定と計算式は[エンジンガイド](recommendation-engine.md)に記載する。

## 既読・未読履歴

既存評価に同じ作品ID・外部IDがあれば候補から除外する。タイトルや作者名だけで別作品を既読にしない。世界の小説の履歴がない場合、残った作品の状態は「読書状態未確認」。なろうでの「読んだ作品は評価済み」という前提を世界の小説全体へ広げない。

履歴を追加する場合はローカルJSON配列を `--history` で指定する。例のIDは説明用であり、実際の本人履歴ではない。

```json
[
  {"work_id": "実際の作品ID", "read_status": "read"},
  {"external_ids": [{"source": "wikidata", "value": "実際の作品QID"}], "read_status": "unread"}
]
```

`read`または実評価のある記録は除外し、`unread`は明示確認として候補へ反映する。`unknown`は既読・未読へ推定しない。矛盾する別IDの履歴は保守的に扱う。`--require-confirmed-unread` は確認済み未読だけを返す。履歴がなければ0件になり得る。評価済みでも読了状態は別で、読了済みを推定しない。

## 評価と残る条件

[誤差分析](recommendation-error-analysis.md)は固定v2の性能差・大きな同点群・対応不足を記録する。後の実装では、なろう固有テーマも検証済みの元引用を `native_feature_provenance` として保持し、診断評価へ渡す。335実評価の別計算では、全方式・全foldの順位ID・スコア・評価指標がv2と完全一致し、native personalの根拠参照なし候補は335→12、計算可能だが参照なしの候補は323→0となった。これは根拠の保持を直した結果で、推薦精度を改善した証拠ではない。詳細はローカルの `data/evaluation/poc-openlibrary-enriched-v1-delta-v1/native-provenance-audit-v1.json` に保存した。

本人の既存評価を著者・シリーズID、保守的な作者名グループ、同一外部IDの連結成分で5分割する。参照側だけから好みを作り、評価側の本人評価を順位計算から隠す。個人方式・人気順・固定乱数順を同じ候補集合で比較する。未知評価を低評価に変換しない。

保存されていない著者・シリーズIDについて漏洩がないとは保証できない。現時点の作品情報を使う探索的な過去評価であり、当時の推薦や将来の未読作品での効果は再現していない。詳細は[評価仕様](recommendation-evaluation.md)、[実行記録](recommendation-poc-run-record.md)を参照する。

世界の小説側の本人評価が不足している間は、なろう上の結果だけで分野横断の推薦精度を認定しない。分類の人手20作品試行→400作品評価、名寄せ400ペア、必要範囲の信頼度校正を続ける。分類評価の固定基準は変更しない。

追加のOpen Library補充は[独立した新DB](openlibrary-enrichment-validation.md)へ保存した。[変更入力の準備](enrichment-classification-preparation.md)で全2,020作品を比較し、変更268作品・不変1,752作品・代表ID変更97作品を固定した。入力が変わった作品は新規分類が必要で、旧結果を新しい代表IDやinput hashへ付け替えない。既存のレビュー用入力も変更しない。

新分類を反映する場合、既存の `--world-database` を分類済み旧DBのまま指定し、追加で `--enriched-database`、`--delta-inputs`、`--delta-manifest`、`--delta-results`、`--delta-run-manifest`、`--delta-terminal-proof` の6引数をすべて渡す。一部だけの指定は拒否する。268件すべての終了成功と入力・根拠・SHAの照合後、新しい推薦出力先に `world-works.json` と `classification-overlay-audit.json` を保存する。補充DBへ分類テーブルを追加したという意味ではない。詳細は[分類差分の統合](recommendation-overlay-guide.md)を参照する。

統合時の既読履歴はSQLで検証した旧代表ID→新代表IDの対応だけを使う。旧IDの既読を新候補の除外へ反映し、元の本人記録は監査ファイルへ保持する。旧IDと新IDの別記録が同じ作品に一致する場合、既読や実評価があれば除外し、未読と不明が混在すれば不明を保つ。同一の元IDを重複登録した入力は引き続き拒否する。実評価を平均・合成したり、候補作品の本人評価として転記したりしない。
