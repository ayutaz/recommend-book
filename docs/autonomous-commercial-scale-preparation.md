# 商用化の素材条件と拡張時の資源見積もり

> **文書の適用範囲（2026-10-26）**: 本書は[文書台帳](documentation-index.md)の「履歴・結果・計画・スナップショット」です。本文の件数・計画・実行状態と、本文内の「現在」「最新」「実行中」は記録日時のものとして扱い、現在の状態は[current-status.md](current-status.md)を参照してください。

2026-10-26案内: 以下の2026-10-20案内、phase15、v16への参照は各記録日時の作業入口です。現行入口は[current-status.md](current-status.md)で、検証済み実装は`5dd`、両OS CIは成功、PR #33はdraft、worldは54ワーカー稼働・取得停止・資料準備144・native新規20件のみ採用です。

2026-10-20案内: 以下の素材確認と処理量は当時の記録。現在の[限定処理と実測](phase15-bounded-poc-and-delivery-v1.md)、[規模拡張の判定](scaling-readiness-v1.md)、[実行計画v16](autonomous-next-phase-plan-v16.md)を優先する。代表的な全工程処理量・無料残枠は未確定で、古い見積もりを1万〜10万作品の納期や商用利用許可へ転用しない。

確認日: 2026-10-13。対象はローカルPoCの素材・取得基盤・分類処理。全素材の商用利用や全世界の本文権利が確定したという報告ではない。新しい契約・問い合わせ・追加入力をユーザー作業として要求せず、条件不明の素材は使用範囲を制限する。

## 用途別の確認と採用方針

| ソース | 一次資料で確認した条件 | このPoCと商用化準備での扱い |
|---|---|---|
| Wikidata | 構造化データはCC0。リンク先の文章・画像は別の権利 | 書誌・IDの中核。リンク先本文にCC0を引き継がない |
| Open Library | IAは追加の権利を主張しないが、投稿・法域の既存権利を留保 | 短い書誌/subjectは現研究policyで処理。descriptionはLLM禁止を維持。商用再配布は未確定のまま |
| NDLサーチ | providerと用途ごとに条件が違い、metadataとコンテンツは別。CC適用metadataはその条件に従う | 許可providerと項目だけを採用し帰属/変更表示を保持。要約/本文を同じ条件で一括採用しない |
| Wikipedia | 文の再利用は帰属、license、変更表示、share-alike等の条件を持つ | 固定revision、作者履歴、抽出変更、licenseとscreeningを保持。引用/輸入文等を除外。ラベル出力全体の権利は別判定 |
| 原文・外部benchmark | metadataの自由licenseと原典・版の権利は別 | 国・原作・版・翻訳・校訂の範囲を確認できた素材だけを分類入力へ。世界共通PDと断言しない |

根拠: [Wikidata Licensing](https://www.wikidata.org/wiki/Wikidata:Licensing)、[Open Library Licensing](https://openlibrary.org/developers/licensing)、[NDL provider条件](https://ndlsearch.ndl.go.jp/help/api/provider)、[NDL利用案内](https://ndlsearch.ndl.go.jp/help/api)、[Wikimedia Terms of Use](https://foundation.wikimedia.org/wiki/Policy:Terms_of_Use)。外部benchmarkの作品別審査は担当の実行記録に保存する。

NDLは継続API利用について連絡先等の案内を求めている。現在の保存済み素材の検証と、大量の継続運用は分ける。申請が必要なproviderや未完了の継続運用手続きを、手続き不要として商用データ経路へ組み込まない。

Open LibraryのAPIは大量取得や高トラフィック商用基盤を想定せず、bulkには月次dumpを案内する。1万件以上の基盤は既存dumpの範囲抽出とキャッシュを基本とし、1作品ずつAPIを大量発行しない。[公式API方針](https://openlibrary.org/developers/api)

## 機械台帳と未確定の素材

`data/autonomous-poc-v1/delivery-preparation/rights-policy-inventory.json`は現policyと、最新2,020入力での参照数を固定した。Wikidata5,815、Wikipedia792、Open Library217、Wikisource40のEvidenceがある。これは作品数ではなくEvidence数で、本文や生成ラベルの商用権利確認件数ではない。

policy-levelの `allowed/conditional/denied/unresolved` と、個別素材の条件履行を分ける。新しい補充資料はraw SHA、版・revision、帰属、license URL、抽出変更、用途別の条件を追加台帳へ残す。PoCに入力できたことを、原文を一般向けサービスで転載できる証明にしない。個人の335評価・嗜好分析はローカルのprivate成果物に保持し、素材公開へ含めない。

最終DBの独立監査では、旧Wikipedia根拠792件に現policyの第4条件と履行配列の記録がなく、Wikisource40件にも履行配列がないことを確認した。元記録・条件・原runとの一致は確認できるが、現在の用途別許諾の全面的な再承認ではない。旧WP792の分類origin内訳は637/148/7で、最後の7件は新79差分runでも使われている。新たに補充した81件の4条件履行とは分ける。7件を個別に事後審査し、外部LLM・商用・表示・再配布を保留、原物は既存保存として保持すると別台帳に確定した。全7の実label参照は0で、モデルへの影響が0という意味ではない。この不足を事後に実行前確認済みへ書き換えない。残るWP785/WS40の個別再審査は未実施で、全外部入力の現行許諾確認率100%や、商用再配布可能率100%とは報告しない。

商用配布候補は、条件が成立した書誌/ラベル/必要な表示情報だけを用途に応じて抽出する。未成立の説明文・本文・表紙は除外する。Open Libraryを含む全素材を一括してCC0や商用許諾済みとした配布packageは作らない。public corpusへの出力審査と一般公開サービスは次段階であり、今回のローカル納品には含めない。

## 1万・3万・10万作品への見積もり

旧268作品の書誌差分分類は最大8並列・batch5、実時間1,027秒、入力1,572,857/output206,213 tokensだった。同じ入力密度・並列度で単純に比例させると次の値になる。あらすじ/本文入力、取得、権利・同定審査、利用枠待ち、再試行、イベントログ、full dumpの保存量を含まない。

| 実作品の目標件数 | 同じ書誌分類負荷での計算時間 | 現書誌DB密度でのサイズ |
|---|---:|---:|
| 10,000 | 約10.6時間 | 約454 MiB |
| 30,000 | 約31.9時間 | 約1,363 MiB |
| 100,000 | 約106.4時間 | 約4,542 MiB |

基準DBは2,020作品で96,210,944 bytes。分類とrawを含む全保存量の見積もりではない。`capacity-baseline.json`には入力サイズ・usage・計算式の適用除外を保存し、元runと入力のSHAは`preparation-source-audit.json`で結び付けた。APIの価格へ換算して請求額を断定していない。

本文抜粋を使った別負荷も測定した。外部pilot20はSpanish本文3×6,000文字/作品、合計360,000文字、並列2/batch5、CLI4呼出しで20 completed・failed/pending0、599.270秒。usageはinput213,797/output41,467 tokensで、実terminal proofとinput/results/contextのSHAを照合した。

この**同じ並列2・抜粋処理だけ**を線形外挿すると1万件83.23時間、3万件249.70時間、10万件832.32時間になる。書誌入力の8並列見積もりと条件が違い、8並列へ増やした速度は未測定である。スペイン語小説20件の実績で全世界へ一般化せず、取得・権利/版審査・他評価・利用枠・失敗時retry・保管を含まない。`capacity-long-input-pilot-v1.json` にsource SHA・usage・除外条件を保存した。価格、利用枠の保証、品質の保証には換算しない。

2026-10-13の単回probeでは、旧推薦v3の2,020候補＋既存335参照の読み込み0.340秒、personal全候補の採点1.832秒、supported696、表示100だった。元3入力の前後SHA一致を確認し、個人の個別行はログへ出していない。新しい推薦方式や補充後の性能ではなく、10万件の実測でもない。`recommendation-runtime-probe.json`に保存した。

1万〜3万件では、入力/結果の単位を作品ID＋入力SHA＋model/schema/prompt版に固定し、取得更新と分類を差分にする。原物とjobイベントは別保存し、DBを分類workerが同時書込みしない。言語別の根拠充足率とunknown率も件数に並べる。

10万件では、候補生成用に疎な分類特徴の逆引きやベクトル検索を比較し、最終順位・既読除外・理由表示は検証済み元資料へ戻れるようにする。現personalの全候補×全参照計算は参照数にも依存するので、単純比例値をSLAとして使わない。新しい検索依存は現PoCへ導入せず、導入時は品質と資源の比較実験を別versionにする。

1万/3万/10万の実コーパスはまだ収集していない。合成性能試験を追加する場合も、合成件数と実収集・名寄せ済みの作品件数を別に記録する。
