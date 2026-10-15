# v3 外部ジャンル・テーマ参照の追加調査

確認日2026-10-13。既存taxonomy72ラベル、既存参照、core、DB、権利configは変更していない。保存先は private `data/autonomous-next-v3/reference/external-genre/`。モデル呼び出し0。ユーザーの追加注釈作業0。

## 実数と成立範囲

CLiGS French roman19の固定commit `1b91e940475c8a7e866457a3953fed5edb0a29f1` から、原文TEI **25作品・12著者群**を取得し、rawをSHA256名で保存した。36作品の母集団から、候補著者没年1900以前という保守的な一次抽出でVerne/Zola/Malot/Rachildeを除外した。12著者全員の没年を一次資料で検証済みとは主張しない。実取得数は初期概算26から25へ訂正した。

| 状態 | 件数 | 意味 |
|---|---:|---|
| 原文TEIとnative metadataの同一ID・同一値照合 | 25 | raw取得済み。本文入力許諾とは別 |
| header/属性/章見出し/注釈/前後付を除くprivate本文preview | 25 | 固定抽出recipeのSHAと範囲を監査 |
| 底本年が未確認 | 18 | 14空欄、4件は年欄が文字列 `source` |
| 底本が保守的1900 cutoffより後 | 2 | Ponson作品2件、1910年版 |
| 底本1900以前＋原著者没年の一次証拠を確認 | 5 | registry/per-work verifierとroot UsageGuardへ渡すレビュー候補 |
| 外部LLM入力のapproved / 実行 | 0 / 0 | 5件も `llm_input_allowed=false` のまま |
| 厳密な72ラベルへのgold対応 | 0 | source codebookに必要な操作的定義がない |

底本と没年を照合した5件は、Gaboriauの `rd1155 Le crime d'Orcival`（1869年版）と `rd1150 Les amours d'une empoisonneuse`（1888年版）、Sandの `rd1282 Valvèdre`（1863年版）、Barbey d'Aurevillyの `rd0737 Un prêtre marié`（1881年版）、Villiersの `rd0721 L'Eve Future`（1886年版）。[GaboriauのBnF書誌・著者表記](https://catalogue.bnf.fr/rechercher.do?index=AUT3&numNotice=11903788&typeNotice=p)、[SandのBnF刊行物](https://editions.bnf.fr/george-sand-0)、[BarbeyのBnF書誌・著者表記](https://catalogue.bnf.fr/rechercher.do?index=AUT3&numNotice=11890143&typeNotice=p)、[VilliersのBnF著者authority](https://catalogue.bnf.fr/ark:/12148/cb11928411z)。raw HTMLも取得し、必要な没年文字列の存在を検査した。BnFの氏名表記とTEI氏名を結び、VIAFとBnF IDの同値性を捏造していない。SandのTEI VIAFは空であり、氏名のblocking keyで扱う。

25件は旧CoNSSA参照と異なるFrench corpus由来である。12群はcorpus内VIAFと氏名による候補群であり、全PoCや事前学習について未観測と証明したものではない。親工程が過去観測作者集合に対してcrosscheckする。**20〜50作品の完全に権利承認された追加benchmarkは未達**。25取得、5レビュー可能、approved0を混同しない。

## native taskと72ラベル対応

元collectionは編集者Christof Schöch / Stefanie Poppによる36小説、4つの選定ジャンル区分を提供する。取得25件の主区分は adventure6、crime-fiction9、education4、fantastic6。[固定collection README](https://github.com/cligs/textbox/blob/1b91e940475c8a7e866457a3953fed5edb0a29f1/french/roman19/readme.md)。これは今回の独立2名読了・裁定goldではなく `curated_reference`。

TEIは複数ジャンルを持つ。例えば `rd0605` のheaderはadventureとhistoricalを併記する。CSVの4区分を排他的・網羅的な正解と扱わない。[固定native keywords](https://github.com/cligs/textbox/blob/1b91e940475c8a7e866457a3953fed5edb0a29f1/french/roman19/keywords/keywords.xml) はジャンル名を列挙するが、story-level判断手順を提供せず、education/crime-fictionの文字列もそのカテゴリ列挙にはない。

`mapping-spec.json` は現72ラベルのSHA `688f8e1a0921bcbafabbbf54d99b9039bce8c525ea7e7147bdb01821b449084e` に固定し、現在は直接gold変換を0とした。adventureという同名だけから「危険な外的冒険がplotを駆動する」という対象definitionを保証しない。crime-fiction→mystery、education→growth、fantastic→fantasy/horror/SF、historical→historicalの自動gold変換も行わない。元ジャンルのexact-string正例検出を独立native task候補とし、unknown/held/未実行を計画分母から落とさない。不存在・precision/F1/accuracy・confidence/relevance校正を生成しない。元cert/importanceは原文字列を残し、confidence値へ変換しない。

## 原文権利と本文抽出

collection READMEはmetadataをCC0、付加markupをCC BY 4.0、原本文をpublic domainと区別する。原本文の法域確認は別途必要であり、PDMやmetadata CC0を本文の世界的許諾へ流用しない。レビュー5件は、単独原著者、原文French、底本・初出1900以前、現代翻訳・新たな本文寄与者なしという固定TEI情報を確認した。日本は旧50年と保守的12年の戦時加算を用い、没年・初出・底本の最大年+62が2018以前に終了する確認に限定する。米国は保守的底本年+95の終了を用いる。世界全域でのPDは主張しない。[文化庁の延長と非復活の説明](https://www.bunka.go.jp/seisaku/chosakuken/hokaisei/kantaiheiyo_chosakuken/1411890.html)、[JASRAC戦時加算資料](https://www.jasrac.or.jp/aboutus/wartime/pdf/wartime.pdf)、[US Copyright Office Circular38B](https://www.copyright.gov/circs/circ38b.pdf)。各raw一次資料のSHA、original body/markup/metadataのscopeを `per-work-rights-review.jsonl` に保持した。

抽出は `extract_inputs.extract_body`。DOCTYPE/ENTITYを拒否、唯一の `text/body` だけを読み、teiHeader/front/back/属性、head/note/castList/stage/figure/table/annotation/書誌、preface等editorial divを除外する。本文の文芸表現として偶然存在するジャンル語を削除して内容を改変することはしない。title/authorやnative metadataは分類入力に渡さない。6000文字の冒頭・中間・末尾の固定範囲はラベルに依存せず選ぶ。raw・全抽出本文・各範囲をSHAとlocatorで結ぶ。sourceとlicense、改変表示をsidecarと提案入力へ保持する。[CC BY 4.0条件](https://creativecommons.org/licenses/by/4.0/)。

TDDは抽出module未存在による失敗を先に確認し、header/注釈/前後付/属性漏洩、外部実体、body不存在、空本文の4テストが成功した。25実本文の非空抽出を確認し、5件の提案入力も権利許諾flagをfalseに維持した。全データはprivate。公開fixture・DBには入れていない。

## Underwood / LTOの追加確認

Underwood固定Dataverse v1 / GitHub v0.2は **984巻・author/titleペア938**。CC0はmetadataに適用し、原小説本文に適用しない。元のデータ辞書はLoC science-fiction/ghost/horror/detective分類、批評書Gothic、detective展示書誌など由来を分ける。random/teamred/teamblack/stew等は内容ジャンル正解ではない。巻・短編・短編集を小説数へ単純加算しない。[公式データ辞書](https://github.com/tedunderwood/fiction/blob/v0.2/meta/datadictionary.csv)、[公式収集手順](https://github.com/tedunderwood/fiction/blob/v0.2/gather_data.md)、[公式Dataverse](https://doi.org/10.7910/DVN/XKQOQM)。native定義・control除外・metadata SHAは `underwood-native-audit.json` に保存。原本文取得0。

Project Gutenbergの現行policyは通常websiteへの自動accessを制限し、harvest・catalog等の例外を記す。本文を通常websiteから機械的に25件まとめて取得する処理は実行していない。[公式robot policy](https://www.gutenberg.org/policy/robot_access.html)、[公式mirror手順](https://www.gutenberg.org/help/mirroring.html)。USでのPDと非US法域確認を分け、copyrighted許諾作品の権利まで転用しない。[公式license](https://www.gutenberg.org/policy/license.html)。これらの最新policy rawも保存した。

LTO v2025.04 archiveを再集計すると `novel:` 見出しは **23件＝近代22件＋古代A True Story (180)**。Golden Assの章を別小説に数えていない。22という既存記述は近代作品数として注記が必要。archive内には本文がなくtheme metadataと説明がある。Wikipedia由来descriptionはCC BY-SA4例外なので入力から除外し、theme部分のMITを説明文や原小説本文へ拡張しない。[LTO公式release](https://zenodo.org/records/15210539)、[公式license例外](https://github.com/theme-ontology/theming/blob/master/LICENSE-EXCEPTION.md)。`lto-native-candidates-v2.jsonl` に正しく区切った23候補を保存した。最初のparser出力は後続の非小説見出しを正しく切らず誤ったtitleを混ぜたため、不採用として `lto-parser-audit.json` に理由を保持し、利用対象をv2に明示した。本文候補の権利・底本確認は未完了。

取得は公式raw GitHubへ逐次・2秒間隔。rawの同SHA既存ファイルはハッシュが一致する場合のみ再利用し、候補・manifest・previewの既存snapshotを上書きしない。Git commit/push、公開、有料処理、連絡送信は実行していない。
