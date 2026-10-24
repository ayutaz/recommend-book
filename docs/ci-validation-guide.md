# CI 検証ガイド

## 作業ブランチの検証状況（2026-10-24）

現在の計画・分類件数・稼働状況は[2万分類への実行状況](scale-classification-v4.md)を参照する。以下の過去のCI成功を、拡張中の作業ブランチの成功と読み替えない。

失敗内容まで確認した[run 40060754593](https://github.com/ayutaz/recommend-book/actions/runs/40060754593)の対象は `1e5216fa88a2f917e32a64f42091e948256acc3a`。Ubuntu・WindowsのPython 3.14で、次の2試験がGit管理外ファイルの不在により失敗した。

| 試験 | CIにない依存ファイル |
|---|---|
| `test_collect_scale20k_content_v1.py::test_preflight_rejects_stale_rights_before_any_selection` | `native-source-registry-v1.json` |
| `test_scale20k_native_v2.py::test_unknown_genre_import_and_existing_control_constructor_connect_end_to_end` | `content-operational-driver-v2.py` |

この過去のrunはUbuntuが2 failed・2,629 passed・7 skipped、Windowsが2 failed・2,630 passed・6 skippedだった。その後、一時ディレクトリ内の合成fixtureへ切り替え、関連17試験のローカル成功を確認した。修正は`7fdc2d8efaccd75e87af9b1bf07d4ba80cd42c3a`としてpush済み。このcommitの[run 40080781445](https://github.com/ayutaz/recommend-book/actions/runs/40080781445)は両OSで成功した。Ubuntuは2,631 passed・7 skipped（68.69秒）、Windowsは2,632 passed・6 skipped（331.28秒）。これはfixture修正のCIであり、後続の最新実装のCI成功へ読み替えない。収集済みデータの公開、skipの追加、固定hash確認の緩和は行っていない。

今後の補助実装についても変更に関係する試験を確認し、確定した実装で両OSのCIを確認する。実データの分類・審査・DB採用、最終監査、実HTTP・画面確認はfixture CIと別に確認する。文書だけの更新では、リンク・差分・保存結果との整合を確認し、分類や全件pytestを追加実行しない。

最新実装は`ade2aad9654322b90f7065932973aa779f6cbbcb`。分類末尾処理、監査・利用量の接続、CLI更新後の証跡照合を含む。[run 40083544370](https://github.com/ayutaz/recommend-book/actions/runs/40083544370)を開始済みで、確認時点では実行待ち。ローカルでは分類・台帳・推薦・archive関連70試験、最終修正後のarchive・監査・利用量33試験、監査の4並列制御2試験がそれぞれ成功した。重複する試験があるため、これらを足した値を全件試験数としない。2万作品の実監査は別処理として進行中である。

## 過去のPython移行と検証（2026-10-20・phase16）

Python 3.14系へ設定を統一し、[PR #20](https://github.com/ayutaz/recommend-book/pull/20)でmainへ統合した。依存パッケージのバージョン変更は0。対象head`46e0532`の[run 39346947438](https://github.com/ayutaz/recommend-book/actions/runs/39346947438)はWindows・Ubuntuとも成功、ローカルは1,873 passed。phase16の後続差分はMarkdownのみで、ソース・依存・テストを変更していないため全件試験・CIを重複実行しない。

新規15作品、6用途、表示・撤回復元、実際の中断再開、全工程実測は[別の実データ報告](phase16-flow-measurement-and-delivery-v1.md)。これを独立精度や本人向け改善の証明にしない。以下は各時点の固定記録である。

## 過去の検証と統合（2026-10-20・phase15）

新しいv4 runtime生成時だけJobStoreを1試行に固定し、失敗後の同一入力の自動再送を防ぐ。通信失敗、schema失敗、同じ名前空間での再開を実際の生成schedulerと検査用fixtureで確認し、対象68件成功。過去runtimeと通常runnerの既定は変更していない。

コード`78d80c8`のWindows・Ubuntu CIを[run 39283406164](https://github.com/ayutaz/recommend-book/actions/runs/39283406164)で両OSの成功を確認した。後続の変更はMarkdownのみで、実装・試験の差分0を確認し、[PR #18](https://github.com/ayutaz/recommend-book/pull/18)でmainへ統合済み（`f1606ce`）。実データ2作品の分類・審査、6用途の読戻し、追加7特徴、画面と撤回・復元は[別報告](phase15-bounded-poc-and-delivery-v1.md)。CIを独立精度・校正・本人向け改善の証明としない。以下は各時点の固定記録である。

## 過去の実装・テスト版（2026-10-19・phase14）

`fdff689`の[CI run 39272636536](https://github.com/ayutaz/recommend-book/actions/runs/39272636536)は成功した。名寄せの限定的なapostrophe比較、既定方式を保持したラベル単位の明示採用と、ローカル資料に依存しない診断テストを含む。実装後の対象試験は名寄せ26件、分類・表示43件が成功。実データの2作品・2特徴の反映と撤回・復元は[別報告](phase14-identity-and-label-admission-v1.md)に記録した。

初回run `39033993443`は、診断テスト2件がGit対象外のローカル資料を参照して両OSで失敗。テストを合成一時SQLite・注入入力に修正し、skipは追加していない。旧headの重複run `39272128462`は取消済み。成功した最終runと区別する。CI成功を独立した分類精度・校正・本人向け改善の証明としない。以後はMarkdownのみの差分を確認して統合する。

## 過去の実装版での確認（2026-10-17・phase13）

最終コード`1b80688`の[CI run 39025631704](https://github.com/ayutaz/recommend-book/actions/runs/39025631704)は両OSで成功した。固定取得対象・完全入力を使う準備経路と、公式の国名根拠・未対応言語コードをunknownとして保持する投影を含む。Ubuntu **1,850 passed・7 skipped（43.05秒）**、Windows **1,851 passed・6 skipped（185.16秒）**。新しい4試験を追加し、skipは従来のscheduler2件・ローカルOPF4件、UbuntuだけWindows lock1件のまま。

実際のSol分類5作品、別呼出の内部審査、用途別DB読出し、推薦採用3作品、ブラウザーでの追加5作品と撤回・復元を[実データ報告](phase13-balanced-poc-and-delivery-v1.md)に分離した。内部審査の2不確実を除いた採用率は60%で、CI成功を独立精度・校正・本人向け改善としない。最終コードへworkflow dispatch一組を実行済み。以後はMarkdownのみの差分を確認し、全件試験を重複実行しない。

## 前回の実装版での確認（2026-10-17・phase12）

最終コード`30bf83e`の[CI run 39000544589](https://github.com/ayutaz/recommend-book/actions/runs/39000544589)は両OSで成功した。根拠付き合本ID訂正、厳密なSEリンク、別authority DBの小説適格性・重複検査、token上限の伝播を含む。Windowsは**1,847 passed・6 skipped（229.66秒）**、Ubuntuは**1,846 passed・7 skipped（44.41秒）**。skipは既存scheduler依存2件・ローカルOPF依存4件に加え、UbuntuだけWindows lock1件。新たなskipは追加していない。

5作品の実Sol分類・内部審査・DB採用・画面・撤回復元はローカルで別途確認した。[実データ報告](phase12-targeted-evidence-and-delivery-v1.md)を参照する。CI成功を分類精度や本人向け推薦改善の証明へ読み替えない。push/PRはskip指定とし、最終コードのworkflow dispatch一組だけを実行した。後続Markdown変更はコード差分0を確認して統合する。以下は過去の固定記録である。

`.github/workflows/tests.yml` は Windows と Ubuntu の GitHub hosted runner で、既存の全 pytest を実行する。Python 3.14 と uv 0.12.20 を指定し、2 job の最小構成にしている。ローカルの`.python-version`と`pyproject.toml`もPython 3.14に統一している。

## 過去の実装版の確認（2026-10-16）

[PR #13](https://github.com/ayutaz/recommend-book/pull/13)の最終実装head `fe45415`に対する[CI run 38854021948](https://github.com/ayutaz/recommend-book/actions/runs/38854021948)は両OSで成功した。現在件数での更新検査と、唯一の著者が挿絵担当を兼ねる形式を含む。

| OS | 結果 | pytestの時間 |
|---|---|---:|
| Ubuntu / Python 3.14 | 1,765 passed・7 skipped | 43.54秒 |
| Windows / Python 3.14 | 1,766 passed・6 skipped | 291.32秒 |

両OSのskipは旧取得済みscheduler依存2件、非配布のローカルOPF4件。UbuntuはWindows専用lock1件を追加する。新しい実OPF10件はローカルで確認したが、完全入力の名寄せ・用途条件が成立せず、新しいモデル・表示生成は実行していない。fixture CI成功を分類精度・推薦改善に置き換えない。push/PRの重複を避けるためcommitにskip指定を付け、最終headへ明示的なworkflow dispatchを1回実行した。先行head `547745e`のrun `38853387465`も成功したが、著者役割の追加修正後は上の最終版を証拠とする。後続はMarkdownのみでコード差分0を確認する。

## 前回の実装版（phase10）

[PR #12](https://github.com/ayutaz/recommend-book/pull/12)の実装head `d12228f`に対する[CI run 38829155501](https://github.com/ayutaz/recommend-book/actions/runs/38829155501)は両OSで成功した。編集紹介文の更新・追加表示・用途検査・撤回経路と、題名・著者役割の取込み形式を含む。

| OS | 結果 | pytestの時間 |
|---|---|---:|
| Ubuntu / Python 3.14 | 1,762 passed・7 skipped | 44.66秒 |
| Windows / Python 3.14 | 1,763 passed・6 skipped | 245.02秒 |

両OSのskipは旧取得済みscheduler依存2件、配布しないローカルOPF4件。UbuntuはWindows専用lock1件を追加する。実OPF・分類・審査・用途別読出し・画面と撤回はローカル実処理で確認し、fixture CIとは区別した。独立した分類精度・校正・推薦改善は未検証。旧head `f808641` のPR run `38828816359` と重複push run `38828808661`・`38829151538` は取消済みで成功扱いしない。以後の文書のみの変更は、テスト済みheadからコード差分0を確認して統合する。

## 前回の実装版（phase9）

[PR #11](https://github.com/ayutaz/recommend-book/pull/11)の実装head `8ce559c`に対する[CI run 38777725136](https://github.com/ayutaz/recommend-book/actions/runs/38777725136)は両OSで成功した。新しいStandard Ebooks取込み・用途検査のfixture試験を含む。

| OS | 結果 | pytestの時間 |
|---|---|---:|
| Ubuntu / Python 3.14 | 1,741 passed・4 skipped | 47.08秒 |
| Windows / Python 3.14 | 1,742 passed・3 skipped | 385.89秒 |

両OSのskipは旧取得済みscheduler依存2件とGitに配布していないローカルOPF1件、UbuntuはWindows専用lock1件を追加する。保存済み実OPF・原作品対応・分類・審査・補助DBの用途別読出しはローカル実処理で確認し、CIとは区別した。CI後の変更は結果を記録するMarkdownのみでコード差分0を確認し、重複した全件試験を省く。重複push run `38777710217` は取消済み。精度・校正・推薦改善の未検証は維持する。

## 前回の実装版（phase8）

[PR #9](https://github.com/ayutaz/recommend-book/pull/9)のhead `c7d4587`に対する[CI run 38766835239](https://github.com/ayutaz/recommend-book/actions/runs/38766835239)は両OSで成功した。mainへの統合commitは`6579c58`で、テスト済みheadとtreeが一致する。

| OS | 結果 | pytestの時間 |
|---|---|---:|
| Ubuntu / Python 3.14 | 1,717 passed・3 skipped | 38.13秒 |
| Windows / Python 3.14 | 1,718 passed・2 skipped | 586.60秒 |

両OSのskip2件は`test_autonomous_native_reference_runner.py`の旧取得済みschedulerがCIにないためで、現在の拒否経路の試験は実行する。Ubuntuの追加1件はWindows byte-range lock専用試験。実装版CIの成功は実作品の分類精度・confidence校正・本人向け推薦改善を証明しない。PRと重複したpush側、および統合後の同内容のmain側CIは意図的に停止し、成功したPR側の結果と区別して記録した。

## 文書だけを更新する場合

変更差分がMarkdownだけであること、参照先、現在値と保存済み結果の一致、過去の記録と今後の作業の区別を確認する。分類、DB生成、全件pytestは繰り返さない。コード・schema・設定・依存・workflowを変更した場合は該当する試験と最終版CIを実行する。

Markdownのみのcommitは`[skip ci]`を使い、push/PRによる全件試験の重複を避けられる（[GitHubの公式手順](https://docs.github.com/en/actions/how-tos/manage-workflow-runs/skip-workflow-runs)）。必須チェックによって統合が止まる場合は、それを回避せず必要なCIを実行する。文書確認の成功と、過去の実装版CIの成功を別に報告する。

## 実行対象と再現手順

push、pull request、手動の workflow dispatch が対象。各 OS で次の順に実行し、失敗したコマンドは job を失敗させる。テストの除外オプションや `continue-on-error` は使用しない。

```console
uv lock --check
uv sync --locked --dev
uv run --locked pytest tests -ra
```

`uv lock --check` はプロジェクトの宣言と lockfile の整合性を確認する。`--locked` は不整合時に lockfile を更新せず失敗させる。`--dev` により既存の pytest dependency group を含める。[uv の lock / sync 仕様](https://docs.astral.sh/uv/concepts/projects/sync/)

Python / uv の導入には公式 `astral-sh/setup-uv` を使い、action を完全な commit SHA で固定した。`python-version` の指定方法も公式資料に従う。checkout も公式例の SHA を使用し、両 action の `action.yml` が取得できることを確認した。[uv の GitHub Actions ガイド](https://docs.astral.sh/uv/guides/integration/github/)、[setup-uv の固定 revision](https://github.com/astral-sh/setup-uv/blob/c771a70e6277c0a99b617c7a806ffedaca235ff9/action.yml)、[checkout の固定 revision](https://github.com/actions/checkout/blob/3d3c42e5aac5ba805825da76410c181273ba90b1/action.yml)

権限は `contents: read`、checkout の credentials 永続化は無効。秘密情報を workflow に渡さず、Codex CLI を導入しない。matrix の片側が失敗しても、他方の検証は継続する。Windows と Ubuntu で文字コードによる差が出ないよう `PYTHONUTF8=1` を設定する。[GitHub Actions の workflow 構文](https://docs.github.com/en/actions/reference/workflows-and-actions/workflow-syntax)

## fixture と実通信の境界

2026-10-15追記: 分類の共通fixtureは、使用するexecution specだけを台帳へ登録する。通常のworld分類テストからnative研究資料の取得済みキャッシュを参照しない。native providerのテストではコード・schema・設定を`tmp_path`の小さなプロジェクトへコピーし、明示した合成コードブック3件を置く。実際のguard・原物hash・実行recipe照合は残し、資料改変後に旧recipeで実行できないことも検証する。Git対象外の本文・PDF・私有データをCI用に追加したり、検証をskipして成功扱いにしたりしない。

固定したPythonコードの原bytes SHAをWindows checkoutでも保つため、`.gitattributes`で`*.py text eol=lf`を指定する。改行変換でhash不一致になった場合に、検証側で任意のbytesを許可したり、過去の固定SHAを置換したりしない。

2026-10-12 の既存テストを静的に確認した結果は次のとおり。

| 対象 | テストで使うもの |
| --- | --- |
| HTTP adapter、ダウンロード、429 / Retry-After | `httpx.MockTransport` と模擬 response / clock |
| NDL / Wikidata collector | 偽 adapter と `tmp_path` 内の入力・出力 |
| Wikipedia enrichment | `HTTPFetcher` の偽物を monkeypatch |
| Wikisource collector resume | 通信したら失敗する `NoNetwork` adapter と保存済み fixture |
| Codex scheduler | `subprocess.run` の monkeypatch、固定イベントと分類結果 |
| 分類 CLI | 偽 runner、入力 0 件、引数検証で停止する経路 |
| 実 subprocess | Python による local CLI の help / ingest / stats と、分割結果の hash seed 再現性検証 |
| 評価 / production 準備 | 合成入力と `tmp_path` 内の評価 snapshot。実人手 gold は作らない |

CI はライブ収集、実 LLM 分類、外部データ snapshot の更新を実行しない。テスト自体の HTTP / Codex 境界は上記 fixture に依存する。runner のネットワークを firewall で遮断する構成ではないので、新規テストでも実 client / executable を fixture に置き換えること。Python、uv、action、依存パッケージの取得にはネットワークが必要であり、`uv` の offline オプションを付けるだけでは pytest の通信禁止にはならない。

`tests/test_jobs.py::test_claim_waits_when_empty_lock_file_is_already_locked` はWindows byte-range-lock専用で、Ubuntuは理由付きskip、Windowsでは実行する。現行のskip内訳は冒頭の実測表を参照する。新たな実通信・実Codex依存やplatform障害が見つかった場合は、テストを除外して成功扱いにせず、失敗と原因を報告する。

## 作成時点の構文検証と範囲（2026-10-12）

workflow の YAML、expression、matrix、action input は [actionlint](https://github.com/rhysd/actionlint) で確認する。ローカルに Go がある場合の固定版コマンドは次のとおり。これは開発用ツールとして Go の cache を使い、プロジェクトの Python 依存には追加しない。

```console
go run github.com/rhysd/actionlint/cmd/actionlint@v1.7.12 .github/workflows/tests.yml
```

2026-10-12 のローカル確認結果は次のとおり。

| 確認 | 結果 |
| --- | --- |
| `uv lock --check` | exit 0、24 packages の整合性確認 |
| `uv sync --locked --dev` | exit 0、24 packages の同期確認 |
| `uv run --locked pytest tests -ra` | Windows / Python 3.14.0、269 passed、9.56 秒、skip なし |
| actionlint v1.7.12 | exit 0、diagnostic なし |
| Ubuntu / GitHub hosted runner | 未実行 |

この節は初回導入時点の履歴で、当時はローカルのworkflow・本文の追加と検証までだった。現在のGitHub hosted runnerでの実成功は冒頭に記載した。ローカル結果を別OSや別実装版の成功へ読み替えない。
