# 新規入力用の固定runtimeと実行記録

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

この処理版は、新規作品の分類・検証・DB採用で実行ファイルの読み取りを共有する。過去のv3検証器や分類proofは変更しない。`prepare_native_snapshot_runtime_v1.py`が元ソースを隔離先へコピーし、置換箇所数を確認した決定的な変換でv4モジュールを生成する。生成元・生成後のbytesと入力・台帳・taxonomy・処理条件を実行前に固定する。

## 境界

- guardごと、スレッドごとの明示的な検証scopeを使い、プロセス全体の恒久cacheを作らない。
- scope開始時に実行ファイルのbytesとSHAを保持し、同じscopeのreceiptで共有する。終了時に再読取りして変更を拒否する。mtimeだけでは判定しない。
- Pythonソースの内容・パス・ファイル一覧も開始時と終了時に照合する。検証scopeがないv4 native検証を拒否する。
- runnerの結果採用、DBの公開前、表示用loaderの戻り前に検証を閉じる。失敗時に原応答が残ることはあるが、その応答を採用済み結果へ昇格しない。
- 原入力、完全な元batch、用途、実行context、実際のPID・終了コード、stdoutとfinalの対応を保持する。実行ファイルの検査共有でこれらの照合を省略しない。
- native結果は台帳全体のSHAへ結び付く。撤回用の別台帳へ変更すると、関係のない同じ旧台帳の分類も保留になる。この制約を回避して旧proofを付け替えない。

実行ファイルの読取りは**一つのscopeにつき2回**であり、全工程で2回という意味ではない。実際の初回10作品の分類は3scope・12receipt・6読取り・約2.00GBであった。最終採用は別scopeで20receipt・2読取り・約0.667GB。メモリに実行ファイルのbytesを保持するため、I/O削減と引き換えにメモリを使う。全工程の高速化率や最大メモリを、この数字だけから主張しない。

## 最新の実行版（2026-10-16）

`data/autonomous-next-v3/preparation/phase8-provenance-runtime-v3/`を使用した。仕様は`provenance-v4`、prompt `4.0-provenance`、専用`classification-provenance-v4.schema.json`。モデル・CLIは`gpt-6.1-sol`・`codex-cli 0.162.1`、分類はbatch5・2並列。最初10件は修正の回帰検証で特徴採用7件、条件成立後の残26件は16件採用、合計23/36件だった。13件の不確実は推薦特徴に使わない。実分類8呼出に審査3呼出を加え計11呼出である。

後続バッチの準備・検証は、実際に実行した固定runtime内の生成器を使用する。更新後の作業ツリーを過去の実行snapshotと同一と扱わず、固定コピーと変換後bytesを検証する。実行済みruntimeを後から修正しない。旧台帳がpinする本体`next_database.py`のbytesを保持し、新specの採用互換性はimport補助処理側で扱う。

結果と採用・保留の内訳は[分類改善と評価](phase8-quality-evaluation-v1.md)を参照する。以下は前回の実行記録であり、残26件の未実行状態は現在値ではない。

## 前回の実行版（phase7）

`data/autonomous-next-v3/preparation/phase7-world-native-snapshot-runtime-v2/`を使用した。中間の`runtime-v1`やfixture実行とは区別する。

- 分類仕様: `guarded-v3-baseline`、72ラベル・6軸。
- モデルとCLI: `gpt-6.1-sol`、`codex-cli 0.162.1`。APIキー・API endpoint環境変数は子プロセスから除去した。
- 実行: `native_snapshot_terminal_v1.py`が本物の分類プロセスを起動し、親が観測したPID・終了コードと出力SHAを保存する。
- 採用: `adopt_world_pilot_v1.py`が完全な終了証跡と実審査を要求し、新しい補助DBを作る。元DBや既存採用を上書きしない。
- importerは採用済みDBへの追記を拒否する。複数バッチを扱う場合は別々の補助DBとし、表示側で分類の所有元・審査の網羅・重複を確認する。

初回10作品は分類2呼出、10完了・失敗0・再試行0。実モデルの審査1呼出を終え、原分類10件・機械的な特徴採用7作品・分類内矛盾保留3作品・AI審査uncertain4作品を新しい補助DBへ保存した。uncertainと矛盾保留は一部重なる。推薦表示ではuncertain作品の分類特徴を使わないため、特徴採用は4作品となる。採用率40%で追加投入条件を満たさず、残り26作品は未実行とした。

## 試験と限界

`tests/test_native_snapshot_runtime_v1.py`はソース・実行ファイルの変更拒否、検証scope、二重採用拒否、fixtureプロセスによる新しい処理版の実行・cache再開・DB採用を扱う。fixtureの模擬分類と、上記の本物のCodex分類を区別する。実モデル途中の任意中断で重複呼出が起きないという保証はしない。表示前の中断・再開と撤回の実データ検証は、実行報告に別途記録する。

独立した人手正解・分類精度・confidence校正・本人向け推薦改善は未検証のままである。
