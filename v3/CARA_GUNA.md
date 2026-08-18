# Cara guna — 2 benda je yang tinggal

Kalau kau baca satu fail je dalam repo ni, baca yang ni.

---

## Kenapa aku (Claude) tak boleh buat dua benda ni sendiri

Semua kod dah siap dan dah diuji. Tapi ada dua soalan yang **tak boleh dijawab
dari sini**, sebab jawapannya ada dalam PC kau dan dalam kepala kau:

1. **Berapa lama safety check ambil masa atas 662,244 fail kau?**
   Aku tak pernah run atas hardware kau. Kalau aku bagi nombor, itu tekaan.

2. **Sistem ni betul-betul jumpa dokumen yang betul ke?**
   Untuk tahu, kena ada senarai soalan betul + fail mana yang sepatutnya keluar.
   Aku tak kenal dokumen kau. Kalau aku reka soalan sendiri, markah tu tipu —
   nampak macam ukuran, sebenarnya bukan.

---

## LANGKAH 1 — Update kod

Buka PowerShell, taip:

```
cd C:\rag-pipeline
git pull
```

---

## LANGKAH 2 — Berapa lama safety check? (5 minit)

```
cd C:\rag-pipeline\v3
python -m alirag.cli safety snapshot --sample 2000
```

Dia akan cuba 2,000 fail je, lepas tu bagitahu:

* `files_per_second` — laju mana
* `estimated_full_run_s` — anggaran saat untuk semua fail

Bahagi dengan 60 = minit. Kalau nampak munasabah, baru run penuh nanti.
**Ini anggaran, bukan ukuran sebenar** — fail besar lagi lambat dari fail kecil.

Hantar output tu kat aku.

---

## LANGKAH 3 — Ujian ketepatan (ni yang penting)

### 3a. Buat borang soalan

```
python -m alirag.cli bench make --csv
```

Dia tulis satu fail Excel kat:
`E:\ALI_RAG\15_BENCHMARK\questions.csv`

### 3b. Buka dalam Excel, isi

Double-click fail tu. Kau akan nampak table macam ni:

| question | expected_file | project | mode | reviewed | ... |
|---|---|---|---|---|---|
| *(kosong — kau isi)* | Selgate 01.pdf | SITE CONCEPT | | no | |

Untuk setiap baris yang kau nak guna:

1. **`question`** — taip soalan BETUL, guna bahasa kau sendiri, soalan yang kau
   memang akan tanya. Jawapannya mesti ada dalam fail tu.
   Contoh: *"Berapa jumlah claim yang certified untuk Selgate?"*
2. **`expected_file`** — dah diisi. Tukar hanya kalau fail LAIN yang sepatutnya
   jawab soalan kau.
3. **`project`** — dah diisi; betulkan kalau salah.
4. **`reviewed`** — tukar `no` jadi **`yes`** bila kau puas hati dengan baris tu.

Baris yang kau tak sentuh akan diabaikan. **Minimum 5 baris** kena `yes`.
Baris lain boleh delete. Save (kekalkan format CSV bila Excel tanya).

> Kalau kau tak pasti fail tu pasal apa — ada column `_snippet_hint` yang
> tunjuk sikit isi dokumen tu.

### 3c. Run ujian

```
python -m alirag.cli bench run --questions "E:\ALI_RAG\15_BENCHMARK\questions.csv" --label fast
```

Hantar output kat aku. Baru kita tahu:

* **recall** — berapa kerap fail yang betul keluar
* **mrr** — kedudukan dia dalam senarai
* **wrong_project_rate** — berapa kerap dia bagi dokumen projek LAIN
  (ni yang paling bahaya)

---

## CARA SENANG — buat semua ni dari WhatsApp

Kalau taip command satu-satu kat cmd tu menyusahkan, ada jalan lain. V1 kau
dah sambung ke WhatsApp (OpenClaw). V3 pun boleh.

### Pasang sekali je

1. Pastikan `mcp` dipasang:

```
pip install mcp
```

2. Buka `C:\Users\User\.openclaw\openclaw.json`, cari bahagian
   `mcp.servers`, tambah satu entry:

```json
"alirag": {
  "command": "python",
  "args": ["-m", "alirag.mcp_server"],
  "cwd": "C:\\rag-pipeline\\v3",
  "env": { "ALIRAG_CONFIG": "C:\\rag-pipeline\\v3\\config.kazasline.yaml" }
}
```

3. Restart OpenClaw.

### Lepas tu, guna macam biasa

WhatsApp kau:

> **Kau:** berapa claim amount yang certified untuk Selgate?
>
> **Ali:** RM97,923.07 …
> Sumber: AVC 2 SCI CERT PAYMENT 11R01.pdf p.17 — projek: SITE CONCEPT
> *Betul tak? Balas: betul / salah <nama fail yang sepatutnya>*
>
> **Kau:** betul
>
> **Ali:** Direkod. Jumlah soalan ujian: 1. Lagi 4 soalan sebelum boleh run ujian.

**Itu sahaja.** Setiap kali kau balas "betul" atau "salah", satu soalan ujian
terbina sendiri — soalan BETUL yang kau memang tanya, bukan soalan auta. Lepas
5 soalan, baru boleh run ujian ketepatan.

Ini sama je dengan isi Excel tu, cuma kau buat masa kau memang tengah guna
sistem, masa kau memang tahu jawapan tu betul ke tak.

### Satu benda kena tahu (privasi)

Hantar jawapan ke WhatsApp bermakna teks jawapan + nama fail lalu server Meta.
V1 kau memang dah buat macam tu (hantar gambar page). Carian, model dan index
semua kekal dalam PC kau — cuma balasan je keluar. Ini keputusan kau, tapi aku
sebut sebab ia nyata.

---

## Kenapa sistem ni degil pasal `reviewed: yes`

Sistem ni memang sengaja **tolak** untuk bagi markah kalau soalan tak disemak
manusia. Sebab senang sangat nak dapat nombor cantik dari soalan auta, lepas tu
tersilap ingat sistem ni dah terbukti. Nombor tanpa semakan lagi teruk dari
takde nombor langsung — sebab kau akan percaya.

---

## Status sekarang (jujur)

| | |
|---|---|
| Kod siap | ✅ 283 ujian lulus |
| Fail kau selamat | ✅ tiada kod yang tulis/padam/rename fail asal — 7 kali disemak |
| Boleh jawab soalan | ✅ dah pernah bagi jawapan betul dengan citation |
| **Ketepatan berapa %** | ❌ **BELUM DIUKUR** — kena LANGKAH 3 |
| **Safety check atas E:\ penuh** | ❌ **BELUM DIRUN** — kena LANGKAH 2 |
| Sign-off | ❌ Reviewer bebas dah gagalkan 7 kali; setiap kali aku baiki |

Sesiapa yang cakap sistem ni "siap" sebelum LANGKAH 2 dan 3 selesai, tak
bercakap benar — termasuk aku.
