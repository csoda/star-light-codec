# Star Light Codec

Star Light Codec は、Star Light プロジェクトから切り出した実験的な
exact byte compression の公開サンプルです。

任意のバイト列を `SLB1` という小さな自己説明型アーティファクトにまとめ、
デコーダー側でサイズ・SHA-256 digest・変換手順を検証してから、元のバイト列を
完全に復元します。

これは動画や音楽を再生するための codec pack ではありません。Astro Starlight とも
無関係です。最初の公開範囲は、圧縮方式そのものよりも「安全に復元できる
アーティファクト形式」と「読みやすいエンコーダー/デコーダー例」を示すことに
絞っています。

## できること

- 任意ファイルの完全復元
- `SLB1` binary artifact container の作成と読み取り
- 最大4回までの bounded gzip transform
- payload と元入力の SHA-256 検証
- 全体サイズが大きくなる場合は `keep-original-for-storage` を推奨
- Star Light 本体の `starlight-byte-exact` 互換プロファイル

## 使い方

```powershell
python -m pip install -e .[test]
python -m starlight_codec encode README.md README.slb1 --max-passes 2
python -m starlight_codec inspect README.slb1
python -m starlight_codec decode README.slb1 README.roundtrip.md
pytest
```

エンコード結果は `SLB1` アーティファクトとして保存されます。デコードすると元の
バイト列が完全に復元されます。CLI の出力はメタデータだけで、payload 本体は表示
しません。

## 今後

ロードマップは [docs/roadmap.md](docs/roadmap.md) にあります。

今後は、より賢い encoder planning、chunking、dictionary、domain-specific codec、
そして圧縮とは分離した authenticated sealing / encryption track を追加していく予定です。

## 名前について

公開名は **Star Light Codec** です。2026-07-07 時点の簡易調査では、
`StarCodec`、`Stable Codec`、各種 `Starlight` 関連プロジェクトなど近い名前は
見つかりましたが、`Star Light Codec` そのものの明確な既存プロダクト衝突は
見つかりませんでした。ただし、これは法的な商標調査ではありません。

## ライセンス

Star Light と同じ方針です。

- コード、スクリプト、テスト: Apache-2.0
- ドキュメント、仕様、ロードマップ: CC BY 4.0
- 小さなサンプル、fixture、再利用向けメタデータ: CC0-1.0

詳細は [LICENSING.md](LICENSING.md) を参照してください。
