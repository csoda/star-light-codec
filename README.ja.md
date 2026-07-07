# Star Light Codec

Star Light Codec は、Star Light プロジェクトから切り出した実験的な
exact byte artifact codec です。

任意のバイト列を `SLB1` という自己説明型アーティファクトにまとめ、
デコーダー側でサイズ、SHA-256 digest、変換手順を検証してから、元のバイト列を
完全に復元します。ファイル種別を知らなくても exact round-trip できることが、
この方式の一番強いところです。

これは動画や音楽を再生するための codec pack ではありません。Astro Starlight とも
無関係です。最初の公開範囲は、強い圧縮アルゴリズムそのものではなく、
「安全に復元できるアーティファクト形式」と「encoder を進化させても decoder を
単純に保てる契約」を示すことに絞っています。

## できること

- 任意ファイルの完全復元
- `SLB1` binary artifact container の作成と読み取り
- 最大4回までの bounded gzip transform
- payload と元入力の SHA-256 検証
- 全体サイズが大きくなる場合は `keep-original-for-storage` を推奨
- Star Light 本体の `starlight-byte-exact` 互換プロファイル
- encoder/decoder 分離による、将来の圧縮方式差し替え余地

## どう強いか

- **任意バイト列を対象にできる:** text、JSON、ログ、バイナリ、生成物、未知の
  ファイル形式を同じ exact-byte interface で扱えます。
- **完全復元を検証する:** 元サイズ、payload サイズ、payload digest、最終的な
  input digest を持ち、復元結果が本当に元データと一致するか確認します。
- **decoder が単純:** header を読み、長さと digest を検証し、allowlist された
  transform を逆順に適用するだけです。
- **encoder を育てられる:** chunking、dictionary、residual、domain-specific codec
  などを追加しても、exact round-trip の契約を保てます。
- **圧縮できない時に盛らない:** payload だけでなく artifact 全体が元データより
  小さいかを見て、保存採用すべきかをメタデータで返します。

## 技術形状

`SLB1` は以下の binary layout を持ちます。

```text
magic          4 bytes   ASCII "SLB1"
headerLength   4 bytes   little-endian uint32
payloadLength  8 bytes   little-endian uint64
header         N bytes   UTF-8 compact JSON
payload        M bytes   raw transformed payload bytes
```

header には `schemaVersion: 2`、`packageKind: starlight-byte-exact`、
`artifactContainer: slb1`、`strategy`、`transforms`、`inputDigest`、
`payloadDigest` などが入ります。

payload は JSON 内に埋め込まず、header の後ろに raw bytes として置きます。
これにより base64 expansion を避けつつ、metadata は人間が inspect できる形を
保ちます。

## 使い方

```powershell
python -m pip install -e .[test]
python -m starlight_codec encode README.md README.slb1 --max-passes 2
python -m starlight_codec inspect README.slb1
python -m starlight_codec decode README.slb1 README.roundtrip.md
python -m starlight_codec capsule README.md README.slb1 README.capsule.json --tag docs
python -m starlight_codec hydrate README.capsule.json README.chunk.md --chunk c0001
pytest
```

エンコード結果は `SLB1` アーティファクトとして保存されます。デコードすると元の
バイト列が完全に復元されます。CLI の出力はメタデータだけで、payload 本体は表示
しません。

## LLM transport capsule

LLM に gzip、base64、圧縮済みpayloadを直接読ませる方針は取りません。
圧縮済みbytesは LLM から見ると opaque なデータとして扱います。

代わりに、LLM には軽い capsule manifest を渡します。

```powershell
python -m starlight_codec capsule input.bin input.slb1 input.capsule.json `
  --summary "Asset metadata fixture" `
  --tag exact-roundtrip

python -m starlight_codec hydrate input.capsule.json chunk.bin --chunk c0001
python -m starlight_codec hydrate input.slb1 range.bin --range 0:4096
```

capsule には artifact reference、digest、サイズ、strategy、semantic tags、
summary、chunk index が入ります。raw bytes や base64 payload は埋め込みません。
LLM は metadata を読んで判断し、必要な時だけ tool layer で exact bytes を
hydrate します。

詳しくは [docs/llm-transport.md](docs/llm-transport.md) を参照してください。

## 今後

ロードマップは [docs/roadmap.md](docs/roadmap.md) にあります。

今後は、より賢い encoder planning、物理的な chunked container、dictionary、
domain-specific codec、そして圧縮とは分離した authenticated sealing /
encryption track を追加していく予定です。

## ベンチマーク

synthetic local benchmark は [BENCHMARKS.md](BENCHMARKS.md) にあります。
現在のbaselineでは、raw bytes、gzip、gzip+base64、`SLB1`、LLM向けcapsule
manifestを、重複text、JSON logs、random bytes、already-compressed inputで
比較しています。

## 名前について

公開名は **Star Light Codec** です。2026-07-07 時点の簡易調査では、
`StarCodec`、`Stable Codec`、各種 `Starlight` 関連プロジェクトなど近い名前は
見つかりましたが、`Star Light Codec` そのものの明確な既存プロダクト衝突は
見つかりませんでした。ただし、これは法的な商標調査ではありません。

## ライセンス

codec format を広く自由に再実装できるよう、format-first の方針です。

- 参照実装コード、CLI、テスト、benchmark scripts: Apache-2.0
- codec format、互換profile、schema、transport capsule spec、test vectors、
  fixtures、sample metadata、benchmark result data: CC0-1.0
- README、roadmap、説明文書: 明示がなければ CC BY 4.0

詳細は [LICENSING.md](LICENSING.md) を参照してください。
