# PoC品質判定の固定条件 v1

2026-10-12、100作品の2・4・8並列試行が完了した時点で、計画書の暫定目標をこの実験の評価条件として固定する。未達の目標を結果に合わせて変更しない。未使用holdoutを確保する新しい評価版を作る場合は、旧版と比較可能な履歴を保持する。

## 実験と標本の対応

- taxonomy: version1.0、6軸72ラベル、主要28ラベル。原JSON SHA `688f8e1a0921bcbafabbbf54d99b9039bce8c525ea7e7147bdb01821b449084e`。
- 入力: `poc-frozen-v2` の2,000作品。原JSONL SHA `77793ab2c89983cb6174d58ce35d4d353934ac4cb73bd073aafdcae4a3c337fe`。
- model: `gpt-6.1-sol`、CLI0.162.1、medium reasoning、batch5。prompt SHA `318068a80595b9640c1de4c5921583e2cc70247e7fd169ed88f4a57ad6bc919a`、schema SHA `3d1acce735e97ab5fa2d9c8f6b00c3d354db278ed8752b7003b71e7f76f94217`。
- 人手標本: [balanced v1](evaluation-balanced-v1.md) のdev150/calibration100/holdout150。split SHA `7b4ebe9b0a1599caf155ca13dbcbade13c34269a316f4f768d09a6ed00f4ae6c`。
- 本番の1,000→2,000順序は人手のpartitionを変更しない。100作品の運用pilotと人手holdoutを同じ標本として集計しない。
- 原文古典20は独立した入力・分類・DB対応の実験であり、短い抜粋の機械成立を本体2,000の文体品質へ拡張しない。

taxonomyファイルの歴史的status値 `provisional_until_pilot_freeze` は実行中のhashを保つため変更しない。この実験で使用する意味・境界条件は上記SHAで固定する。後のtaxonomy変更は別の入力/実行/評価版とする。

## 判定表

| 対象 | 固定した目標 | 判定に必要な証拠 |
|---|---|---|
| 構造 | 再試行後の有効JSON99%以上 | 全選択作品を分母にしたvalid結果、最終保留・未投入を含むmanifest、実入力/Schema照合 |
| 根拠の存在 | 採用ラベルの根拠ID100%存在 | 許可根拠ID・scope・locator・原物SHA・引用所属の機械照合 |
| 根拠の正しさ | 架空/誤参照1%以下を目標、区間付き | 独立した人手の箇所/意味的支持判定。文字列の存在だけでは達成としない |
| 主要ラベル総合 | micro F1≥0.80、macro F1≥0.65 | holdoutの人手が判定可能な正例/負例、全主要ラベルの支持件数と除外理由、グループbootstrapの95%区間 |
| 主要ラベル別 | precision≥0.85、recall≥0.65 | 正例20未満は確証不足とする。unknown/未出力による正例未検出をrecallから落とさない |
| 高信頼採用 | 実precision≥0.95 | 校正済み範囲でのholdout実精度、95%区間、採用率。raw confidence閾値だけでは達成としない |
| 校正 | 十分な標本範囲でECE≤0.10 | 人手calibrationだけで学習しholdoutへ適用。Brier/校正曲線、model/prompt/taxonomy/language/evidenceの適用範囲を保持 |
| 判定coverage | 根拠が利用可能な作品のgenre/theme80%以上 | 人手が判定可能な範囲を示した分母。全登録作品の率も別掲し、資料不足へ断定を強制しない |
| 文体/構造 | 作品内容の正解と十分な入力がある範囲で評価 | 原文/明示翻訳scope、knowledgeable readerまたは十分な資料、作品truthと入力truthの分離 |
| 関連度 | MAE・順位相関・不一致事例を報告 | 人手の中心性段階評価。信頼度の確かさと混同しない。数値目標を後付けしない |
| 復旧 | 再開・再投入後も二重反映/不整合0 | lease/job key、immutable入力、再利用結果の再検証、DB整合性試験 |

二名の独立判定と不一致裁定を基本とする。単独判定・時間を空けた再判定は、その制約を明示する。作品truthの詳しい評価は50〜100作品、名寄せ難例の人手評価は300〜500ペアを初期範囲とする。分類と名寄せの正解を自動で作成しない。

校正の最小条件は、同じlabel・原言語・資料種類・実行条件の範囲に40項目以上、使用するconfidence帯に20項目以上。校正範囲外は `calibrated confidence=null` のままにする。希少言語・希少ラベルの評価不足を他言語の精度で埋めない。

## この時点の判定

運用pilotは300/300有効、参照と引用所属の機械検証に合格。並列8を選択した。2並列結果は何らかの判断52/100、全軸unknown48であり、全登録作品に対する実判定coverageを報告する。これだけで人手が判定可能な分母を決定しない。

人手goldは0、分類precision/recall/F1・関連度精度・根拠支持・作品内容truth・ECE/Brier・高信頼採用精度・名寄せ精度は未評価。品質gateは未達/未検証であり、PoC全体は完了していない。

レビューは [日本語ラベルガイド](human-taxonomy-v1-guide.md)、[評価手順](evaluation-guide.md)、[名寄せ手順](resolution-review-guide.md)に従う。実分類の構造有効率と、人手で検証した意味的精度を別の結果として保持する。
