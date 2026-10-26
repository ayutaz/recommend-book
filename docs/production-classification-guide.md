# 固定2,000作品の段階分類

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

2026-10-12。既存の `poc-frozen-v2` を変更せず、最初の1,000作品で日本語350・英語350・その他300を処理し、その後同じジョブ保存先で2,000作品へ拡張する。これは実行順序であり、人手評価のpartitionや標本を変更しない。

## 固定入力

```powershell
uv run python scripts/prepare_production_inputs.py --frozen data/poc-frozen-v2 --review-pack data/evaluation/poc-frozen-v2/400-review-balanced-v1/review_pack.json --output data/production-inputs/poc-frozen-v2-v1 --stage-size 1000
```

実行済み。2,000作品の原JSON行を並べ替え、各行の内容をそのまま保持した。元入力・pilot・review pack・実行コード/設定のSHAを照合し、出力済みディレクトリへの上書きを拒否する。

- 元2,000入力SHA: `77793ab2c89983cb6174d58ce35d4d353934ac4cb73bd073aafdcae4a3c337fe`
- 並べ替え入力SHA: `69179956b2ce41dbf89c7d8bb8ecb187d3760f66935ee20e8e31e7ee9e375305`
- 最初の1,000にreview400とpilot100の和集合479作品を全件収容。
- 14の原言語/不明bucketを含む。残りはseed1729の決定的順序で選択。
- rare languageは先行するため、1,000作品を母集団の無作為標本として扱わない。品質評価は別の固定400作品を使用する。
- 作品追加、根拠更新、ラベル変更、判定済み正解の生成は行わない。

配分不能、重複work、expression行、未知の優先ID、改変されたreview入力、元入力SHA不一致、実行contextの変更は拒否する。構成できない配分を縮小して成功扱いにしない。5テストで原JSON行保存・元資料不変・再現性・改変拒否等を検証した。

## 本番実行の条件

100作品の2→4→8試行は新規runで比較し、同じ入力・model・prompt・taxonomy・schema・CLI条件が保持された完了レポートから並列数を選ぶ。時刻制限・token制限・rate limit・利用上限・認証エラーによる停止がある場合、残りの試行や本番を自動投入しない。性能上の選択は意味的品質の承認ではない。

完了したbenchmarkは2/4/8すべて100有効結果で、時間は763.832/454.693/371.687秒、p95は97.264/97.911/117.886秒。4→8の処理量増加22.33%、p95悪化20.40%が暫定gateを通過し、選択並列数は8になった。8並列では1バッチのinvalid_resultを既存runner内で再試行し、最終失敗は0。意味的品質の承認ではない。

以下の本番コマンドを実際に起動した。実行handleは52066、資源監視handleは28126。timeout900秒、batch5、最初の1,000は最大7,200秒・観測8,000,000 tokenとし、実行中の呼び出しは終了を待つ。上限は超えることがあり、観測tokenはAPI課金額ではない。

```powershell
uv run recommend-book classify --input data/production-inputs/poc-frozen-v2-v1/ordered-inputs.jsonl --output data/runs/poc-frozen-v2-production-v1 --model gpt-6.1-sol --concurrency 8 --batch-size 5 --limit 1000 --timeout 900 --max-run-seconds 7200 --max-observed-tokens 8000000 --stop-on-rate-limit
```

実プロセスの終了とmanifestを確認してから次段階へ進む。CLIの終了コードだけで完了とせず、`selected/completed/failed/pending/stopped/stop_reasons` と個別結果を照合する。構造検証に合格した結果と、最終保留の具体的な理由を別々に保存する。未投入・実行中・資料不足・実行失敗を同じunknown分類で埋めない。

1,000作品の完了結果は同じWorkInput・実行設定のjob keyで再利用し、2,000作品の実行では `--limit 1000` を外す。既存結果も毎回再検証する。各実行の `manifest.<run_id>.json` は残る。rate/usage/authenticationによる停止直後に自動再開しない。観測pollのtimeoutは分類プロセスの終了ではなく、同じhandleの確認を続ける。

## 観測と品質

本番の生存プロセスと子CLIのCPU・メモリは、`observe_classification_resources.ps1 -CommandMatch` を本番コマンドに合わせて別ファイルへ記録できる。測定間隔内に終了した子は見落とすため、sample最大値を厳密なpeakと表示しない。

完了結果は `summarize_classification_run.py` と固定DBのread-only監査でschema・ID・利用可能な根拠・raw SHA・引用所属・判定coverageを再照合する。人手gold、summaryの忠実さ、ラベルの意味的支持、作品全体の正しさ、信頼度校正は別途評価する。人手goldがない間はprecision/F1/ECE等をnullとし、raw confidenceを校正済み確率として使わない。

実行中、関連度の契約に不整合を確認した。promptとSchemaは中心性不明の`relevance=null`を許容するが、固定validatorはknown判定で数値を必須にしている。完了済みの不正attempt6件・null関連度12項目を`data/evaluation/poc-frozen-v2/relevance-contract-observation-v1/observation.json`へSHA付きで保存した。同じ作品/ラベルの再試行を含むため、12を異なる作品数として扱わない。実行中の入力・検証コード・設定は変更していない。次段階の前に [契約レビュー](relevance-contract-review.md) の次版案を実施し、旧履歴を残して再検証する。支持されていないラベルや関連度の数値を補って成功にしない。

人手レビューには [分布調整した400/20作品の設計](evaluation-balanced-v1.md) と [評価手順](evaluation-guide.md) を使用する。元のblind packはモデル結果の追加で上書きせず、注釈・partition・出典SHAへの対応を保持する。
