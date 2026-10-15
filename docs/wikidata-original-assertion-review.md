# Wikidata のタイトル・原言語 assertion 独立レビュー

2026-10-12。コードと保存済み raw / identity-map / SourceRecord / 保留診断を読み、観察と次 snapshot の仕様案だけを記録した。DB、frozen 2,000 件、分類 runner / models、原文 raw / results、保留状態は変更していない。追加 LLM・WDQS・entity API 取得はゼロ。一次資料の公開説明ページだけを参照した。原文統合の現状 **8 接続・12 保留**は [統合ガイド](prose-integration-guide.md) のまま。

## 確認した現在の変換

| 経路 | タイトル | P407 と scope |
| --- | --- | --- |
| `src/recommend_book/ingestion/wikidata.py` | label を先頭に置き、P1476 の monolingual text を保持。work は一律 `kind=original`、edition は P1476 を先頭にする | `claim_values()` は deprecated を除き、preferred があれば preferred のみ、なければ normal 等の非 deprecated。qualifier / reference は値選択に使わず、全 claims を `extra` と raw に保持 |
| `scripts/resolve_prose_wikidata.py` | `names()` の同定照合には P1476 を使うが、`source_record()` は英語 label または QID **だけ**を titles に保存 | 同じ `claim_values()` を利用。単一 QID を言語コード化し、work に `original_language`、edition に `language` を設定。British English Q7979 はこの経路だけ `en-GB` に対応 |
| `scripts/integrate_prose_poc.py` | 既存 immutable authority を利用 | raw 検証の `values()/qids()` は **全非 deprecated** の値集合。identity-map の rank 選択集合と異なると保留。本文と authority の言語コードも完全一致を要求 |

両変換とも `record_kind` に応じて作品 / 版の言語を分けるが、P407 自体が「創作時の原言語」を表すかは判定しない。`claim_values()` は statement ID・qualifier・reference を返さない。複数 statement が同じ言語 QID を持つ場合も重複したリストになり、長さが 1 でなければ言語は `None`。preferred の unknown/no-value に値がなければ normal に戻らず空になる。

汎用変換が work の全採用 P1476 を `original` と呼ぶ点にも過剰解釈がある。P1476 は刊行された名称で、monolingual text の言語はその**タイトル文字列**の言語。label は項目の通称であり原題・原言語の保証ではない。[P1476](https://www.wikidata.org/wiki/Property:P1476)、[Label](https://www.wikidata.org/wiki/Help:Label)。

## 保存済み 3 件の観察

3 原作を含む同じ raw ファイルの SHA を実読で確認した：`3f49f97e625ea572db9e62be6b94ad7e09492dbfa890dab99211df6286bab2b1`。以下はこの snapshot の assertion で、現在の項目を再取得した結果ではない。

| 原作 | 観察と原因 | 今回の結論 |
| --- | --- | --- |
| Jane Eyre Q182961 | 要求した英語 label が raw にない。P1476 は normal、`Jane Eyre` / `en`、statement `Q182961$16d56678-49df-c172-f06a-658f93371638`。専用変換が P1476 を落とすためタイトルは QID、さらに QID に `language=en` を付けている | 同定照合には P1476 が使われた。表示 placeholder 改善の根拠は既存 raw にあり、タイトルの創作は不要 |
| Frankenstein Q150827 | P407 は preferred 英語 Q1860 が 1、normal が 38、deprecated は 0。preferred に `P3831=Q126721911`、`P7452=Q17590740`。P1476 にも preferred 英語と normal の他言語がある | identity-map の `[Q1860]` と raw の 39 値集合は選択方針が異なる。これを英語本文が翻訳という証拠にしない。38 言語も翻訳と断定しない |
| Alice Q92640 | P407 は normal の Q7979 一つ。P1476 は preferred 英語タイトル、normal ドイツ語タイトル。authority は `en-GB`、本文は `en` | 言語タグの精度差。原作の P1476 の英語表記から authority を `en` に置換しない。完全一致保留を維持 |

Frankenstein の statement は `Q150827$265C855C-F9DD-4643-8BE0-1EDC3DB54016`。役割 Q126721911 の公式定義は「世界で書籍として最初に刊行された言語」であり、創作時の言語と常に同値ではない。役割 entity 自体はこの保存 raw にないため、意味の確認は公開一次資料に基づく。初出版が翻訳、連載後の書籍化、多言語創作は追加 scope 確認が必要。[Q126721911](https://www.wikidata.org/wiki/Q126721911)、[P3831](https://www.wikidata.org/wiki/Property:P3831)。

## 提案する次 snapshot の仕様（未実装）

1. **タイトルの候補を失わない。** 専用変換も P1476 の原文字列・言語・statement ID・rank・qualifier・reference・raw SHA を保持する。QID fallback は placeholder と明示し、言語を補わない。表示は有効な work label、なければ scope を確認した work P1476 の順に選び、採用 locator を残す。候補が複数なら表示上の選択と原題認定を分ける。版タイトルを作品の原題へ転記せず、原言語未確認の P1476 は自動で `original` と呼ばない。現在の DB `work_inputs()` は work titles の非 QID 値を探すため、Jane Eyre は変換時の候補保持が先に必要。
2. **全 statement と採用集合を分ける。** 全 claims を保存した上で、entity role、statement の scope / qualifier / reference、最後に rank を扱う。P407 は広く関連言語を表すため、preferred 単独で原文と認定しない。原版の言語役割は候補の根拠になるが、創作原言語と一致する確認が別に必要。作品上の unqualified 単一値も原言語候補として残し、版・翻訳・不明 scope の値や矛盾する原言語 assertion は自動採用しない。複数の創作原言語は単一 scalar に潰さず保留する。[P407](https://www.wikidata.org/wiki/Property:P407)、[Books モデル](https://www.wikidata.org/wiki/Wikidata:WikiProject_Books)、[Rank](https://www.wikidata.org/wiki/Help:Ranking)。
3. **同定と raw proof で同一 policy を再現する。** version 付きの選択規則、採用・除外 statement ID と理由を保存する。全非 deprecated 集合の SHA 検証と、scope 確認後の採用集合の比較を別々に行う。preferred-or-normal は既定の best-rank 抽出として妥当でも、原言語の意味保証ではない。normal の原言語矛盾を preferred で隠さず、unknown / no-value・不明役割・参照不整合は保留する。Frankenstein の解除にはこの policy の合意と原文 scope 再検証が必要。
4. **精度差と履歴を保持する。** QID、言語タグ、タイトル言語、版言語、本文言語を別 assertion として扱う。`en-GB` と `en` は将来「互換候補」として説明できるが、完全一致とは記録しない。言語タグの一般化だけで Alice を解除しない。新変換は新 SourceRecord 版・派生 input hash として作り、旧版・旧分類・保留理由・rights を保持する。

実装時の最低 fixture は、label 欠落と有効 P1476、翻訳題だけの P1476、preferred 原版言語と normal 不明言語、preferred と normal の原言語矛盾、初出版が翻訳、多言語原作、同 QID 重複 statement、unknown/no-value、`en` / `en-GB` と版言語の混在。保存済み未確定 10 件も、このタイトル改善だけでは版・著者・刊行年の保留条件を満たさない。今回、接続追加・保留解除・コード修正・再分類は行っていない。

## レビュー後の許可済みタイトル保持修正

上記観察を提出後、専用 `source_record()` と専用テストだけの改善が許可された。既存 label / QID とその属性を保持し、非 deprecated P1476 の原文字列・言語を `alternative` 刊行題候補として追加した。原文・翻訳の認定を意味する kind は付けない。全 P1476 statement は deprecated / unknown も含め、原 statement、locator、titles の対応 index、raw SHA / path を `extra.title_assertions` に保持する。`names()` / candidate 判定 / P407 / rank / 原言語方針は未変更。既存 placeholder の属性修正や原言語仕様案は未実装。

新規 2 テストの RED（2 failed / 11 passed）確認後、専用・統合テストは **19 passed**。`--output data/prose-poc/wikidata-resolution-v2 --replay-from data/prose-poc/wikidata-resolution-v1` で 4 保存応答をオフライン再生し、20 候補・10 matched / 10 uncertain・29 SourceRecord を出力した。Jane Eyre の P1476 候補が復元され、同定判断・タイトル以外の assertions は v1 と一致した。原文関連旧ファイル、既存独立 DB、frozen-v2、poc-v1、旧分類を含む **393 ファイルの前後 SHA が一致**。証跡は新出力の `offline-replay-audit.json`。この resolution の matched は原文 DB の接続数ではなく、Frankenstein / Alice の追加保留を解除していない。新 DB 生成・分類は実行していない。

## 汎用変換の後続修正と旧 snapshot の制約

固定 Q1044713（Taken at the Flood）の保存済み P1476 は normal の英語 `Taken at the Flood` とポルトガル語 `Arrastado na Torrente`。後者に P3005=Q45 と ISBN reference がある。旧変換は両方を `original` にしたが、これらは原題を認定する根拠ではない。原 raw SHA は `52adc3d4aac12343c2f68f8271367d8ad9a1dfb2787bf120c5e8d7a412df2ace`。

汎用 `WikidataAdapter.fetch_entities()` も、work / edition とも label の preferred を保持し、全非 deprecated P1476 の原文字列・言語を `alternative` 刊行題候補に変更した。P1476 の preferred と normal はどちらも候補として残す。statement の rank、qualifier、reference、locator、title index、source ID / URL / kind、raw SHA / path を `extra.title_assertions` に保存し、deprecated / unknown は候補に出さず原 statement を保持する。明示的 original-role の意味判定は今回実装していないため、P1476 から `original` / `translated` を付与しない。原言語・rank の既存選択関数・QID placeholder 処理は未変更。

専用 title fixture と旧期待の修正を **5 failed / 12 passed** で確認後、汎用・版収集・原文 resolver・統合の関連テスト **38 passed**。保存済み Q1044713 の実 raw を MockTransport と一時 RawStore だけで変換し、en / pt 候補、qualifier / reference、原言語値、元 raw SHA の保持も確認した。外部通信・追加 LLM・dataset 出力・DB 更新はゼロ。

この修正は将来の snapshot 作成に適用される。既存 selected / frozen / 本番 input / immutable DB に保存された `kind=original` を書き換えていないため、旧 snapshot には過剰な kind が残る。旧 title kind だけを原題や原文 scope の証明に使わず、原 statement と確認済み scope を参照する。将来移行する際は新 SourceRecord 版・入力 hash として扱い、旧分類との対応を別途検証する。
