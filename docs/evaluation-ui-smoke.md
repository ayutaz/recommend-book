# 実Sol 5件とローカルレビューUIの動作確認

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026年10月12日。固定入力は `data/smoke-full-schema/work-inputs.jsonl`、実Solの結果は `data/runs/full-schema-smoke-v1/results.jsonl`、DBは `data/smoke-full-schema/dataset.sqlite`。この確認は機械構造とレビュー操作の試験であり、人手分類品質の検証ではない。

## 実データの機械監査

`uv run python scripts/audit_dataset.py --database data/smoke-full-schema/dataset.sqlite --inputs data/smoke-full-schema/work-inputs.jsonl --results data/runs/full-schema-smoke-v1/results.jsonl --output data/evaluation/full-schema-smoke-audit` を実行した。

| 項目 | 確認結果 |
|---|---|
| 異なるresolved作品 | 5 |
| 入力行・実分類結果 | 各5 |
| Schema・意味的参照検証 | 5/5 |
| 何かのラベルを判断した作品 | 2/5。残りは根拠不足の保留 |
| Evidence | 15行 |
| Field assertions | 104行 |
| 必須出典・権利参照 | 100%。構造・台帳参照の検査としての値 |
| 原データSHA-256 | 5/5 |
| 原言語 | ar 1、en 2、ja 2。英語表示題から補完しない |
| 欠損 | 著者1/5、初出年2/5、地域2/5 |
| 人手正解 | 0。`human_validation_pending`、品質完了false |
| 400作品分割・20作品試行 | この5件だけでは件数不足 |

成果物は `data/evaluation/full-schema-smoke-audit/dataset-audit.json` と `dataset-audit.html`。PoC全体や世界の小説母集団の性能へ一般化しない。

## ブラウザで確認した経路

`data/evaluation/ui-smoke/review.html` を生成し、127.0.0.1のHTTPサーバ経由でChromeに表示した。サービスは `data/evaluation` のみを127.0.0.1へ公開し、試験終了時に停止した。外部サーバへ注釈を送信していない。

1. 5作品の選択肢、書誌・資料・72ラベルが表示された。モデル判定は初期状態で非表示だった。
2. 担当IDが空の保存で「担当IDを入力してください」と表示された。
3. ラベルフィルタに `genre.horror` を入力し、一つのラベル行へ絞られることを確認した。
4. 担当IDだけを入力して判定を空にした保存で「判定を1件以上入力してください」と表示された。
5. 操作試験は別の `data/evaluation/ui-smoke/synthetic-ui-test` パックで行った。画面に「合成UI試験専用：入力は人手正解へ利用できません」と表示する。
6. 担当ID `SYNTHETIC_UI_TEST_DO_NOT_USE_AS_GOLD`、一つの試験判定、関連度0.75、実在する根拠IDを入力した。これは人手による内容判断ではなく、UI入力の合成テストである。
7. JSON保存をクリックし、Chrome Downloadsに843バイトの `synthetic-ui-test-annotations.json` が保存された。download待機APIはタイムアウトしたが、実ファイルの時刻・内容・packハッシュで保存を確認した。
8. ダウンロードを別試験フォルダへコピーし、`--allow-synthetic-test` 付きの注釈インポートで1件の検証済み合成レコードを生成した。
9. 同じJSONの通常インポートは拒否された。`evaluate_results()` も `synthetic_ui_test` provenanceを正解として受け付けない。synthetic packのJSONをhumanへ書き換えても拒否するテストが成功している。

試験のJSONと `validated-test-annotations.jsonl` はsyntheticフォルダだけに保存し、人手goldに混ぜない。通常インポートの拒否後に `forbidden-human-gold.jsonl` が作成されていないことも確認した。

Chromeのアクセシビリティ状態・DOMで入力値と保存済みメッセージを確認し、取得できた画面を `data/evaluation/ui-smoke/synthetic-ui-test/review-proof.png` に保存した。全ページのスクリーンショットと、その後の追加ナビゲーション試験ではブラウザ接続がタイムアウトしたため、これらを確認済みとはしない。通常の画面表示、フィルタ、未入力検証、実ダウンロード、インポートの要求経路は上記の証拠で確認できた。

## 合成データを品質評価から排除する契約

`create_review_pack(..., annotation_mode='synthetic_ui_test')` だけが試験パックを生成する。エクスポートは `provenance: synthetic_ui_test`、通常パックと別のハッシュ・ローカル作業領域を持つ。`import_annotations(..., allow_synthetic=True)` は試験の構造・snapshotを検証するだけで、provenanceをhumanへ変更しない。

```powershell
uv run python -m recommend_book.evaluation import --annotations data/evaluation/ui-smoke/synthetic-ui-test/downloaded-annotations.json --pack data/evaluation/ui-smoke/synthetic-ui-test --output data/evaluation/ui-smoke/synthetic-ui-test/validated-test-annotations.jsonl --allow-synthetic-test
```

最初のsynthetic APIテストは未実装keywordで失敗することを確認し、実装後に従来評価・監査と合わせて20テストが成功した。UIの合成判定、人手が判定可能かどうか、作品の内容知識、独立2名の一致・裁定を混同しない。人手400作品、20作品の人手時間試行、作品知識50〜100作品、名寄せ300〜500ペアの作成は別途必要である。
