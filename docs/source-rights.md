# ソース・項目・用途別の利用条件

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「現行ガイド・仕様」です。手順・契約の再利用前に、現在の状態は[current-status.md](current-status.md)、入力・権利・版の前提は本文で確認してください。

更新案内: 2026年10月26日。実行時の台帳は `config/sources.json`、パッケージ内のコピーは `src/recommend_book/ingestion/sources.json`。`rights.py` と `UsageGuard` が用途別のポリシー、原資料、公式条件の保存証跡を検証します。以下の一次資料の調査・少量PoCは2026年10月12日時点の記録であり、今回Web規約を再照合した結果ではありません。

現在の世界小説の差分処理は[取り込み手順](world-classification-expansion-guide.md)を参照してください。入力の取得・保存・外部LLM・表示の審査と、分類結果の出力審査を分けています。特定の入力の成功を、提供元の全データやToCサービス全体の許可へ広げません。無料資料と既存Codexだけを使用し、購入・外注・有料APIへ自動で切り替えません。

## 判定と対象範囲

`allowed` は確認した範囲で条件を満たす用途、`conditional` は記録された義務を満たした場合、`denied` は禁止、`unresolved` は未解決を表す。台帳にないソース・提供機関・項目・用途は許可しない。取得可能であること、無料であること、認証不要であることから利用許諾を推定しない。

対象の文章に適用される項目別ポリシーを `Evidence.rights_policy_id` に記録し、用途判定を通った根拠だけ `llm_input_allowed=True` にする。未解決の要約・注記は保存しても外部LLMへ送らない。書誌の出典、取得日時、入力SHA256、原データパスと根拠の箇所を保持する。分類結果の公開・商用利用・再配布は入力原文の条件と分けて確認する。

| 対象・ポリシー | 取得・ローカル保存 | 外部LLM入力 | 商用利用 | 表示・再配布 | 帰属・その他 |
|---|---|---|---|---|---|
| Wikidata構造化項目 `wikidata-cc0` | allowed | allowed | allowed | allowed | CC0。項目URL、revision、元のclaimsを追跡する |
| Open Library短い書誌値・主題 `openlibrary-metadata` | allowed: ダンプ利用 | allowed: 今回の研究PoCの短い事実・主題 | unresolved | 表示conditional、再配布unresolved | 表示時はレコードURL。CC0とは表示しない |
| Open Library説明文 `openlibrary-prose` | allowed: ダンプの原データ保存 | unresolved | unresolved | unresolved | 投稿由来の権利を個別確認するまで文章を隔離 |
| NDL限定提供機関の書誌・主題 `ndl-iss-ndl-opac` 等 | allowed | conditional | conditional | conditional | CC BY 4.0と互換の条件。提供機関・NDLサーチ・元URL・ライセンスリンク・正規化変更の表示 |
| NDL未確認の要約・注記 `ndl-prose-unresolved` | allowed: 限定提供機関のメタデータ保存 | unresolved | unresolved | unresolved | 書誌の条件を第三者の文章へ自動継承しない |
| Wikipedia確認済み節 `wikipedia-text` | allowed: opt-in | conditional | conditional | conditional | 帰属、share-alike、第三者文章の確認。原文と改変物の条件を保持 |
| Wikipedia自動検査済み文章 `wikipedia-screened-prose` | allowed: opt-in | conditional | conditional | conditional | 帰属、share-alike、改変表示、第三者文章の自動検査記録。人手確認とは区別 |

台帳の `retention` に、今回確認した範囲ではソース指定の保存期限を確認していないことを記録する。これは無期限保持の法的保証ではない。出典・ライセンス情報・改変表示を原データと正規化データに伴わせる。

## 一次資料で確認した条件

Open Libraryは大量メタデータ取得にAPIを使わず月次ダンプを使うよう案内している。作品・著者・版のgzipを行単位で処理する。ダンプは型・キー・revision・更新日時・JSONの5列。APIの既定1 request/s、識別済み3 request/sは大量取得の許可ではない。[API方針](https://openlibrary.org/developers/api)、[ダンプ仕様](https://openlibrary.org/developers/dumps)。

Internet Archiveが新たな権利を主張しないという案内には、既存の投稿や法域の権利問題があり得る旨もある。説明文・画像・本文を一括してCC0や商用利用可能とは扱わない。台帳の短い書誌値に対する研究PoCの運用判断と、未確認の文章の権利は分離する。[ライセンス案内](https://openlibrary.org/developers/licensing)。

WikidataのCC0は構造化項目に適用され、Wikipedia記事や外部リンク先の文章・画像の条件にはならない。`description` は項目識別の短い説明であり、あらすじとして水増ししない。[Licensing](https://www.wikidata.org/wiki/Wikidata:Licensing)、[Description](https://www.wikidata.org/wiki/Help:Description)。

NDLの提供機関一覧で `iss-ndl-opac`、`iss-ndl-opac-national`、`aozora` のメタデータ条件を確認した。今回のライブ取得は `iss-ndl-opac` だけを指定する。他の提供機関には自動拡張しない。提供機関・データ種別により条件が異なり、例えばJ-STAGEの要約値は一律利用不可と明記されている。[提供機関と項目の条件](https://ndlsearch.ndl.go.jp/help/api/provider)。

NDL利用サイト・アプリにはAPI利用を明記し、CC BY対象には提供機関の名称とライセンスへのリンクを保持する。多重アクセスを避けるため、共有RawStoreのSQLite通信ゲートで通信自体を直列化する。通常の分類並列度とは独立して制御する。[API利用案内](https://ndlsearch.ndl.go.jp/help/api)。

SRUは501件目以降を取得できない。取得済み件数・検索総件数・500件の位置上限を別々に記録し、少量取得や上限到達を全件取得成功と呼ばない。検索用APIは同定後、OAI-PMHは提供元の同定前レコードを対象とする。[2026年9月25日版インタフェース仕様](https://ndlsearch.ndl.go.jp/file/help/api/specifications/ndlsearch_api_20260925.pdf)。

OAI-PMHでは `from` が必要で、期間は最大1年、1応答200件。続きを取得する場合は継続トークンを他の選択条件と混ぜず渡す。トークン、期間、提供機関、削除通知、原応答、ページログを保存してからcheckpointを進める。大量更新には月次全件ファイルも検討する。[OAI-PMH公式案内](https://ndlsearch.ndl.go.jp/help/api/oai_pmh)。

DC-NDLの構造化タイトルの値と読み、書誌本体の作成者とメタデータ作成機関、版の刊行年と作品初出年を区別する。`abstract` は要約、`description` は注記として種類を保持し、許諾未確認の文章はLLM入力不可にする。[DC-NDL ver.3](https://ndlsearch.ndl.go.jp/renkei/dcndl/version3)。

## Wikipedia文章の補完と制限の引継ぎ

Wikimediaの利用規約はライセンスに従う商用利用を認め、本文の帰属として執筆者履歴へ辿れるページURL等を認める。原文を修正して配布する場合はCC BY-SA 4.0以降等の適用条件に従い、変更とライセンスを表示する。一方、fair-use引用、輸入文章、画像等は別条件になり得る。[Wikimedia利用規約 第7節](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use)、[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。

補完レコードには言語、ページID、厳密なrevision ID、revision時刻、節名、固定版permalink、執筆者履歴URL、ライセンスURL、抽出・除去等の変更記録を保存する。Wikipedia原文とその改変物をCC0/public domainとして再配布しない。LLM入力に許可することと、その入力原文や生成物を任意の条件で公開できることを同一視しない。

`wikipedia-text` の第三者文章確認と `wikipedia-screened-prose` の自動検査は別ポリシー。自動検査は引用・テンプレート・外部リンク由来の文章などを除去・除外し、その処理を記録する運用であり、法的完全確認や人手レビュー、人手gold作成を意味しない。ページ固有の追加帰属・互換性を確定できない輸入文章は使用しない。再配布する文章には帰属・share-alike・改変表示を引き継ぎ、生成ラベルには別の出力確認を残す。

## NDL少量PoCの実行と採用判定

```powershell
uv run python scripts/collect_ndl_poc.py --limit-per-query 10
uv run python scripts/collect_ndl_poc.py --reassess-snapshot
```

既定出力は `data/ndl-poc/`。原応答とSHA256は `raw/`、採用版は `records.jsonl`、不一致・未確認レコードは `rejected.jsonl`、検索条件・総件数・打切り・採用理由は `manifest.json` に保存する。返却件数、採用した異なる版数、名寄せ後の作品数を分ける。

検索式は提供機関をアダプタが固定する平坦なAND式とし、他提供機関を指定する条件やORによる拡張を拒否する。NDLの検索成功だけを採用根拠にせず、返却されたNDC分類・刊行年・言語・ISBN等を独立照合する。翻訳専用の採用には明示された原文言語を必要とし、欠ける場合は隔離する。ISBNはチェックディジットとISBN-10/13の等価性を検証する。これらは書誌上の採用判定であり、小説内容の人手評価や作品同定の完了ではない。

`--reassess-snapshot` は保存済みの正規化値に対する採用条件だけを再評価し、通信を発生させない。元の取得日時・原データハッシュ・検索応答の状態は保持する。新しい取得と呼ばず、元スナップショット日時をmanifestに保存する。以前のmanifestは `run-manifests/`、各検索の進捗は `query-log.jsonl` に残す。

### 2026年10月12日のライブ確認結果

提供機関 `iss-ndl-opac`、1クエリ最大10件、7クエリを直列実行した。複合式を括弧で囲んだ初回試行は `illegal query syntax` となり採用0件だった。平坦なAND式へ修正した試行で取得に成功した。初回の原応答もraw manifestに残っている。下表は修正後の取得と、ISBNの等価性を保存済みデータ上で再評価した結果。

| 検索層 | 検索総件数 | 返却 | 採用 | 状態 |
|---|---:|---:|---:|---|
| 日本語小説分類913.6・刊行2024 | 754 | 10 | 9 | truncated |
| 英語小説分類933.7の日本語版・刊行2024 | 71 | 10 | 10 | truncated |
| 仏語小説分類953.7の日本語版・刊行2024 | 1 | 1 | 1 | complete |
| 独語小説分類943.7の日本語版・刊行2024 | 3 | 3 | 3 | complete |
| 中国語小説分類923.7の日本語版・刊行2024 | 3 | 3 | 1 | complete |
| ISBN 9784101010137 | 1 | 1 | 1 | complete |
| ISBN 9784151200533 | 1 | 1 | 1 | complete |

返却計29、採用した異なるNDL版レコード26、隔離3。検索の総件数は相互重複し得るため母集団件数に合算しない。隔離理由は刊行年不一致1件、翻訳元言語未明示2件。ISBN検索で返されたISBN-10は検証済みのISBN-13へ比較用に変換し、元の表記は保持した。検索に含まれたこと自体を採用理由にしない動作を実応答で確認した。

この26版は作品名寄せや分類品質の承認済み作品数ではない。日本語版の原語別補完を少量で確認したもので、世界の小説全体の分布・網羅率を表すサンプルでもない。あらすじ・注記の外部LLM利用許可は0件。

## Wikidataの版・翻訳資料

`P629` の公式の意味は「version, edition or translation of」であり、印刷版だけに限定されない。作品と版を分け、関係の元項目ID・対象作品IDと原claimsを保持する。`P212` はISBN-13、`P957` はISBN-10、`P655` は訳者の項目。ISBNは版スコープにだけ入れ、版の刊行日・言語を作品の初出年・原言語へ自動転記しない。[P629](https://www.wikidata.org/wiki/Property:P629)、[Booksモデル](https://www.wikidata.org/wiki/Wikidata:WikiProject_Books)、[P212](https://www.wikidata.org/wiki/Property:P212)、[P957](https://www.wikidata.org/wiki/Property:P957)、[P655](https://www.wikidata.org/wiki/Property:P655)。

`scripts/collect_wikidata_editions.py` は固定作品フレームのIDを30〜100件ずつVALUESで限定して照会する。1ページ500関係以下、PoCの既定上限は異なる版候補300件、最大500件。候補上限・対象作品の未走査・応答失敗はmanifestに区別して記録する。作品の著者を版の著者としてコピーせず、著者が欠ける版は欠損として数える。同じソースからの継承値を独立した裏付けと表示しない。P629の広い意味と版種別・ISBN・出版情報の欠損を残し、版レコード数を作品数へ加算しない。

2026年10月12日の版収集開始時、WDQSの原応答はHTTP 429で、進行中の障害に伴う「1分に1要求」の制限を明示した。これは恒久的なAPI規約としての推定ではなく、その取得時点の実応答である。版収集ではWDQSホストに61秒以上、エンティティAPIホストには通常の間隔を適用する。`HTTPFetcher(host_intervals={"query.wikidata.org": 61})` で明示設定でき、Retry-Afterはこれより長ければ優先する。Retry-Afterの期限は同じraw保存先のSQLiteへ保存し、中断後の再起動でも守る。`raw/http-waits.jsonl` に要求待ち時間と実測待ち時間を、raw manifestのmetadataにRetry-Afterと設定間隔を記録する。待機未完了はpendingのまま残す。異なるraw保存先同士はゲートを共有しないため、別収集との同時通信も避ける。

最初の試行は旧実装がRetry-Afterヘッダを保存しておらず、値と期限を再現できなかった。既知の1分制約に合わせる設定更新のため旧プロセスを停止し、最後の応答から283秒以上の経過を実測して、一般的なバックオフ後の限定再試行を行った。不明ヘッダを「61秒だった」とは扱わない。この経緯は `data/wikidata-editions-poc/configuration-upgrade.json` に残す。新実装の再試行で2026-10-12T00:51:03ZにHTTP 429とRetry-After=1000秒を確認し、原応答・ヘッダ・待機期限を保存した。明示された期限まで新たな要求を出さず、取得成功前の版数を0、manifest状態をwaiting_retry_afterとして記録した。
