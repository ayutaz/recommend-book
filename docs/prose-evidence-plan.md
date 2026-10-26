# 原文抜粋の確保と文体軸の少量評価計画

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

調査日: 2026年10月12日。対象は、日本で運用するこのPoCの文体分類に用いる20〜50作品の実際の原文抜粋。公開・商用化の完了を意味しない。

## 現状と今回の結論

本番分類Schemaの5作品試行は構造上成功したが、あらすじ・主題等だけでは原文の文体を判定できず、文体軸のunknownが残る。これは根拠不足として維持する。一方、文体軸自体の評価を全件unknownで終えないため、通常の100作品試行とは別に、権利・表現・底本・箇所を確認した20作品からなる原文抜粋セットを作り、30〜40作品へ増やす。

最初の20作品は、以下に挙げる19世紀以前の英語原作を優先する。Wikisourceの底本が明確な校訂済み章のAPI取得、またはProject Gutenberg（PG）が公式に列挙するミラーの対象ファイル取得を使う。日本語原作10作品、仏語原作10作品を次段階とする。現代の保護作品や新しい翻訳を「短い抜粋だから自由」「AIだから自由」として追加する運用は採らない。

本調査で確認したのは提供元の方針、取得経路、候補の書誌入口、既存モデルとの接続方法である。20作品すべての特定版の権利審査・本文取得・文体分類・人手精度評価はまだ実施していない。以降の件数・文字数は実施案である。

## 確認した利用条件

| ソース・資料 | 確認済みの内容 | このPoCへの適用 |
|---|---|---|
| PGの米国で非保護の本文 | 米国外の利用には現地法の確認が必要。PGは米国外の自由利用を保証しない。著者から配布許可を得た保護作品も混在する | 国・作品・版ごとの確認を行い、PG掲載や米国状態だけで日本の許諾をallowedにしない |
| PGの取得経路 | 主サイトは人間向けとされ、自動アクセスの遮断方針がある。自動取得にはミラー、書誌にはオフラインカタログ等の経路を案内 | 20件程度でも主サイトの本文URLをループ取得しない。公式ミラーの対象ファイルだけを取得する |
| Wikisource | 主に米国法に基づく収録方針。PD本文または互換の自由ライセンス作品を収録し、利用者による新規創作・翻訳等は原則CC BY-SA | 原典の本文と、編集者の解説・新訳・注釈を分離する。ページのフッターを本文の一律PD判定に使わない |
| 日本での保護期間 | 原則死後70年、翌年起算。2018年の延長で既に消滅していた権利は復活しない。外国作品では相互主義・戦時加算・旧法の確認が必要 | 著者没年だけの一律計算を採用しない。著作物・国・初出・版の審査結果を保存する |
| 翻訳 | 原作と翻訳物は別の権利の層を持つ | 原作者の権利消滅だけで現代訳を送信しない。訳者、没年、翻訳初出、当該版も確認する |

根拠: [PG Terms of Use](https://www.gutenberg.org/policy/terms_of_use.html)、[PG License](https://www.gutenberg.org/policy/license.html)、[Wikisource Copyright policy](https://en.wikisource.org/wiki/Wikisource:Copyright_policy)、[文化庁・保護期間延長Q&A](https://www.bunka.go.jp/seisaku/chosakuken/hokaisei/kantaiheiyo_chosakuken/1411890.html)、[文化庁・令和8年度著作権テキスト](https://www.bunka.go.jp/seisaku/chosakuken/seidokaisetsu/pdf/94383901_01.pdf)（保護期間・旧法・翻訳の説明）。

### 日本在住の運用と将来の商用利用

確認対象は「原典の著作権」「使用する表現・版の権利」「提供元・ミラーの条件」「表示する名称・商標」「外部LLMへの送信条件」で分ける。日本での権利消滅を確認しても、世界中での権利消滅を保証した状態にはしない。現行の利用地域を日本と、確認した外部処理先の範囲として台帳に記録し、将来、未確認の地域向けに配布する場合はその地域を追加審査する。

PGの本文とPG名称・ライセンスは別に扱う。名前を保持・関連付けた再配布や販売には、全文ライセンスの1.E等の条件が適用され、料金を取る場合のロイヤリティや、別条件での頒布に関する書面許可の規定もある。ライセンスの説明部は、謝辞・参考文献・奥付でのリンク付き言及を商標使用に当たらない例としている。将来の商品名・広告・表紙にPGを使うことや、「PG由来だからすべて商用自由」とすることは別問題である。[PG License](https://www.gutenberg.org/policy/license.html)、[PG Permission How-to](https://www.gutenberg.org/policy/permission.html)。

このため、研究時の原ファイルはヘッダー・ライセンス込みで保存し、LLMには確認済みの本文箇所だけを送り、出典と条件は台帳に残す。公開用の根拠文は、名称との関連・ライセンスの扱いを確認した別成果物とする。「文字列を除去すれば日本でもすべて解決する」とは判断しない。内部出典情報を消して追跡不能にすることもしない。

CC BY-SAの新訳・注釈を使う場合は、帰属、ライセンスURL、変更表示、改変物共有時の継承等を管理する。分類ラベルやモデル出力全体が必ず同ライセンスの改変物になる、あるいは必ず無関係になる、と事前に断定しない。引用・要約・本文と分離して判断する。初回20作品ではこの追加審査を抑えるため、現代の創作部分を含まない原典の本文を優先する。[CC BY-SA 4.0 Legal Code](https://creativecommons.org/licenses/by-sa/4.0/legalcode.en)。

## 作品・表現の候補を作る方法

### Wikidata識別子

`P2034` はProject Gutenberg ebook IDで、書式は数値ID、案内URLは `https://www.gutenberg.org/ebooks/{id}`。その対象種類には「版・バージョン・翻訳」が指定される。作品IDとのリンク候補であり、そのファイルが原文だという証明ではない。[P50（著者）](https://www.wikidata.org/wiki/Property:P50)、著者の[P570（死亡日）](https://www.wikidata.org/wiki/Property:P570)、[P407（言語）](https://www.wikidata.org/wiki/Property:P407)、[P577（刊行日）](https://www.wikidata.org/wiki/Property:P577)、原作・版関係の主張、Wikisourceのsitelinks等を併せて取得する。没年はWikidataの参照元も保存し、矛盾や未詳年を保留する。[Wikidata P2034](https://www.wikidata.org/wiki/Property:P2034)。

手順は次のとおり。

1. rootが確定した作品DBから、原言語がenで、以下の候補と照合できる作品を選ぶ。文字列タイトルだけで作品を統合しない。
2. 作品・関連する版/表現のエンティティからP2034とWikisourceリンクを集める。P2034未登録だけで作品を除外せず、下表の書誌入口から底本を調べる。
3. 本文の言語・タイトル・著者・訳者・底本・改訂版・原作初出との対応を確認する。著者・言語の一致のみで翻訳や再話を除外したことにはしない。
4. 判定対象の原文表現を固定する。原作の1818年版と1831年改訂版等は本文版を記録し、混ぜない。
5. 原典・底本の権利情報と日本での審査を満たした候補だけを本文取得・抜粋作成へ進める。

### 初回20作品の具体的な入口

表は**候補**であり、当該提供ファイルの全条件を審査済みとするものではない。著者・作品一覧の入口は今回確認した。作品別ページには複数版や曖昧さ回避があり得るため、API取得対象はその先の特定版・章に固定する。特にDickensの一般名ページは曖昧さ回避だったため、本人の正式名ページを用いる。

| 枠 | 著者・没年（候補抽出用） | 作品候補 | 書誌・本文への入口 |
|---|---|---|---|
| 1–6 | Jane Austen・1817 | Sense and Sensibility / Pride and Prejudice / Mansfield Park / Emma / Northanger Abbey / Persuasion | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Jane_Austen) |
| 7–10 | Charles Dickens・1870 | A Tale of Two Cities / Great Expectations / Oliver Twist / David Copperfield | [Wikisource本人・作品一覧](https://en.wikisource.org/wiki/Author:Charles_John_Huffam_Dickens) |
| 11 | Mary Shelley・1851 | Frankenstein（1818年版・1831年版を分離） | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Mary_Wollstonecraft_Shelley) |
| 12 | Lewis Carroll・1898 | Alice's Adventures in Wonderland | [Wikisource本人・作品一覧](https://en.wikisource.org/wiki/Author:Charles_Lutwidge_Dodgson) |
| 13 | Herman Melville・1891 | Moby-Dick | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Herman_Melville) |
| 14 | Oscar Wilde・1900 | The Picture of Dorian Gray（1890年/1891年本文を区別） | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Oscar_Wilde) |
| 15–16 | Robert Louis Stevenson・1894 | Treasure Island / Strange Case of Dr Jekyll and Mr Hyde | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Robert_Louis_Stevenson) |
| 17 | Charlotte Brontë・1855 | Jane Eyre | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Charlotte_Bront%C3%AB) |
| 18 | Emily Brontë・1848 | Wuthering Heights | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Emily_Jane_Bront%C3%AB) |
| 19 | Nathaniel Hawthorne・1864 | The Scarlet Letter | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Nathaniel_Hawthorne) |
| 20 | Daniel Defoe・1731 | Robinson Crusoe | [Wikisource著者・作品一覧](https://en.wikisource.org/wiki/Author:Daniel_Defoe) |

著者没年1900年以前・原作初出1900年以前を最初の候補条件とするのは、保護期間の余裕が大きい作品から審査を始めるためである。これは自動的な世界PD判定ルールではない。死後に初めて出版された文書、現代編集者の序文、現代訳、再話、合本の解説を同じ条件で通さない。

20作品が採用できない場合の予備候補は、同じ確認済みの著者一覧からAustenのLady Susan、MelvilleのTypee、StevensonのKidnapped、HawthorneのThe House of the Seven Gablesなどを選ぶ。ただし死後初出・本文版を改めて確認する。同一作者に偏るため、このセットを世界の小説の代表標本として使わず、作者グループを評価分割で分離する。

### 30〜40作品への拡張案

日本語原作10作品は、青空文庫または日本語Wikisourceで、著作権が消滅した原作本文と特定底本を確認できるものから採用する。例として、樋口一葉、国木田独歩、夏目漱石、森鷗外、芥川龍之介、宮沢賢治の小説を調べる。本文・ルビ・外字注記と編集者の説明を分離し、現代語訳を原文にしない。青空文庫にも著作権者の同意で掲載された保護作品があり、ファイルの区分と条件を確認する。[青空文庫・取り扱い規準](https://www.aozora.gr.jp/guide/kijyunn.html)、[作品収録を望まれる方へ](https://www.aozora.gr.jp/guide/shuuroku.html)。

青空文庫の規準は、権利消滅作品について有償/無償の複製・再配布・共有や活用を認め、由来情報の保持と変更履歴の記録を望んでいる。一方、保護作品は私的使用を超える利用に許諾が必要で、訳者の権利も別に判定する。書誌データはCC BY4.0、作業マニュアルはCC BY-NC4.0であり、文庫の素材全部を同じ用途で商用可能とは扱わない。日本で権利消滅済みでも、外部処理先での死後出版作品等の保護状態は別に確認する。[青空文庫・取り扱い規準](https://www.aozora.gr.jp/guide/kijyunn.html)。

仏語原作10作品は仏語Wikisourceで、Balzac、Flaubert、Hugo、Dumas、Maupassant等の原文・底本を候補化する。英訳PGを文体根拠に用いる場合は翻訳表現専用の別評価となり、仏語原文文体には加算しない。これら追加候補の特定版・本文・権利は今回未審査。40件で必要な表現範囲が揃わない場合だけ、最大50作品へ拡張する。

## 少量取得の経路

### Wikisourceを第一経路とする

特定版・章のページIDとrevision IDを先に確定し、Action APIの `query` で履歴・ページ情報を、`parse` の `oldid` 指定で固定版の本文HTMLを取得する。ナビゲーション、著者紹介、権利テンプレート、目次だけを本文と誤認しない。ProofreadPageのIndex/Page参照、底本スキャン、校訂状態も保存し、読める本文のある章を選ぶ。

HTTP通信は単一キューの逐次処理、連絡先を含むUser-Agent、`maxlag=5`、GET、応答キャッシュを使う。エラー・429・maxlag・Retry-Afterを見て停止/待機し、別IPで回避しない。20作品の全章を取らず、初回は各作品2箇所と必要なページメタデータだけを取得する。これらはPoCの保守的な設定であり、提供元の一律req/s許可値ではない。[MediaWiki API Etiquette](https://www.mediawiki.org/wiki/API:Etiquette)。

HTTP取得後の整形は決定的なローカル処理とし、指定revisionの保存HTMLと、その本文だけを抽出したUTF-8ファイルを分ける。wikitextのテンプレート除去だけで読み順を保証しない。本文HTMLと底本の該当箇所を最初の2作品で人が照合し、その後20作品へ進める。

### PGは公式ミラーを使う

公式一覧には `https://mirror.cs.odu.edu/gutenberg/`、同 `/gutenberg-epub/`、iBiblioのrsync等が掲載されている。PGの案内では原ファイルと生成ファイルでディレクトリ構造が異なるため、RDFカタログ等の実際のファイル一覧を見て対象20ファイルだけを選び、存在確認・サイズ上限・文字コード・ヘッダーの確認を行う。20ファイルのために全コレクションを同期しない。ミラー独自の利用条件・robots・負荷方針も取得時に確認し、HTTP取得許可が確認できなければWikisource経路へ戻す。[PG Mirrors](https://www.gutenberg.org/MIRRORS.ALL)、[Mirroring How To](https://www.gutenberg.org/help/mirroring.html)。

初回取得案は1通信ずつ、初期上限1ファイル5MB・総計100MB、失敗時最大3回、途中再開できる取得manifestとする。上限は実測前の運用値であり実ファイルサイズではない。ZIP展開ではパストラバーサル・過大展開を拒否する。UTF-8がある場合は優先し、復号失敗を置換文字で隠さず保留する。PGのSTART/ENDマーカーが無いファイルは、本文範囲の自動抽出に成功したとせず確認に回す。

## 作品別の権利台帳と既存モデルへの接続

共有の`rights.py`や既存ソース一式を今回一律allowedへ変更しない。取得担当と合意して、原文抜粋用の作品・版単位の補助台帳を用意する。最低限、次を残す。

- 内部work ID、原作QID、表現/版ID、P2034またはWikisourceページ・revision ID、本文言語、原文/翻訳/改訂の区分。
- 著者・共同著者・訳者・編集者の識別子と没年、初出日、底本の刊行年・出版社・注記。値の参照元URLと確認日。
- 日本の判定、米国の判定、原典・版の判定、確認済みの地域、相互主義・戦時加算・旧法の適用または非適用理由。
- 取得、保存、外部LLM入力、商用、表示、再配布それぞれの `allowed / conditional / denied / unresolved`。外部処理先の条件が未確認なら外部LLM入力はunresolvedのまま。
- PG商標・ライセンスとの関連、CC帰属/改変表示等の義務、義務を満たした記録。本文著作権の状態と提供元の条件を別欄にする。
- 原ファイルSHA-256、抽出ファイルSHA-256、取得時刻、採用箇所、採用/保留理由、確認担当者と根拠URL。

既存の `RightsRegistry.permits()` は、条件付き用途について全obligationsが履行済みの場合だけ許可する。原文についてもこの用途別判定を通し、`external_llm` の許可が成立した場合にだけ `Evidence.llm_input_allowed=True` とする。書誌へのCC0方針をリンク先本文へ流用しない。

既存の `Evidence` へは `kind="excerpt"`、本文言語、出典URL、作品/版別rights policy ID、`locator`、必要な`expression_ref`を設定する。原文では現在の分類validatorが要求する `language == WorkInput.original_language` かつ `expression_ref=None` を満たす。表現・底本の詳しい識別は補助台帳に残し、同一workの原文と翻訳を一つの抜粋に混ぜない。翻訳では確定したexpression IDを`WorkInput.expression_id`と`Evidence.expression_ref`に設定する。原文/翻訳の適用範囲は分類結果のscopeにも記録する。

`locator` は、たとえば `rev=123;chapter=1;normalized_sha256=<hash>;chars=1200:3000` とし、原URLの現時点ページではなく固定ファイルと固定範囲へ戻れるようにする。validatorは出力のlocatorとEvidenceのlocatorの一致・引用の部分文字列一致を確認するが、台帳と原ファイルの対応やラベルの意味的支持まで証明するものではない。

## 抜粋の量・抽出・評価

初回は1作品につき2箇所、各1,200〜2,000文字、合計約2,400〜4,000文字を目安とする。20作品では本文約48,000〜80,000文字、40作品では約96,000〜160,000文字であり、token数・CLI利用量は言語と実測で見積もる。1バッチ5作品でも本文量が既存のあらすじ試行より増えるため、最初の2作品と5作品で時間・利用量を測ってから残りを投入する。

箇所選択は、文体をモデルの好みに合わせて探す方式にしない。権利確認済みの本文から、序文・献辞・PGヘッダー・目次・現代注釈を除いた最初の物語本文と、本文の約1/3付近の段落境界から固定文字数を取る。短編は重複しない2箇所が取れなければ1箇所と不足理由を記録する。詩の挿入部分だけ、会話だけ、章見出しだけが抽出されないかを確認する。

文体の判定範囲はまず「この原文の抜粋」に限定する。2箇所で見られた特徴を作品全体の恒常的文体と確定しない。抜粋間で相反する特徴がある場合は箇所・scope・conflictを保存して人手確認に回す。将来のSchemaには抜粋範囲を明示する項目の追加を検討するが、今回の機械成功率のためにunknownをpresentへ変換しない。

評価は次を別々に報告する。

1. 取得・許諾coverage: 候補20/40/50件のうち、特定原文・権利・2箇所を確保できた件数。保留理由と原言語別件数。
2. 入力品質: 本文/注釈の分離、文字化け、抽出箇所、原文/翻訳/底本の誤同定の確認件数。
3. 機械品質: Schema、ID、参照・quote/locator、CLI失敗・再試行、p50/p95、CLI観測利用量。
4. 文体評価: まずこの抜粋に対する独立人手判定との比較。unknown率、判定coverage、主要文体ラベルの支持件数とprecision/recallを分ける。作品内容全体に対する精度は詳しい読者レビューが実施された範囲だけ。

最初の20件は取得方式・判定基準の開発用とし、同じ作者・作品・翻訳を開発/校正/最終評価に跨がせない。20〜40作品では文体12ラベル全部や全言語の品質保証には足りず、校正済み確率も人手データのない範囲では出さない。

## 実施順序と受け渡し

1. 候補20作品を既存DBのwork IDと照合し、作者・原文言語・初出・提供版のメタデータを固定する。
2. 2作品の版/国/用途別台帳を確認し、Wikisource APIか許可確認済みミラーから取得する。取得原物・抽出物・locatorを照合する。
3. 抽出器のfixtureで、原文/現代訳の混同、注釈だけの抽出、許諾未解決の送信、文字化け、マーカー欠落、同一抜粋の重複を失敗テストにする。
4. 同じ経路で20作品へ拡張し、採用済みEvidenceを固定WorkInput JSONLへ加える。満たさない作品は欠損のまま残し予備候補へ切り替える。
5. rootが既存Sol CLIで2→5→20作品を分類する。本レーンから追加ライブLLMを起動しない。
6. 人手抜粋レビューと機械報告を作り、原言語の不足を見て日本語10・仏語10、必要時最大50作品へ拡張する。

実装した取得器は `src/recommend_book/ingestion/wikisource.py` と `scripts/collect_prose_poc.py`。取得原物・抽出物・台帳は `data/prose-poc/` に保持する。本文・原ファイル・権利審査を公開用リポジトリへ自動追加せず、公開成果物は商用/表示/再配布条件を満たすものだけから別に生成する。

## 初回20作品の実取得結果（2026-10-12）

`uv run python scripts/collect_prose_poc.py --output data/prose-poc --limit 20` を実行し、20作品・40抜粋、合計64,000 Unicode文字を取得した。各作品は最初の物語章と章数の約1/3付近から1,600文字ずつ採用し、ネタバレの少なさやモデルが判定しやすい箇所を選定基準にしていない。『ジキル博士とハイド氏』は番号のない章のため「Story of the Door」「The Carew Murder Case」を明示選定した。段落抽出には底本に印刷された短い章題が残る場合がある。

取得物は次の通り。

- `manifest.json`: requested=20、completed=20、excerpt_count=40、llm_input_allowed=40、errors=[]。取得器からライブLLMは起動しない。取得後の分類は下記の独立runとして実行した。
- `source-records.jsonl`: 作品ごとの著者没年・初出・採用版・原文/訳・JP/US限定条件、固定章revision、author/edition fact引用、Contributor帰属、raw/normalized SHA、変更通知。
- `work-inputs.jsonl`: 原文の文体判定に渡す入力。`kind=excerpt`、`language=en`、`expression_ref=null`、`license/license_url/attribution/inherited_restrictions` を保持する。
- `quality-checks.json`: 20作品・40抜粋のraw SHA、normalized SHA、Unicode codepoint単位char locator、入力一致、default rights台帳条件を照合した機械チェック。分類の意味的精度や人手goldを示すものではない。
- `raw/`: HTTP応答・正規化本文を内容SHAで保管し、manifestで取得日時とURLを保持する。`works/` は作品別checkpointで、停止後に完了作品を再要求せず再開できる。

対象はAusten6作、Dickens4作、Frankenstein、Alice、Moby-Dick、Dorian Gray、Treasure Island、Jekyll/Hyde、Jane Eyre、Wuthering Heights、Scarlet Letter、Kidnapped。Robinson Crusoeは原典版を確実に特定できる本文が足りないため、予備のKidnappedへ替えた。Aliceは1866年版、Dorian Grayは1891年改訂版、Frankensteinは1818年初版、Jane EyreとWuthering Heightsは初版、Scarlet Letterは1850年第2版、Kidnappedは1887年版。A Tale of Two Citiesは1859年初版のWikisourceページが欠けていたため1898年版のDickens物語本文だけを採用し、Andrew Langの序文・注を入力対象にしない。ここでの初出年は作品の連載開始や実出版年を含む整理値であり、権利判定は採用した原文版が1900年以前に存在することでも上限を確認する。正確な連載開始年・日付の独立書誌検証がすべて完了したという意味ではない。

固定候補 `data/wikipedia-frame-v2-input.jsonl` の3,154作品には、この古典20作品とタイトルが一致する作品がなかった。現在のIDは `ws:en:<採用版pageid>` の独立追加候補であり、Wikisourceに接続するQIDは作品ではなく版を指す可能性がある。`edition_wikidata_candidate` に保存し、Wikidata P629・型・原文版の接続を確認してから実work QIDへ統合する。既存固定候補へ未検証のQIDを追加したことにしない。

`wikisource-reviewed-original` をrepoの `config/sources.json` とwheel fallbackへ追加した。全用途をconditionalとし、`per_work_original_rights_checked`、`attribution`、`share_alike`、`mark_changes` の4義務を要求する。`fulfilled_for_record()` は記録されたper-work条件・書誌一致・原文Evidence・帰属条件を再検証する。policy名や `llm_input_allowed` の真偽だけから個別原作の条件履行を推測しない。原典の確認はJP/US限定であり、Contributorの創作的寄与にはCC BY-SA 4.0条件を継承する。世界一律のPD、別の現代訳、画像、ブランド/商標利用の許可へ拡張しない。

章の `oldid` はtransclusion先のPage/Templateや著者データを原子的に固定しない。このため、実際に取得したrender応答のraw SHAと本文SHAを分類根拠の正本とする。章render後に観測した依存Page revisionを監査補助として保存するが、renderと同時刻の厳密な依存snapshotを証明する値ではない。著者年表も取得したrender原物を保持する。将来再現する際は現在のURLを再描画するだけで同一本文・同一年表が得られると仮定しない。

## 原文20作品の実Sol分類（prose-style-v1）

2026-10-12、rootから分類実行を委譲され、保存済み原文を再取得せず次を実行した。追加Wikidata通信も行っていない。

```powershell
uv run recommend-book classify --input data/prose-poc/work-inputs.jsonl --output data/runs/prose-style-v1 --model gpt-6.1-sol --concurrency 2 --batch-size 5 --timeout 900 --max-run-seconds 1800 --max-observed-tokens 500000 --stop-on-rate-limit
```

`input-preflight.json` で20作品・40抜粋のSHA/locator/rights条件を再照合し、実際の `build_batch_prompt()` が `license`・`license_url`・`attribution`・`inherited_restrictions` を省略せずstdin用JSONへ直列化することを確認した。CLIは通常のresolverでnative `codex.exe` を選択し、`codex-cli 0.162.1`、medium reasoning、read-only、web disabled、tool/subagent使用禁止の既存prompt/schemaを使用した。runner/prompt/schemaは変更していない。

実行handleは `exec_command session_id=41539`、終了コード0。最終manifestのrun IDは `8245a97ec7a74fe5809f07510822dd22`、SHA-256は `cde1cd073b39b45d0b2ff3679fe59adfe739521ec3cf8d38d15ab1b8eaf4646c`。入力JSONLのSHA-256は `d60b1f2908ccb59fe1e784246c5e3e2e0950ad1f478e1e3b6b6ffcc4f22e6bf2`。

最終20completed・0failed・0pending、587.108秒、CLI5回。初回4バッチのうち3バッチが参照検証まで通り、1バッチはNorthanger Abbeyの引用先頭 `her` と原文 `Her` の大小文字不一致で失敗した。既存runner内でその5作品を1回再試行し成功したため、manifestの `attempts=25`・`retries=5` はwork単位、CLI再試行は1回である。一過性のpolling yieldでプロセスを再起動していない。全CLIイベントでtool呼出し0。

CLI観測利用量は input_tokens=157,414、output_tokens=43,500、reasoning_output_tokens=1,899、cached_input_tokens=0。API請求額や他の同時runの利用量を表す値ではない。CLI呼出し時間のp50=150.029秒、p95=226.059秒。

`mechanical-report.json` に最終結果を入力と再照合した集計を保存した。20/20結果がSchema・ID・権利付き入力参照・locator・引用文字列の契約を通過し、20/20作品で原文scopeの文体判定が得られた（31styleラベル、すべて `scope=original`、en excerpt、expression_refなし）。全軸の既知判定116件はすべてpresentであり、省略ラベルやmissing_axesはunknownのまま扱う。全軸で128根拠参照、そのうち原文部分文字列の引用97件・summaryのみ31件。使われた抜粋は全軸39/40（97.5%）、文体24/40（60%）。一つの抜粋から複数ラベルが出ることもあるため、参照数と独立抜粋数を混同しない。

これらは機械的な構造・引用所属・原文scopeの成立である。引用が原文に含まれることは、そのラベルの意味的支持を保証しない。summaryの忠実さ、31文体ラベルの人手precision/recall、作品全体への代表性は未評価。116ラベルの確率はuncalibratedであり、calibrated confidenceは全件null。仮のWikisource候補IDも実Wikidata work IDへ解決したことにはしていない。
