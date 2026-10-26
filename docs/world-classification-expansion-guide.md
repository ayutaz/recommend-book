# 世界小説の差分分類・取り込み

確認日: 2026-10-26。実装 `5dd5ffc` の現行バッチ用ガイドです。計画は[v25](autonomous-next-phase-plan-v25.md)、件数と実行状態は[現在の到達点](current-status.md)を参照してください。旧scale20k/scale100kの実行器を、そのまま新しい目標へ再起動する手順ではありません。

## 前提

Python 3.14とuv、既存Codex CLIの認証、生成済みの共有DB・固定runtime・用途審査済みsource windowを使います。DBや原資料はGitに含まれないため、cloneだけでは実行できません。

今回の作業領域は `data/world-21000-delivery-20261026-v1/`。共有DBは `data/autonomous-next-v3/scale-v1/catalogue.sqlite`、固定runtimeは `data/autonomous-next-v3/preparation/scale30k-content-runtime-v1/` です。既存の固定runtimeや過去の成果物を上書きしません。

資料のジャンルが不明でも、表示許可と物語内容の根拠が揃えば分類対象にできます。`works.recommendable` は資料ジャンルによるフラグなので、新しい内容分類を採用するために書き換えません。作品ID、入力hash、原資料、関連度、未校正の信頼度、不明値を保持します。

## 実行の順序

1. 既存分類済みQID・予約済みQID・過去と同じ入力hashを除外し、物語資料の取得・保存・外部LLM入力・表示を審査します。準備は `prepare_source_window()`、照合は `load_sources()` を使います。
2. 新しいsource indexを別ファイルとして固定します。`world-novel-expansion-source-index-v1`、除外台帳のpath/SHA、0から連番のwindow、各manifestとwork mappingのSHAを記録します。既に使用したindexは変更しません。
3. `run_world_novel_expansion_v1.py check` で実入力・用途許可・mapping・重複を照合します。このモードはモデルを呼びません。
4. 未着手のwindowだけ `run` します。最初は2または4並列。後続には直前windowの実DB取込後のv4 controlとSHAが必要です。8並列には、同じ区間の実測で正常終了した4/8並列の記録も必要です。
5. native-v1 exportを `publish_world_novel_expansion_v1.py` へ渡します。出力審査でv3を作成し、単一writer lockの下で実DBへ取り込みます。前後の書誌・新規ID・採用ラベルを照合し、v4 controlを保存します。
6. 新規作品の検索・詳細・特徴を使った推薦を実HTTPで確認します。資料の準備数、分類数、DB採用数、表示特徴数を分けて報告します。

分類はSolの1作品単位で、別呼び出しによる全件審査を行います。今回の固定設定は `gpt-6.1-sol`、CLI 0.164.0、最大8並列です。窓ごとの失敗率5%以下・表示特徴採用率70%以上・内容由来の非ジャンル特徴があることを確認します。これは独立した正解集合での精度保証ではありません。

## コマンドの入口

次は保存済みindexの確認例です。indexの内容を承認する前に、担当Codexが原資料と用途審査の証跡を確認します。

```powershell
$sourceIndex = 'data/world-21000-delivery-20261026-v1/source-index-v4.json'
$sourceIndexSha256 = (Get-FileHash -Algorithm SHA256 -LiteralPath $sourceIndex).Hash.ToLowerInvariant()
uv run --locked python scripts/run_world_novel_expansion_v1.py check --source-index $sourceIndex --source-index-sha256 $sourceIndexSha256
```

| コマンド | 必須引数と条件 |
|---|---|
| `run_world_novel_expansion_v1.py run` | `--source-index`、`--source-index-sha256`、`--window-index`。後続窓は `--prior-control`、`--prior-control-sha256` を指定。`--concurrency` は2/4/8 |
| `publish_world_novel_expansion_v1.py` | `--export` は成功したnative-v1 manifest、`--sha256` はそのSHA、`--output-v3` は作業領域内の新規ディレクトリ |

開始するとcreate-onlyの試行予約を保存します。失敗時は予約や原出力を削除して同じコマンドを再送しません。生きているプロセスは同じhandleを確認し、モデル開始前の環境エラーと実モデルの失敗を区別して引き継ぎます。取り込み済みQIDの置き換えも拒否します。

`execution/`、`classification-exports/`、`public-exports/`、`catalogue-imports/`、`window-controls/` に実行・審査・取込の証跡を保存します。最初の20作品はこの経路で採用・実HTTP確認済みです。whole-clean producer補助コードは後続の`d7bc5e0`でcommit・push済みです。その2入力はまだ分類・DB採用していません。
