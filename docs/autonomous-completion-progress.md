# 自動PoCの進捗と完了証拠

2026-10-13時点。[最新計画](autonomous-completion-plan-v2.md)の6段階を進行中であり、全工程の完了報告ではない。追加の本人評価・試読・採点・履歴入力・画面確認は0件。既存335件の本人評価を再利用し、AI判断を本人評価や人手正解として登録しない。

## 現在の証拠

| 段階 | 確認済み | 完了までの残作業 |
|---|---|---|
| A 固定 | 評価契約とbaseline16資料を固定。統合担当が16件の現SHAを再照合 | 最終DB・入力・結果・納品manifestへの集約 |
| B 外部注釈 | 実成立60作品の現taxonomy分類は55 completed/5 held。別schema native20は20 completed。両実handle exit0、元20再利用を含むraw・cache・event・durable・indexとnative source前後SHAを独立照合 | 限定評価結果を最終manifestへ集約 |
| C 補充・名寄せ | 特徴なし1,026作品の補充調査。79作品/81Evidence採用、947作品保留。新書誌DB構築と79差分入力固定まで完了。NDL役割監査26件中16件にauthor/translator曖昧性。全26未解決で既存2020へ投影0 | 差分結果の新DB反映、scope隔離、名寄せ400ペア＋26件の全件終了とstrict audit |
| D 分類評価 | 固定400作品の両blind laneが400 completed/failedpending0。不一致120作品の第三blindを実行中。79変更入力の分類も統合担当が開始 | 第三blind終了・裁定、79差分分類のterminal、最終監査 |
| E 推薦 | 既存335件のnested grouped CV終了。8設定を事前固定し、11成果物/コードSHAを再照合 | 最終world DBからHTML/JSON生成と実画面確認 |
| F 納品 | 権利範囲、拡張資源の外挿、再現CLI、独立監査を準備 | 全工程terminal、最終全件監査・テスト・画面検証・報告・コミット |

同定が成立した本文は現時点で60作品であり、目標100件の達成とは数えない。条件不明の資料を通して件数を増やさない。名寄せのAI合意をauthority mergeへ反映しない。

全2,020 canonicalのうち、active Wikidata原物を持つ2,008件のP31を再検査し、版classが作品扱いになっていた15件を発見した。残る非WD12件はこのclass検査の対象外として保持する。統合担当もSQLと原物から全15件を独立に再検出し、出典/version/canonical/claims/SHAを照合した。最終DB・推薦では15件をscope矛盾の保留へ隔離し、2,020件の歴史分類と2,005件の推薦対象を分ける。既存rawと分類結果を消して数を合わせない。

## 評価で分かったこと

外部pilot20の主人公4ラベルの正例は23件、検出7件、入力が成立した範囲の検出率30.43%。固定180作品の全200正例を分母にすると3.5%、入力coverageは20/180。常時present方式は同じ入力で23/23となるため、検出率だけでは精度の合格を示せない。これは抜粋からの限定検出であり、作品全体のaccuracy・precision・F1・校正ではない。別native taskの結果も、この現taxonomy出力から作った値として代用しない。

外部60では24/65正例検出（36.92%）、全200分母では24/200（12%）、入力coverage60/180。held5を分母から除いていない。専用native20の直接4正例対応は0/23、別掲raw native age/profession40項目は2/40（age0/20・profession2/20）。語義統合せず、部分抜粋・文字列一致に限定した検出結果であり、accuracy・precision・F1・校正や世界全6軸の精度ではない。

本人の既存評価では、内側CVで選ぶ手続きの外側nDCG@10は0.573995、人気順0.633978。人気順を上回る証拠は得られていない。全335件の内側CVが選んだhybrid_50は実験候補であり、外側の値で再選定していない。世界作品には比較可能な人気指標がないため、納品では根拠付きpersonalの参考順位へ戻す。未知の読書状態を未読に変換しない。

出典: `data/autonomous-poc-v1/reference/pilot20-positive-report.json`、`data/autonomous-poc-v1/independent-audit/reference-pilot20-v1.json`、[推薦評価](autonomous-recommendation-evaluation.md)。新しい本人評価は求めず、worldの実効推薦精度は未検証として報告する。

## 修正・実行記録

DB構築の初回は、元runの大きなmetadataを作品ごとに再parseしてメモリが増える問題を実測した。担当のbuilderだけを意図的に停止し、exit1・出力未公開・baseline16不変を保存。run単位の共有cacheへ修正し、30作品/2MB metadataの反例はpeak約120MBのREDから16MB未満のGREENへ変わった。関連123テスト成功後に新attemptを開始した。停止した初回を成功扱いしない。

推薦納品APIは、呼出し側がbooleanだけでworld人気指標の確認を主張する経路を拒否した。反例RED後に修正し、納品6件＋推薦評価17件の23テスト成功。最終DBによる生成・全体テスト・ブラウザ確認はこれから行う。

scope隔離済みloaderへ納品経路を接続し、歴史poolが推薦に混入する反例REDから修正。納品7件＋推薦評価17件の24テスト成功。今後のWikidata取得でも、work指定に非deprecatedの版class/P629があれば原物付きholdとする再発防止を追加し、関連24テスト成功。既存のkind=edition取得は保持する。

全体テストの同session観測はexit0、1,123 passed / 90.39秒。`data/autonomous-poc-v1/independent-audit/full-tests-observation-v1.json` に記録した。一部の別監査コードは並行作業中であり、実行前source snapshotを捕捉していないため、全実装固定後の最終チェックは別途行う。外部pilot20のraw4batchは再利用できるread-only監査CLIでも全件照合し、原物不変・新LLM0を確認した。

79差分の新execution contextは10 core raw copy・入力・兄弟manifest・並列2/batch5等のcontrolsを実行前に固定した。新run namespaceが存在せずcache移行0であることを `delta-classification-preparation/migration-noop-v1.json` に保存した。これは準備であり、モデルを実行済みとはしない。

名寄せv3は350/426 finalizedの後、sameとcontradictionsが同居する1出力をvalidatorが拒否し、native session38383の実exit1を観測した。原runと73 CLI応答は保持し、350を426成功と表示しない。未finalized76のみ別namespaceで回復し、partial350はraw/原物/strict監査の来歴を保持して集約する。新しい同一関係のauthority採用は0。

第三分類の固定400 cohortにはscope矛盾15件中3件を含む。標本を事後に変更せず歴史的な安定性実験として記載し、最終推薦対象の15除外とは分ける。

外部60とnative20は独立raw監査でも終了証拠・実イベント・durableを全件照合した。60のraw20呼出しには元pilot4と新phase16を含むため、元20作品を新規実行として二重計数しない。nativeは4呼出し。原stdin bytes/実argvの外部捕捉がない範囲は、そのまま未証明とする。

元分類のcore10、旧入力・結果・人手packを保持している。CLIは最大8並列の予約を分担し、統合担当の差分分類は2枠が解放されてから起動する。CLI versionは実測の `codex-cli 0.162.1` をそのまま使用する。利用量はCLIイベントの観測で、請求額ではない。

[商用化・拡張準備](autonomous-commercial-scale-preparation.md)は用途別条件と線形見積もりを示す。全素材の商用許諾、全世界の本文権利、実10万作品の収集・性能を確認済みとはしない。GitHub hosted CIは未実行。データ・個人評価はローカルprivate成果物として保存し、push・公開は行っていない。
