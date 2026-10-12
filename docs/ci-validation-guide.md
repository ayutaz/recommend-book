# CI 検証ガイド

`.github/workflows/tests.yml` は Windows と Ubuntu の GitHub hosted runner で、既存の全 pytest を実行する。2026-10-12 時点のローカル環境に合わせて Python 3.14 と uv 0.12.20 を指定し、2 job の最小構成にした。`pyproject.toml` の宣言上の最小 Python 3.12 は、この matrix では検証しない。

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

`tests/test_jobs.py::test_claim_waits_when_empty_lock_file_is_already_locked` は既存の Windows byte-range-lock 専用テストで、Ubuntu は理由付き skip、Windows は実行対象となる。これ以外に CI 用の skip は追加していない。新たな実通信 / 実 Codex 依存や platform 障害が見つかった場合は、テストを除外して成功扱いにせず、失敗と原因を報告する。

## 構文検証と今回の範囲

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

今回の作業はローカルの workflow / 本文の追加と検証まで。push、PR 作成、GitHub Actions の起動は行わない。ローカルの Windows テスト結果は Ubuntu や hosted runner 上の成功を証明しない。両 job の実行結果は、後日の許可された push / PR / 手動起動後に別途確認する。
