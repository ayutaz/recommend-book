# 自動PoCの進捗と完了証拠

2026-10-13時点。[最新計画](autonomous-completion-plan-v2.md)の6段階を進行中であり、全工程の完了報告ではない。追加の本人評価・試読・採点・履歴入力・画面確認は0件。既存335件の本人評価を再利用し、AI判断を本人評価や人手正解として登録しない。

## 現在の証拠

| 段階 | 確認済み | 完了までの残作業 |
|---|---|---|
| A 固定 | 評価契約とbaseline16資料を固定。統合担当が16件の現SHAを再照合 | 最終DB・入力・結果・納品manifestへの集約 |
| B 外部注釈 | 権利・版・入力が成立した20作品の実分類終了。20 completed、failed/pending0。raw4batchのfinal・event・job・durable・indexを独立に照合 | 成立した60作品への展開、別schemaのnative20評価、最終集計 |
| C 補充・名寄せ | 特徴なし1,026作品の補充調査。79作品/81Evidence採用、947作品保留。新書誌DB構築と79差分入力固定まで完了。NDL役割監査26件中16件にauthor/translator曖昧性。全26未解決で既存2020へ投影0 | 差分結果の新DB反映、scope隔離、名寄せ400ペア＋26件の全件終了とstrict audit |
| D 分類評価 | 固定400作品のblind二重審査を実行中。pilot20は両lane成功、第三審査を含む工程を実装 | 両lane400終了、第三blind裁定、変更入力だけの差分分類、最終監査 |
| E 推薦 | 既存335件のnested grouped CV終了。8設定を事前固定し、11成果物/コードSHAを再照合 | 最終world DBからHTML/JSON生成と実画面確認 |
| F 納品 | 権利範囲、拡張資源の外挿、再現CLI、独立監査を準備 | 全工程terminal、最終全件監査・テスト・画面検証・報告・コミット |

同定が成立した本文は現時点で60作品であり、目標100件の達成とは数えない。条件不明の資料を通して件数を増やさない。名寄せのAI合意をauthority mergeへ反映しない。

全2,020 canonicalのうち、active Wikidata原物を持つ2,008件のP31を再検査し、版classが作品扱いになっていた15件を発見した。残る非WD12件はこのclass検査の対象外として保持する。統合担当もSQLと原物から全15件を独立に再検出し、出典/version/canonical/claims/SHAを照合した。最終DB・推薦では15件をscope矛盾の保留へ隔離し、2,020件の歴史分類と2,005件の推薦対象を分ける。既存rawと分類結果を消して数を合わせない。

## 評価で分かったこと

外部pilot20の主人公4ラベルの正例は23件、検出7件、入力が成立した範囲の検出率30.43%。固定180作品の全200正例を分母にすると3.5%、入力coverageは20/180。常時present方式は同じ入力で23/23となるため、検出率だけでは精度の合格を示せない。これは抜粋からの限定検出であり、作品全体のaccuracy・precision・F1・校正ではない。別native taskの結果も、この現taxonomy出力から作った値として代用しない。

本人の既存評価では、内側CVで選ぶ手続きの外側nDCG@10は0.573995、人気順0.633978。人気順を上回る証拠は得られていない。全335件の内側CVが選んだhybrid_50は実験候補であり、外側の値で再選定していない。世界作品には比較可能な人気指標がないため、納品では根拠付きpersonalの参考順位へ戻す。未知の読書状態を未読に変換しない。

出典: `data/autonomous-poc-v1/reference/pilot20-positive-report.json`、`data/autonomous-poc-v1/independent-audit/reference-pilot20-v1.json`、[推薦評価](autonomous-recommendation-evaluation.md)。新しい本人評価は求めず、worldの実効推薦精度は未検証として報告する。

## 修正・実行記録

DB構築の初回は、元runの大きなmetadataを作品ごとに再parseしてメモリが増える問題を実測した。担当のbuilderだけを意図的に停止し、exit1・出力未公開・baseline16不変を保存。run単位の共有cacheへ修正し、30作品/2MB metadataの反例はpeak約120MBのREDから16MB未満のGREENへ変わった。関連123テスト成功後に新attemptを開始した。停止した初回を成功扱いしない。

推薦納品APIは、呼出し側がbooleanだけでworld人気指標の確認を主張する経路を拒否した。反例RED後に修正し、納品6件＋推薦評価17件の23テスト成功。最終DBによる生成・全体テスト・ブラウザ確認はこれから行う。

scope隔離済みloaderへ納品経路を接続し、歴史poolが推薦に混入する反例REDから修正。納品7件＋推薦評価17件の24テスト成功。今後のWikidata取得でも、work指定に非deprecatedの版class/P629があれば原物付きholdとする再発防止を追加し、関連24テスト成功。既存のkind=edition取得は保持する。

元分類のcore10、旧入力・結果・人手packを保持している。CLIは最大8並列の予約を分担し、統合担当の差分分類は2枠が解放されてから起動する。CLI versionは実測の `codex-cli 0.162.1` をそのまま使用する。利用量はCLIイベントの観測で、請求額ではない。

[商用化・拡張準備](autonomous-commercial-scale-preparation.md)は用途別条件と線形見積もりを示す。全素材の商用許諾、全世界の本文権利、実10万作品の収集・性能を確認済みとはしない。GitHub hosted CIは未実行。データ・個人評価はローカルprivate成果物として保存し、push・公開は行っていない。
