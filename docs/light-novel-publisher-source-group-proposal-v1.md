# NDLと未接続のＭノベルス掲載グループを保存する設計

出版社の掲載グループを新しい `publisher_group` 型で保存し、検索・詳細・お気に入り・読書状態に通す。実装`fa6e511`で、190群中NDL接続済み76群を除く114群・349商品参照を実DBへ採用した。既存ID・証拠・履歴は保持した。以下は保存・公開の仕様と確認項目であり、実データの検証結果と残作業は[進捗](light-novels-progress-v1.md)へ記録する。

## IDと更新

`lng:pub:futaba:<32桁の小文字hex>` を**ローカル掲載グループID**とする。初回保存時に一度だけ生成・保存し、公式の恒久グループID、作品ID、物語シリーズIDとは呼ばない。`entry_kind=publisher_group`、`qid=null`。NDL IDやQIDを合成せず、既 `lns:ndl:` / `lne:ndl:` / `lns:wd:` は変更しない。

[現source verifier](../src/recommend_book/light_novel_futaba_workgroups_v1.py)の `reference_id=futaba-grouping-local:<sha256>` は、hash対象に `source_snapshot_sha256` を含む。これは**特定snapshot内の出版社フィールド主張**であり、entry IDとして使うとsnapshot更新だけで重複entryと履歴断絶が起きる。

新recordは `schema_version=light-novel-futaba-source-group-v1` とし、local ID、active assertionのmanifest pin・reference ID・完全な `local_reference_key`、過去に採用したassertion pinを保持する。過去pinは監査用に残し、表示はactive assertionを既verifierで再生する。全過去rawの毎回再走査は追加しない。別tableや汎用identity frameworkは作らない。

DB再構築時も保存済みrecordのlocal IDを再利用する。history export単体からgroup IDを再生成できるとは扱わず、recordとassertion対応を一緒に保全する。

同一snapshotの再取込はassertion reference一致でidempotent。次snapshotは「provider・source scope・frame・label ID・grouping文字列の完全一致」「変更前後で少なくとも1個の同一出版社product referenceが残る」「対応先local entryが一意」の場合だけ同じlocal IDへassertionを追加する。題名の省略・巻削除・著者推測はしない。改名・分割・統合・member総入替・複数対応は保留し、旧recordと履歴を保持する。これは掲載グループの継続規則であり、物語同一性の証明ではない。

## 保存・表示の境界

既anchor経路で保存したＭ群のsource assertionを先にmetadataで照合し、既存entryに対応する群は追加しない。未接続114群だけが新IDの対象。将来NDL対応が増えても新IDをNDL IDへ変更せず、個別に検証した書誌リンクを詳細へ追加する。既anchor側のentry・proof・読書状態を移動・削除・上書きしない。

projectionは既 `verify_futaba_mnovels_workgroups(manifest, sha)` の正式raw再生をrequest内で1回だけ共有し、対象referenceの完全なsource key・member setと照合する。保存済みJSONは候補選択用、公開値は再生結果から生成する。NDLanchor・架空の巻番号・共通著者・紙電子の同一版を補わない。

詳細には出版社の完全grouping文字列、公式一覧URL、保存frame内の掲載product参照、既に独立検証済みの書誌リンクを表示する。product参照は版や作品のmemberに昇格させず、`edition_series_memberships` に保存しない。`story_series_identity_asserted=false`、`original_work_identity_asserted=false`、`light_novel_confirmed=false`、全媒体・全期間の網羅性はunknownを維持する。統計では作品/物語シリーズと別に掲載グループを数える。

## 実装範囲

|ファイル|限定変更|
|---|---|
|`src/recommend_book/light_novel_catalogue_v1.py`|`import_futaba_source_groups`で初回登録と上記の限定更新。新ID、M専用schema、完全grouping文字列と明示著者役割による検索、独立商品参照、別統計を追加。物語seriesへunionせず、内容特徴を継承しない。|
|`src/recommend_book/light_novel_site_backend_v1.py`|`_LIGHT` / `_KINDS` と履歴prefix推定へ `lng → publisher_group`。world QIDとの混同を拒否。|
|`src/recommend_book/recommendation/scale_site_v1.py`|`_ENTRY_ID` / kind enum / API経路 / preference検証 / JS favorites regex / 検索filterを同じ新型へ拡張。公開allowlistにM掲載group専用scope・member数・公式URLだけを追加し、raw path/proof本文は非公開。|
|既 `test_light_novel_catalogue_v1.py` / `test_light_novel_site_backend_v1.py` / `test_light_novel_site_v1.py`|下のケースを既合成source fixtureで確認。source verifierそのものは変更しない。|

お気に入りと履歴は掲載グループに対する状態として保存する。新kindの履歴が含まれるexportだけ `catalogue-history-export-v3` とし、`entry_kind=publisher_group` を明示する。旧IDだけのexportはv2のcanonical payloadとSHAを保持し、削除は現在exportのSHAを再照合する既方式を維持する。新kindを理解しない旧readerへの変換はしない。推薦入力としてIDを受理しても、内容特徴・genreのないgroupから特徴を推測せず、候補group自体も推薦対象にしない。既挙動どおり利用可能な根拠がなければ `insufficient_evidence` を返す。

## 確認項目

1. NDL版・QIDが0件の正式M raw fixtureからgroupを保存し、完全題検索→詳細→お気に入り指定→読書状態→history exportで同じtyped IDを保持する。
2. malformed ID、偽NDL/QID、未知provider、publisher_groupとseries/editionのkind偽装を保存/API双方で拒否する。
3. snapshot/receipt/raw/SHAまたは保存JSONを改変すると公開・追加保存を拒否し、途中までのmemberやrecordを残さない。
4. 同一snapshot再取込は新entry0。許可された次snapshot更新でも同一local IDと既読状態を保ち、旧assertion pinを残す。
5. 改名・split/merge・member総入替・複数対応を自動継承せず保留し、旧entryと履歴を保持する。
6. 既M anchor entryに対応するassertionを再取込しても重複0、旧typed ID・proof・read_statusの変更0。
7. group題名/掲載scope/件数だけを公開し、raw path・snapshot内部path・prose・仮著者/ISBN/巻番号が出ない。紙電子productを同一版へまとめず、global completenessをtrueにしない。
8. groupをlike/readしても既物語seriesへのfamily統合、native feature継承、LN確定、他版の既読化を起こさない。group推薦候補0、根拠不足の結果を保持する。

9. 正式検証中に別connectionから同じ群が登録された場合、書込みtransaction内の対象集合・kind・保存SHAの再照合で検出し、新しいUUIDによる二重登録を防ぐ。根拠不足の類似作品照会は無関係な全件走査の前に終了する。

既知114群の取込みは完了したが、掲載グループを物語シリーズの同定数へ加算しない。全国のほぼ網羅と独立した推薦品質は未達・未評価である。
