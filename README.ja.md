# Star Light Codec

Star Light Codec は、Star Light プロジェクトから生まれた実験的な
exact byte artifact codec です。

任意のバイト列を `SLB1` という自己説明型のアーティファクトにまとめ、
デコーダー側で元のバイト列を完全に復元します。ファイル種別を知らなくても
exact round-trip できることが、この方式の一番大事なところです。

これは動画や音声を再生するための codec pack ではありません。Astro
Starlight とも無関係です。

現在の公開範囲はあえて小さくしています。

- 任意ファイルの完全復元
- `SLB1` binary artifact container の作成と読み取り
- 最大4回までの bounded gzip transform
- 実験的な predictive residual model layer
- payload と元入力の SHA-256 検証
- 全体サイズが大きくなる場合の `keep-original-for-storage` 推奨
- Star Light 本体の `starlight-byte-exact` 互換プロファイル
- encoder/decoder 分離による、将来の圧縮方式差し替え余地

重要なのは、最初の encoder が gzip を使っていることではありません。
重要なのはアーティファクト契約です。decoder は元ファイル形式を知らなくても
exact bytes を復元でき、payload と復元後の bytes を検証できます。将来、
encoder 側の planner を賢くしても、decode の安全性を保てる構造を目指しています。

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

encoder は `SLB1` アーティファクトを書き出します。decoder は元のバイト列を
完全に復元します。CLI の出力はメタデータだけで、payload 本体は表示しません。

## どう強いか

- **任意のバイト列を扱える:** text、JSON、log、binary、生成物、未知の
  ファイル形式を同じ exact-byte interface で扱えます。
- **完全復元を検証する:** 元サイズ、payload サイズ、payload digest、最終的な
  input digest を持ち、復元結果が本当に元データと一致するか確認します。
- **decoder が単純:** header を読み、長さと digest を検証し、allowlist 済み
  transform を逆順に適用するだけです。
- **encoder を育てられる:** chunking、dictionary、residual、domain-specific
  codec などを追加しても、exact round-trip の契約を保てます。
- **圧縮できない時に盛らない:** payload だけでなく artifact 全体が元データより
  小さいかを見て、保存採用すべきかをメタデータで返します。

## 技術形状

`SLB1` は自己完結した exact-byte artifact です。

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

payload は JSON に埋め込まず、header の後ろに raw bytes として置きます。
これにより base64 expansion を避けつつ、metadata は人間が inspect できる形を
保ちます。

詳しい仕様は [docs/spec.md](docs/spec.md) を参照してください。

## 現在の encoder

reference encoder は bounded transform planner を使います。

1. 入力の形を分類する
2. 最大4回まで gzip pass を試す
3. payload が小さくならなくなったら止める
4. `stored-base64`、`gzip-base64`、`gzip-recursive-base64` などの strategy を記録する
5. artifact 全体のサイズを元入力と比較する
6. 小さくなった時だけ `use-artifact-for-storage` を返す

これは到達点ではなく、土台です。今後 encoder 側を賢くしていきながら、
exact round-trip と fail-closed decode を守ります。

## 実験的な model layer

Star Light Codec は、圧縮前に小さな決定的予測モデルを試すこともできます。

```powershell
python -m starlight_codec encode input.bin input.slb1 --model auto
python -m starlight_codec capsule input.bin input.slb1 input.capsule.json --model auto
```

最初のモデルは `delta-prev-v1` です。ひとつ前の byte から次の byte を予測し、
差分 residual を作ってから、通常の bounded gzip planner に渡します。

これは neural compressor ではありません。また lossy でもありません。
model id、model hash、transform stack、payload digest、最終 input digest を
保存するので、decode は exact かつ fail-closed のままです。

`--model auto` は baseline encoder と model encoder を比較し、`SLB1` artifact
全体が小さくなる場合だけ model 側を採用します。既定値は互換性重視の
`--model none` です。

## LLM transport capsule

LLM に gzip、base64、圧縮済み payload を直接読ませる方針は取りません。
圧縮済み bytes は LLM から見ると opaque なデータとして扱います。

代わりに、LLM には軽い capsule manifest を渡します。

```powershell
python -m starlight_codec capsule input.bin input.slb1 input.capsule.json `
  --summary "Asset metadata fixture" `
  --tag exact-roundtrip

python -m starlight_codec hydrate input.capsule.json chunk.bin --chunk c0001
python -m starlight_codec hydrate input.slb1 range.bin --range 0:4096
```

capsule には artifact reference、digest、size、strategy、semantic tags、
summary、chunk index が入ります。raw bytes や base64 payload は埋め込みません。
LLM は metadata を読んで判断し、必要な時だけ tool layer で exact bytes を
hydrate します。

詳しくは [docs/llm-transport.md](docs/llm-transport.md) を参照してください。

## これは何ではないか

- gzip、zstd、Brotli、PNG、MP3、Opus などの成熟した codec の代替ではありません。
- universal compression を主張するものではありません。
- neural machine-learning compressor ではありません。
- production security system ではありません。
- media file を再生する codec pack ではありません。

## ベンチマーク

synthetic local benchmark は [BENCHMARKS.md](BENCHMARKS.md) にあります。
現在の baseline では、raw bytes、gzip、gzip+base64、`SLB1`、`--model auto`、
LLM 向け capsule manifest を、redundant text、JSON logs、ramp bytes、
random bytes、already-compressed input で比較しています。

## ロードマップ

ロードマップは [docs/roadmap.md](docs/roadmap.md) にあります。
今後は、より賢い encoder planning、物理的な chunked container、dictionary、
domain-specific residual codec、圧縮とは分離した authenticated sealing /
encryption track を追加していく予定です。

## 名前について

公開名は **Star Light Codec** です。2026-07-07 時点の簡易調査では、
`StarCodec`、`Stable Codec`、各種 `Starlight` 関連プロジェクトなど近い名前は
見つかりましたが、`Star Light Codec` そのものの明確な既存プロダクト衝突は
見つかりませんでした。ただし、これは法的な商標調査ではありません。

## ライセンス

この repository は、codec format を広く自由に再実装できるよう、
format-first の方針を取ります。

- reference implementation code、CLI、tests、benchmark scripts: Apache-2.0
- codec format、compatibility profile、schemas、transport capsule spec、
  test vectors、fixtures、sample metadata、benchmark result data: CC0-1.0
- README、roadmap、説明文書: 明示がなければ CC BY 4.0

詳しくは [LICENSING.md](LICENSING.md) を参照してください。
