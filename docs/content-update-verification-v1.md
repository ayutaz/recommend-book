# 内容更新・中断再開・利用撤回の結合検証 v1

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、次の作業順序は[現行計画 v25](autonomous-next-phase-plan-v25.md)を参照し、入力・権利・版の前提は本文で確認してください。本文のCLI例・数値・測定値は記載した版・namespaceの再現用です。

2026-10-15。`feat/update-validation-and-evaluation-handoff` 上で、既存の本番処理を変更せず、小さな合成データで実行した。既存のメタデータ更新検証を補完する。

実装は `src/recommend_book/content_update_verification.py`、実行入口は `scripts/verify_content_updates.py`、回帰テストは `tests/test_content_update_resume_integration.py`。

## 実行と保存先

```powershell
uv run pytest tests/test_content_update_resume_integration.py -q
uv run python scripts/verify_content_updates.py --output data/content-update-verification-v1-20261015-04
```

後者の保存先は実行済み。再実行時は未作成の別ディレクトリを指定する。既存ディレクトリへの上書きは拒否する。実行結果は `data/content-update-verification-v1-20261015-04/verification.json`。`data/` は Git 管理対象外で、端末内の検証成果物として保持する。

外部リクエスト、実モデル、API、実コーパス、公開、課金操作はない。Wikidata 形式の JSON、CC0 条件文、記述文、分類結果はすべて合成フィクスチャ。モデル名欄は本番スキーマに合わせた値だが、実際の Sol 実行ではない。CLI の応答だけを子プロセス内で置き換える。UsageGuard、入力・出力検証、runner、JobStore、ネイティブ試行記録・検証、DB importer、特徴抽出、表示用 adapter、ランキング関数は実装をそのまま実行する。権限判定や凍結照合の置換・無効化は行っていない。

## 確認できたこと

| 経路 | 入力・操作 | 実測結果 |
| --- | --- | --- |
| 内容証拠の訂正 | 同じ作品・同じタイトルで、原典形式 JSON の `descriptions.en` と対応する `Evidence(kind='description')` を更新 | 完全入力ハッシュと job key が変化。旧結果の新入力に対する検証、旧ハッシュのキャッシュ利用、旧 run 名前空間への新 ledger の投入をすべて拒否 |
| 特徴の更新 | 旧分類は mystery present、更新後は mystery absent という固定応答 | 旧特徴は `{'genre.mystery': 1.0}`。更新原典から作り直した未分類 DB は `{}`。更新入力の新しい分類を本番 importer で採用後も `{}`。旧特徴を引き継がない |
| 処理の中断と再開 | 1作品ずつ固定した2シャード。最初の runner 終了・独立 DB 採用後、次のシャード開始前に実 coordinator 子プロセスを terminate | 終了コード1を親が観測。最初のシャードは全成果物の SHA256 が一致したまま再利用。再開では残る1シャードだけ実行・採用。各 job の attempts は1。結果と候補の作品IDは2件、重複0 |
| 未採用の完了試行から再開 | 最初のシャードを採用後、2番目の worker が終了コード0で終了し端末証拠を書いた時点、DB 採用前に coordinator を terminate | 最初の採用は不変。2番目の `attempt-0001` の完了結果・元証拠も不変で保持。再開時は `attempt-0002` の新しい run 名前空間で再計算し、その元 run ID で採用。採用された作品は計2件、重複0。合成 CLI 応答回数は未採用試行を含め計3回 |
| 利用許可の撤回 | 同じ採用 DB と同じ UsageGuard インスタンスのまま、現在 ledger の対象入力を revoked に変更 | 表示とランキングは2件から0件。撤回作品が残らない。原典、DB、元分類結果は不変。キャッシュ利用は実行ファイル探索より前に拒否 |
| HTML 表示への反映 | 既存 `recommendation.pipeline.render_recommendations(result, build_profile([]))` をそのまま実行 | `before-withdrawal/recommendations.html` は作品カード2件、`after-withdrawal/recommendations.html` と不許可後の `resume/recommendations.html` は作品カード0件。撤回対象タイトルも存在しない。JSON とともに再生成 |
| 再利用の再認可 | 採用済み固定シャードの ledger を撤回後、coordinator を再実行 | 現在許可を読み直して再利用を拒否。`resume/display.json` と `resume/recommendations.json` も空の現在結果に更新し、以前の2件を表示し続けない |
| 固定入力と重複 | 準備済み入力ファイルに改変、同じ採用 DB を二重にロード | 固定入力 SHA 不一致、採用作品ID重複をそれぞれ拒否 |

旧 run、採用 DB、凍結コード文脈は保護対象として実行前後の SHA256 を比較し、変化なし。SQLite 外部キー検査も対象の採用 DB で通過した。

`descriptions.en` はこの検証における内容証拠のバイト更新であり、Wikidata の識別用 description を一般のあらすじに読み替えるものではない。分類応答は決定的な合成値なので、記述から mystery を判断できたという意味理解の実証にもならない。

## 中断再開を成立させた範囲

`run_fixed_shards` は合成フィクスチャ専用の実行調整処理。各シャードの入力、原典スナップショット、実行設定、元 run ID、端末終了証拠、採用 DB を独立して保持する。入力は実行前に凍結し、再利用時にも SHA と現在許可を確認する。採用後のシャードは再実行せず、本番 adapter で読み直す。候補をまとめる際は作品ID重複を拒否する。

未採用シャードの実行は毎回 `shard-N/attempts/attempt-NNNN/run` という新しい名前空間で行う。以前の未採用試行に完了 job、キャッシュ、端末証拠が存在しても、その run ディレクトリを再開しない。孤立した元試行はそのまま保持し、採用できる新しい独立 run を作る。未採用の試行は再計算され得るため、任意の中断をまたぐモデル呼び出し回数の exactly-once は保証しない。採用済み作品を再度採用しないことと、未採用内容の計算を繰り返さないことは別である。

runner を実行する子プロセスの終了コード0を coordinator が観測してから、その子の元の run ID と成果物 SHA で端末証拠を作り、本番 importer に渡す。今回の実際の強制終了は、採用後の境界と、次の worker の終了を観測した後・採用前の境界の2通り。どちらも生存中の worker 孫プロセスはない。再開後も元の証拠を新しい run ID や ledger に付け替えない。

`resume-before-adoption/before-kill-sha256.json` に採用済みシャードと未採用試行の強制終了前ハッシュ一覧を保存する。`verification.json` の `unadopted_attempt_recovery` に中断前 worker 終了コード、coordinator PID、再開時の実行回数、元と再試行の run ID、実際の採用 run ID、旧成果物不変の比較結果を保存する。

既存の単一 run ディレクトリを再開すると runner は新しい run ID を発行する。一方、v1 importer は各元試行の request.run_id と採用対象 manifest.run_id の一致を求める。このため、旧 run で完了した job と再開 run で完了した job を混ぜた DB 採用は拒否される。今回の固定シャード方式は独立して完了・採用した単位を再利用することでこの制約を避ける。単一 run の混在採用の修正を証明したものではない。

## 利用撤回で残る制約

ネイティブ分類証拠は、実行時 ledger 全体の SHA に結び付く。現在 ledger の撤回リストを1件変更すると、撤回対象以外のネイティブ分類も ledger 不一致で保留になる。今回の2件から0件への減少はこの保守的な挙動を含む。対象だけを除外して他の過去分類を維持する差分再認可は未実装。

保存済みの原典、分類、過去の表示ファイルは履歴。現在表示には現在許可で adapter を再実行した成果物を使う必要がある。固定シャード coordinator は再実行で不許可を検知した場合、現在の表示・ランキングファイルを空にして再利用を拒否する。既存 Web UI や常駐配信の自動更新までは検証していない。

HTML は独自の代替レンダラーではなく、既存の本番レンダラーを使用する。実装パスと SHA256 を `verification.json` の `display_renderer` に記録する。この検証の HTML は空の評価プロフィールを渡しており、本人の推薦品質を示すものではない。ブラウザでの目視確認はこの自動 receipt に含めない。

## 未証明

- 実モデルの判断品質、実書籍の分類精度、人手 gold、推薦の有用性。
- 実作品や実際のライセンス確認を通じた全本番経路。
- 実モデル呼び出し途中、JobStore の書き込み途中、SQLite トランザクション途中での強制終了、および稼働中の孫プロセスを含むプロセスツリーの回復。
- 期限切れ実行 lease の回収、並行する複数 coordinator の排他制御。
- 単一 run の異なる run ID を混ぜた採用、および複数シャードを単一 DB へ再統合する処理。

固定シャード再開が通ったことを、これらの確認済み扱いにはしない。
