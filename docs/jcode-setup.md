# Setup jcode (harness Claude + ChatGPT) — Windows

Panduan ni pasangkan **[jcode](https://github.com/1jehuang/jcode)** kat PC Windows kau dan
sambungkannya ke **subscription Claude bulanan** kau. Semua benda yang boleh diautomasi, skrip
`tools/setup-jcode.ps1` buat sendiri. Skrip berhenti tepat kat satu tempat je — **skrin login**,
di mana kau masukkan email/password Anthropic dalam browser. Skrip tak pernah nampak atau simpan
password kau.

jcode tu harness terminal (ditulis dalam Rust) yang jalankan model AI atas projek kau —
sama konsep macam Claude Code, tapi satu harness untuk banyak provider: Claude, ChatGPT/Codex,
Gemini, Copilot dan lain-lain. Dia MIT-licensed dan percuma; yang berbayar tu subscription AI
yang kau dah ada.

---

## ⚠️ Baca dulu sebelum start

Guna subscription **Claude Pro/Max** dalam harness pihak ketiga macam jcode **bukan jalan yang
Anthropic sokong secara rasmi**. Plan subscription tu disediakan untuk aplikasi Claude sendiri
(Claude app + Claude Code), dan akses OAuth dari client luar boleh dikira melanggar terma guna —
risikonya akaun kena rate-limit atau kena suspend. Jalan yang selamat dari segi terma ialah
guna **API key Anthropic** (bil ikut pakai) dengan `ANTHROPIC_API_KEY`, atau provider lain
(OpenRouter, DeepSeek, Ollama tempatan, dsb.) yang memang direka untuk client pihak ketiga.

Keputusan tu kau punya. Skrip ni support kedua-dua jalan:

```powershell
# Jalan subscription (yang kau minta)
.\tools\setup-jcode.ps1

# Jalan API key (tak sentuh subscription langsung)
setx ANTHROPIC_API_KEY "sk-ant-..."      # buka PowerShell baru selepas ni
.\tools\setup-jcode.ps1 -SkipLogin
```

---

## Prasyarat

| Perkara | Keperluan |
|---|---|
| OS | Windows 10/11, x64 atau ARM64 |
| PowerShell | 5.1 ke atas (Windows 11 dah ada; kalau nak PS7: `winget install Microsoft.PowerShell`) |
| Rangkaian | Akses ke `jcode.sh` dan `github.com` |
| Akaun | Akaun Anthropic yang ada subscription Claude bulanan |

Tak perlu Node, tak perlu Rust — melainkan kau paksa build dari source.

---

## Jalankan skrip

Buka PowerShell dalam folder repo ni:

```powershell
cd P:\RAG Database\pipeline          # atau di mana repo ni duduk
powershell -ExecutionPolicy Bypass -File .\tools\setup-jcode.ps1
```

Nak sekali sambungkan tool `file_rag` projek ni ke jcode, dan langkau smoke test:

```powershell
powershell -ExecutionPolicy Bypass -File .\tools\setup-jcode.ps1 -WireRagMcp -SkipSmokeTest
```

### Pilihan (parameter)

| Parameter | Fungsi |
|---|---|
| `-Provider <nama>` | Provider untuk login. Default `claude`. Lain: `openai`, `gemini`, `copilot`, `azure` |
| `-SkipInstall` | jcode dah ada — buat check + login je |
| `-SkipLogin` | Buat semua kecuali login (kau login sendiri kemudian) |
| `-SkipSmokeTest` | Jangan jalankan `jcode run "say hello"` (jimat token) |
| `-Headless` | Login guna `--no-browser` — untuk RDP/SSH atau bila browser tak boleh buka |
| `-ConfigureAlacritty` | Pasang sekali terminal Alacritty (opt-in) |
| `-ConfigureHotkey` | Daftar hotkey global untuk launch jcode (opt-in) |
| `-BuildFromSource` | Paksa build dari source (perlu Git + Rust + VS 2022 Build Tools, workload C++) |
| `-WireRagMcp` | Tulis `.mcp.json` supaya tool `file_rag` repo ni muncul dalam jcode |

---

## Apa yang skrip buat (9 langkah)

1. **Preflight** — semak versi PowerShell, OS, seni bina CPU, paksa TLS 1.2.
2. **Install** — turun dan jalankan installer rasmi (`https://jcode.sh/install.ps1`). Installer
   pilih aset x64/ARM64 yang betul dan **sahkan muat turun terhadap `SHA256SUMS` release**
   sebelum pasang.
3. **PATH** — segarkan `PATH` dalam sesi semasa, jadi `jcode` terus boleh guna tanpa buka
   tetingkap baru.
4. **Verify** — `jcode --version`, cetak SHA-256 binari, semak tandatangan Authenticode.
5. **Kredential sedia ada** — imbas kredential yang kau dah ada (`~/.jcode/auth.json`,
   `~/.claude/.credentials.json`, `~/.codex/auth.json`, `ANTHROPIC_API_KEY`, dll.) supaya tak
   login tak tentu pasal.
6. **Login** — 🔑 **langkah kau**. Lihat bahagian bawah.
7. **Auth test** — `jcode auth-test --all-configured`.
8. **Smoke test** — `jcode run "say hello"`.
9. **MCP (pilihan)** — tulis `.mcp.json` untuk `file_rag`.

---

## 🔑 Langkah yang perlukan kau

Skrip berhenti dan tunggu kau tekan Enter sebelum langkah ni:

```
jcode login --provider claude
```

Lepas tu:

1. Browser default kau terbuka ke halaman sign-in Anthropic.
2. **Log masuk guna akaun yang ada subscription bulanan tu** (email + password, plus 2FA kalau ada).
3. Approve akses untuk jcode.
4. Browser redirect balik ke callback tempatan; jcode simpan token kat
   `%USERPROFILE%\.jcode\auth.json`.

Password kau ditaip kat halaman Anthropic sahaja. Skrip tak baca, tak minta, tak simpan apa-apa
kredential.

**Kalau browser tak buka** (RDP, sesi remote, server tanpa GUI):

```powershell
jcode login --provider claude --no-browser
```

jcode akan cetak URL/kod — buka kat mana-mana peranti, siapkan, pastu paste balik.

---

## Lepas setup — command harian

```powershell
jcode                          # buka TUI dalam folder projek semasa
/model                         # dalam TUI: pilih model Claude
/account                       # dalam TUI: tukar antara akaun/subscription
jcode run "ringkaskan ingest.py"   # sekali jalan, tanpa TUI
jcode --resume fox             # sambung balik sesi lama ikut nama
jcode serve                    # jalan sebagai server latar belakang
jcode connect                  # attach client ke server tu
jcode auth-test --all-configured   # semak semua provider masih ok
jcode update                   # update jcode (ada pengesahan SHA-256)
```

Nak tambah ChatGPT/Codex sebagai provider kedua:

```powershell
jcode login --provider openai
```

Lepas tu boleh tukar-tukar provider ikut kerja — Claude untuk satu tugas, GPT untuk tugas lain,
dalam harness yang sama.

---

## Lokasi fail

| Apa | Di mana |
|---|---|
| Launcher | `%LOCALAPPDATA%\jcode\bin\jcode.exe` |
| Binari stabil | `%LOCALAPPDATA%\jcode\builds\stable\jcode.exe` |
| Config | `%USERPROFILE%\.jcode\config.toml` |
| Auth Claude | `%USERPROFILE%\.jcode\auth.json` |
| Auth OpenAI | `%USERPROFILE%\.jcode\openai-auth.json` |
| MCP (jcode) | `%USERPROFILE%\.jcode\mcp.json` |
| MCP (per-repo) | `.mcp.json` kat root repo — jcode baca format Claude Code terus |

---

## Sambungkan `file_rag` (RAG pipeline ni) ke jcode

jcode baca config MCP gaya Claude Code secara langsung: `~/.claude.json`, `.mcp.json` kat root
repo, dan `.claude/mcp.json`. Jadi tool `file_rag` projek ni boleh masuk dalam sesi jcode tanpa
tulis adapter apa-apa.

Jalankan skrip dengan `-WireRagMcp`, atau tulis sendiri `.mcp.json`:

```json
{
  "mcpServers": {
    "file-rag": {
      "command": "P:\\RAG Database\\pipeline\\.venv\\Scripts\\python.exe",
      "args": ["P:\\RAG Database\\pipeline\\rag_mcp.py"],
      "env": {}
    }
  }
}
```

`.mcp.json` mengandungi path mutlak mesin kau, jadi dia dah masuk `.gitignore` — jangan commit.

Nota: jcode setakat ni sokong server MCP jenis **stdio** sahaja. Entri `"type": "http"` atau
`"sse"` dikenali tapi dilangkau.

---

## Masalah biasa

**"Windows protected your PC" (SmartScreen)**
Muncul bila binari tak bertandatangan atau publisher masih baharu. **Jangan** matikan Defender dan
jangan tambah exclusion. Sahkan dulu: URL release betul, SHA-256 sepadan dengan `SHA256SUMS`
release (skrip cetak hash tu), dan `Get-AuthenticodeSignature` kalau signing dah aktif. Kalau
build rasmi yang bertandatangan pun masih kena tahan, hantar ke
[portal false-positive Microsoft](https://www.microsoft.com/wdsi/filesubmission).

**`jcode` tak dijumpai selepas install**
Installer tambah `%LOCALAPPDATA%\jcode\bin` ke PATH pengguna, tapi tetingkap yang sedang terbuka
masih pegang PATH lama. Buka PowerShell baru, atau jalankan semula skrip ni (dia segarkan PATH
sendiri).

**Installer berhenti kata takde aset padan**
Release tu takde binari Windows untuk seni bina kau. Guna `-BuildFromSource` (perlu Git, Rust, dan
Visual Studio 2022 Build Tools dengan workload *Desktop development with C++*), atau tunggu release
berikutnya.

**Login gagal atau token luput**
Login semula: `jcode login --provider claude`. Nak reset habis, padam
`%USERPROFILE%\.jcode\auth.json` pastu login balik.

**Amaran cache Claude sejuk**
Cache prompt Anthropic sejuk lepas 5 minit. jcode beritahu bila cache sejuk atau bila ada cache
miss yang tak dijangka — itu maklumat kos, bukan ralat.

**Nak buang jcode**
Uninstaller rasmi buang binari dan launcher tapi kekalkan config, auth, dan sesi. Rujuk
[README jcode](https://github.com/1jehuang/jcode#uninstall).

---

## Rujukan

- Repo: <https://github.com/1jehuang/jcode>
- Nota Windows (Defender, SmartScreen, signing): <https://github.com/1jehuang/jcode/blob/master/docs/WINDOWS.md>
- Laman rasmi + docs: <https://jcode.sh> · <https://jcode.sh/docs>
