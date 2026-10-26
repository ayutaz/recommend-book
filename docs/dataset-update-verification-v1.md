# 小規模な更新経路の統合検証 v1

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

`scripts/verify_dataset_updates.py` は、2作品だけの独立した合成DBを作り、既存の
`DatasetDB` → `build_next_database` → `load_next_world_database` → `rank_candidates`
を通して更新結果を保存する。既存の2005作品DB、分類結果、凍結済み実装には書き込まない。

```powershell
uv run pytest -q tests/test_dataset_update_integration.py
uv run python scripts/verify_dataset_updates.py --output data/dataset-update-verification-v1-20261015
```

出力先は新規ディレクトリに限る。`verification.json` に各段階の候補ID、推薦ID、
入力ハッシュ、タイトル、保留件数を記録し、段階別のDB、入力、派生物、推薦JSONも残す。
モデル実行、ネットワーク通信、実データの走査は行わない。

| 操作 | 読み込み候補・推薦件数 | 確認内容 |
| --- | --- | --- |
| 初回取込 | 2 | 本物のUsageGuardが入力全体と書誌派生物を照合 |
| タイトル訂正 | 2 | 新版・入力ハッシュ・表示名の更新、旧スナップショット保持 |
| 同一入力の再投入・DB再オープン | 2 | ソース版は3件のまま、訂正タイトルの永続化 |
| 一括取込の途中で不正レコード | 2 | 例外によるトランザクション巻き戻し。版・head・取込イベントを保持 |
| 合成の誤統合 | 1 | 統合後の新規スナップショットで候補数も減少 |
| 統合取消 | 2 | 原本の版を削除せず、候補数も復元 |
| 現行台帳で利用許可を撤回 | 1 | 同一DB・同一UsageGuardを再利用し、撤回入力を推薦から除外 |

利用撤回には `UsageGuard` の `revoked_input_hashes` を使う。原本・履歴は削除せず、
現行目的での利用対象から除外する。訂正済み入力に `undo_ingest` を適用して旧版へ
戻す操作を、撤回と見なしてはいない。

すべて `synthetic_fixture=true`、`human_gold=false` として扱う。Q1/Q2の内容と
CC0確認ページも合成であり、実作品の取得記録・権利確認資料には使えない。
特徴量と参照評価は空で、全スコアは `unsupported` になる。確認対象は候補集合と
書誌表示の伝播であり、内容特徴の再分類、旧内容キャッシュ無効化、推薦品質を証明しない。
通常のclose/reopenと例外時rollbackを検証するが、プロセスクラッシュや途中停止後の
再開を検証したものではない。原本自体の物理的な撤回APIも追加していない。

旧スナップショットは履歴として保持される。訂正・統合・取消を利用対象に反映するには
新しい派生スナップショットを作り、利用撤回はその読取時に最新台帳で再判定する。
