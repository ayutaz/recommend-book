# 自動資料補充の実行と制約

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

確認日: 2026-10-13。共通仕様は `config/autonomous-evaluation-v1.json`、基準は `data/autonomous-poc-v1/baseline-manifest.json`。本レーンは取得・許諾・作品同定・入力提案の生成までを実施する。LLM分類は実行せず、人手gold・人手確認済みとは表示しない。

## 固定対象と実行結果

`data/recommendation-poc-v3/world-works.json` の2020作品から `features` が空の1026作品を固定した。原言語・初出年代（1900以前、1901–1945、1946–1999、2000以降、不明）・資料種別を層とし、各層を安定したSHA256順で選び、残枠も同順で補う100件の試行を先に実施した。希少層を多く含むため、母集団を代表する無作為標本ではない。

固定100件では12作品・12根拠（あらすじ10、導入文2）を採用、88作品を保留した。その成立後に全1026件へ同じ条件で展開し、79作品・81根拠（あらすじ65、導入文16）を採用、947作品を保留した。資料を加えたことは、分類ラベルの成立や精度改善の確認を意味しない。

Wikipediaの新規API取得は試行12要求、残り19要求の計31要求で、全てHTTP 200だった。初期取得結果を保持し、追加帰属通知の全記事検査と義務4項目の継承を強化した最終再検査には、固定キャッシュだけを使い追加要求0とした。最終成果物は **`data/autonomous-poc-v1/enrichment/final-v1/full/`** のみを採用対象にする。`enrichment/pilot/`、`enrichment/full/`、`strict-v2/` は検証途中の保存物で、採用対象ではない。各台帳は原データの絶対パスとSHAを保持するため、最終ディレクトリだけを移して原物を削除してはいけない。

| 原言語 | 対象 | 補充採用 | 保留 |
|---|---:|---:|---:|
| 英語 | 327 | 12 | 315 |
| 日本語 | 303 | 21 | 282 |
| 韓国語 | 55 | 13 | 42 |
| フランス語 | 54 | 0 | 54 |
| イタリア語 | 44 | 21 | 23 |
| ドイツ語 | 44 | 0 | 44 |
| ポルトガル語 | 40 | 4 | 36 |
| ロシア語 | 38 | 2 | 36 |
| アラビア語 | 38 | 0 | 38 |
| スペイン語 | 37 | 1 | 36 |
| 中国語 | 27 | 4 | 23 |
| ベンガル語 | 10 | 0 | 10 |
| 不明 | 9 | 1 | 8 |

年代別は1900以前2/46、1901–1945が7/95、1946–1999が20/282、2000以降11/296、初出不明39/307。資料種別は小説69/690、短編9/327、ライトノベル1/9。採用の言語・年代・種類の偏りを解消できていない。

保留理由は作品単位の非排他的集計で、対応する対応言語の記事なし515、拒否段落を含む節420、既存入力と同じ本文14。全1026件の詳細は `outcomes.jsonl`、特性別集計は `final-v1/coverage-audit.json`。主題や原文の情報不足をタイトル・作者の有名さから補わない。

## 同一性と入力の固定

保存済みWikidata原物の `entities.QID`、作品種別、作品ID、SHAを確認し、その原物に明示されたsitelinkだけを取得候補にする。Wikipedia応答はnamespace 0、曖昧さ回避ページでないこと、`pageprops.wikibase_item` が対象作品QIDと完全一致すること、保存済み原応答と処理ページが完全一致すること、単一の正のrevision ID、原物SHAを検査する。タイトル類似による結合は実施しない。

原言語、英語、日本語、対応言語順の最大2記事を使う。対応言語の導入文と、言語ごとに限定したあらすじ節だけを対象とする。導入文は `note`、あらすじは `synopsis`。記事の執筆文を小説原文の `excerpt` として扱わず、原文の文体・翻訳表現・主人公属性を推定しない。

新しい `SourceRecord` は既存active Wikidataソースの同じ `(source, source_id)` を使い、書誌値・識別子・元raw・extraを完全に保持し、根拠だけを追加したimmutable版の提案である。追加Wikipedia根拠は自身のCC BY-SAポリシーと別rawを保持し、WikidataのCC0へ付け替えない。

`accepted-inputs.jsonl` は元 `source_record_ids` を完全保持した **入力提案**。DBレーンが旧版・raw・用途・根拠との一致を検証し、immutable版を追加し、再生成したDB入力に新しいrefsと入力hashを確定する。最終DB入力はDBレーンの別ファイルで、提案を上書きしない。既存2020 canonical、元分類input/results、固定baselineの16原物SHAは前後に照合して不変を確認した。

## 用途別の許諾と文章の除外

Wikimedia利用規約第7節は通常の本文をCC BY-SAで再利用する条件、商用利用、記事履歴への帰属、改変表示、輸入文章に追加帰属があり得ることを示す。CC BY-SA 4.0は再利用・改変を認めるが条件がある。本実装の外部LLM入力は、この一般的な再利用許諾と既存 `wikipedia-screened-prose` に基づく用途別の運用判断であり、提供元の個別AI認定や生成物の権利保証を意味しない。[Wikimedia利用規約](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use)、[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/)。

| 対象 | 取得・保存 | 外部LLM・商用・表示・再配布 | 本実行で満たす条件 |
|---|---|---|---|
| Wikipedia `note/synopsis` | allowed | conditional | 固定版URL、執筆者履歴、credit、CC BY-SAリンク、改変表示、share-alike継承、自動第三者文章検査記録 |
| NDL `iss-ndl-opac` 書誌・主題 | allowed | conditional | 既存policyの帰属、NDLサーチAPIと提供機関の表示、ライセンスURL。版ISBNと完全な著者authorityを別途検査 |
| NDL abstract/description | 保存範囲のみ | unresolved | LLM入力へ昇格させない |
| Open Library description | 既存raw保存範囲のみ | unresolved | 旧112件拒否を維持し、新取得や本文の付け替えなし |

節内の引用・テンプレート・HTML・外部リンク・その他拒否段落が一つでもあれば **節全体を拒否** する。著作権・輸入本文・追加帰属を示す記事内テンプレートは全記事保留にする。従前の部分検査で拒否を伴った節の本文hashとOpen Library拒否文章を合わせた912種類を固定し、同じ本文を別ID・別ポリシーで許可しない。既存入力本文と同じ根拠も補充数へ加算しない。

自動検査は無標識の第三者文章を全て検出できる法的完全確認ではない。原文と改変物にはライセンス・帰属・変更表示を継承する。外部LLMに送るEvidenceにも属性を保持するが、生成ラベルが入力原文の複製・改変になった場合まで任意条件で再配布できるとは扱わない。

APIは直列、最大50タイトル/要求、キャッシュ優先、識別可能なUser-Agent、`maxlag=5`。既存HTTPFetcherの共有SQLiteゲートとbounded retry、明示Retry-Afterの保存・遵守を使う。負荷時のAPI errorは採用成功にしない。API利用方針とMediaWiki技術資料は直列化・適切な識別・キャッシュ・制限の遵守を求める。[Wikimedia API利用方針](https://foundation.wikimedia.org/wiki/Policy:Wikimedia_Foundation_API_Usage_Guidelines)、[MediaWiki API:Etiquette](https://www.mediawiki.org/wiki/API:Etiquette)。

## NDL代替経路と原文の限界

NDLは提供機関・用途ごとに条件が異なるため、取得済みregistryの許可provider `iss-ndl-opac` だけを指定する。API利用表示とメタデータの提供元帰属を保持し、未確認abstractをあらすじとして使わない。今回provider一覧のweb再取得はtimeoutとなったため、その一覧について新たな確認済みとは表示せず、2026-10-12確認済みの固定policyを利用した。用途別の手続きと並行通信禁止は現行のAPI案内で再確認した。[NDL API案内](https://ndlsearch.ndl.go.jp/help/api)、[提供機関別条件](https://ndlsearch.ndl.go.jp/help/api/provider)。

代替クエリは作品rawに明示された `P212/P957` ISBNだけを使い、1検索最大5版。応答のISBN一致に加え、rawの全 `P50` 著者と保存済みauthor rawの `P349` NDL authorityを全件確認し、版の全著者authorityと一致すること、巻次の矛盾がないことを必要とする。名前や題名が似るだけのものは保留する。版・翻訳資料の主題は原文文体の根拠にはならない。

NDLの実応答は個人名の `auth/entity/ID` を用いた。公式URI仕様は同じ典拠IDに `auth/ndlna/ID` の典拠情報URIと `auth/entity/ID` の名称実体URIを付与し、SRU等で後者を使うことを明示している。対応をTDDで修正し、正規化したISBN・著者authority・work_refs・主題を原XMLから再生成した全レコードと一致させ、後付け識別子を拒否する。[NDLの公式URI仕様](https://www.ndl.go.jp/dlib/standards/lod/uri)。

31作品に明示された34 ISBNを直列検索し24版を保存した。1クエリ5件までの上限打切り0。10クエリはHTTPエラーではなくSRU `Record does not exist` diagnosticだったため、adapterのfailed状態を隠さず、照合資料が得られなかった結果として残す。ISBNのない残る916保留作品は題名検索で範囲を広げなかった。

最初のURI限定判定は全24版を保留した。この保存結果を上書きせず、追加通信0の再判定を `data/autonomous-poc-v1/enrichment/ndl-identity-v2/ndl-identity-reassessment/` に出した。識別子・著者・巻次では18版一致、6版保留。著者authority不一致4・巻次scope不一致5は重複3があるため、理由件数を足して9版保留と表示しない。主題付きは5作品5版だが、4件はNDC `913.6` のみ、1件は `Short stories.; Canada -- Fiction.; PZ7.M768` だった。NDCの広い書誌分類は現行72項目の物語・テーマ・主人公・文体等を裏付ける内容根拠にはならない。

非NDC1件は **The Road to Yesterday / Q105232604 / work_a82aa900a860db5081aa779d**。NDL英語1974年版 `R100000002-I000006277338` のISBN-10 `0070777217` は作品rawのISBN-13と等価で、Montgomeryの著者authority `00450382` も一致する。しかし、対象WD rawの `P31=Q3331189` は版クラスで、legacy正規化の `record_kind=work` とscope矛盾がある。NDL rawには包含タイトルが複数あり、Short storiesは短編集という書誌の範囲を支えるが、各話の物語内容・人物・文体を支えない。版と原著作の明示対応がなく、本体のnovelや単一短編という型をAIで補わない。今回の有用な内容補充は **0件** として終了した。

scope検査を追加した結果を `enrichment/ndl-scope-v3/ndl-identity-reassessment/` に別保存し、版class対象1件を拒否した。17版が書誌の同定条件を満たすが、主題付きはNDCのみの4件に減り、分類入力追加0。採用しない理由はvalidator対応の有無ではなく、内容根拠の不足と対象scopeの矛盾である。Wikipedia固定79作品は変えていない。

## 既存作品scopeの隔離

2020 canonicalを読み取り専用で調べ、active Wikidata rawを持つ2008件の `P31` をSHA付きで検査した。他sourceの12件は同じWDクラス検査を適用できないため、未検査を一致0と数えず一覧を保持した。`P31=Q3331189`（version, edition or translation）を持つのにworknodeになっていた15 canonicalを **`data/autonomous-poc-v1/enrichment/work-scope-quarantine.json`** に固定した。class entityの原物とラベル、各canonical/source/version、P31/P7937/P629/ISBNの原claimsと正確locator、取得したNDL版の証拠を含む。Wikipedia採用79との重複は0。

元の正規化値・旧DB・旧分類結果はimmutableのまま保持し、この15件を新しい最終world eligibleから隔離する契約をDB・納品レーンへ渡した。旧2020件の保存と、新納品で2020件全てを有効とすることを混同しない。

訂正履歴: 当初Q3331189をshort story collectionと誤って説明したが、保存済み一次class entityのラベルを確認して **版・version・translationクラス** と訂正した。短編集という判断はNDL包含タイトルとsubjectに基づくもので、このQIDの意味ではない。同定18/24とscope可17/24も区別する。これらの訂正はquarantine JSONにも保持した。

権利確認済み原文policyは、作品ごとに原文版、原著者没年、原文言語、翻訳・近代注記の有無、JP/USの確認済み事実を必要とする。本補充ではその情報を新たに確認できない作品へ原文を追加していない。古い作品であることだけから全世界public domainと推定しない。Wikipedia概要は原文の代替にならないため、文体・原文表現の資料不足を残す。

## 再実行と検証

```powershell
uv run pytest tests/test_autonomous_enrichment.py tests/test_wikipedia_prose.py tests/test_ingestion_ndl.py tests/test_autonomous_database.py -q
uv run python scripts/prepare_autonomous_enrichment.py --phase pilot --root data/autonomous-poc-v1/enrichment/new-run-v2
uv run python scripts/prepare_autonomous_enrichment.py --phase full --root data/autonomous-poc-v1/enrichment/new-run-v2
uv run python scripts/prepare_autonomous_enrichment.py --phase full --root data/autonomous-poc-v1/enrichment/new-run-v2 --ndl-alternative
uv run python scripts/prepare_autonomous_enrichment.py --root data/autonomous-poc-v1/enrichment/new-ndl-assessment --reassess-ndl-from data/autonomous-poc-v1/enrichment/final-v1/ndl-alternative/checkpoint.json
uv run python scripts/prepare_autonomous_enrichment.py --root data/autonomous-poc-v1/enrichment/new-scope-audit --scope-audit
```

実装前に失敗テストを実行した。対象・関連50テストが成功。取得テストはfixtureで、ライブ通信をテストから実行しない。resumeは固定入力・policy・節設定・拒否hash・実装hashが一致するcheckpointのみ許可する。最終79/81は固定済みなので再実行で上書きせず、新しい用途・規則や追加資料は別namespace/差分にする。NDLのURI対応と原XML再検証はWikipedia固定後に追加したため、最終79/81の実行時実装hashと現在のファイルhashは異なる。Wikipedia本文抽出規則の変更ではなく、旧namespaceを現行実装で上書き再実行しない。
