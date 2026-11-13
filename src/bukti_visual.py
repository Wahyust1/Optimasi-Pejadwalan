"""
Bukti visual hard constraint (tanpa web): workbook Excel + gambar heatmap dari sebuah jadwal.

Idenya: ubah jadwal menjadi PETA HUNIAN. Baris = dosen / ruangan / paket, kolom = blok waktu (hari x jam). Isi sel = berapa sesi
yang menempati sel itu. Pelanggaran muncul sebagai sel MERAH; bila tidak ada sel merah, constraint terbukti terpenuhi.

Di workbook, angka pada peta bukan hasil hitung Python melainkan RUMUS EXCEL (COUNTIFS/SUMIFS) atas tabel data mentah, sehingga
dosen dapat mengklik sel dan memeriksa sendiri. Python hanya meratakan jadwal menjadi tabel data (satu baris = satu kelas pada
satu blok); semua penghitungan bentrok dilakukan Excel.

Sheet:
  Ringkasan       banner status, jumlah pelanggaran tiap H1-H6 (rumus), cara membuktikan
  Legenda         arti warna, sesi gabungan, batasan bukti
  H3_Dosen        peta dosen x blok (sel = jumlah sesi berbeda), klik sel untuk melihat kelasnya
  H2_Ruangan      peta ruangan x blok (merah bila melebihi kapasitas atau ada dua mata kuliah berbeda)
  H4_Paket        peta paket x blok (satu paket = mahasiswa yang sama, tidak boleh dua kelas bersamaan)
  H1_Kelas        tiap kelas: SKS, jumlah blok yang tercakup (rumus), status
  H5_H6_Aturan    tiap kelas: ruangan dan blok yang dipakai terhadap aturan ruangan dan blok per prodi
  Data_*          tabel mentah tempat rumus bekerja
Gambar (PNG): heatmap dosen, ruangan, paket untuk laporan.

Contoh:
  python src\\bukti_visual.py                    # otomatis: gaN terbaru dan data bersih dari status
  python src\\bukti_visual.py --jadwal data\\ga\\ga3 --anonim
Keluaran: folder bukti_visualN di sebelah jadwal. Termasuk bukti_visual_SABOTASE.xlsx: salinan dengan SATU pelanggaran sengaja,
agar terlihat bahwa peta memang menampilkan bentrok (berkas contoh, bukan jadwal asli).
"""
import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

from openpyxl import Workbook
from openpyxl.comments import Comment
from openpyxl.formatting.rule import FormulaRule
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter as L

from audit import Data, baca, folder_keluar, himp, menit, periksa, r_fmt, tentukan, peringatan_data

HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat", "Sabtu", "Minggu"]
MERAH = PatternFill("solid", bgColor="F4B6B6", fgColor="F4B6B6")
HIJAU = PatternFill("solid", bgColor="CFE8CF", fgColor="CFE8CF")
ABU = PatternFill("solid", fgColor="E8E8E8")
TEBAL = Font(bold=True)


def urut_blok(d):
    bl = list(d.blok.values())
    bl.sort(key=lambda b: (HARI.index(b["hari"]) if b["hari"] in HARI else 9, menit(b["jam_mulai"])))
    return bl


def ratakan(d, iv, blok):
    """Kelas -> daftar blok yang dicakup. Tabel panjang untuk rumus Excel."""
    cakup = {}
    for kid, (hari, m1, m2, ruang, mk, ba) in iv.items():
        cakup[kid] = [b["blok_id"] for b in blok if b["hari"] == hari and menit(b["jam_mulai"]) >= m1 and menit(b["jam_selesai"]) <= m2]
    return cakup


def sesi_id(iv, kid):
    hari, m1, m2, ruang, mk, _ = iv[kid]
    return f"{mk}|{hari}|{r_fmt(m1)}-{r_fmt(m2)}|{ruang}"


def tulis_header(ws, blok, kolom0, baris_nama_kolom):
    """Tiga baris header: hari (digabung), id blok, jam. Kolom blok mulai di kolom0."""
    ws.cell(baris_nama_kolom, 1, "Hari").font = TEBAL
    ws.cell(baris_nama_kolom + 1, 1, "Blok").font = TEBAL
    ws.cell(baris_nama_kolom + 2, 1, "Jam").font = TEBAL
    j = kolom0
    awal = {}
    for b in blok:
        ws.cell(baris_nama_kolom, j, b["hari"] if b["hari"] not in awal else None)
        awal.setdefault(b["hari"], j)
        ws.cell(baris_nama_kolom + 1, j, b["blok_id"]).alignment = Alignment(horizontal="center")
        ws.cell(baris_nama_kolom + 2, j, b["jam_mulai"]).alignment = Alignment(horizontal="center", text_rotation=90)
        ws.column_dimensions[L(j)].width = 4.2
        j += 1
    for h, c in awal.items():
        n = sum(1 for b in blok if b["hari"] == h)
        ws.merge_cells(start_row=baris_nama_kolom, start_column=c, end_row=baris_nama_kolom, end_column=c + n - 1)
        ws.cell(baris_nama_kolom, c).alignment = Alignment(horizontal="center")
        ws.cell(baris_nama_kolom, c).font = TEBAL
        ws.cell(baris_nama_kolom, c).fill = ABU
    ws.row_dimensions[baris_nama_kolom + 2].height = 34


def bangun(d, rows, jalur, judul, anonim=False, hanya_catatan=""):
    pelanggar, stat, iv = periksa(d, rows)
    blok = urut_blok(d)
    cakup = ratakan(d, iv, blok)
    nama = dict(d.dosen_nama)
    if anonim:
        for i, did in enumerate(sorted(nama), 1):
            nama[did] = f"Dosen {i:02d}"
    wb = Workbook()
    ring = wb.active
    ring.title = "Ringkasan"
    leg = wb.create_sheet("Legenda")

    # ---------------- Data_Blok
    wsb = wb.create_sheet("Data_Blok")
    wsb.append(["blok_id", "hari", "sesi", "urutan", "mulai_menit", "selesai_menit", "jam_mulai", "jam_selesai", "prodi_diizinkan"])
    for b in blok:
        wsb.append([b["blok_id"], b["hari"], b["sesi"], int(b["urutan"]), menit(b["jam_mulai"]), menit(b["jam_selesai"]),
                    b["jam_mulai"], b["jam_selesai"], b.get("prodi_diizinkan", "semua")])
    nb = len(blok) + 1

    # ---------------- Data_Kelas (H1)
    wsk = wb.create_sheet("H1_Kelas")
    wsk.append(["kelas_id", "prodi", "mata_kuliah", "sks (blok)", "hari", "mulai_menit", "selesai_menit", "jam_mulai", "jam_selesai",
                "ruangan", "dosen", "blok_tercakup (rumus)", "status (rumus)"])
    baris = 2
    for kid in sorted(d.kelas):
        k = d.kelas[kid]
        sks = int(k["durasi_blok"] or k["sks"])
        dos = "; ".join(sorted(nama.get(x, x) for x in d.kelas_dosen.get(kid, ())))
        if kid in iv:
            h, m1, m2, ruang, mk, _ = iv[kid]
            wsk.append([kid, k["prodi"], k["mk_id"], sks, h, m1, m2, r_fmt(m1), r_fmt(m2), d.nama_ruang.get(ruang, ruang), dos])
            wsk.cell(baris, 12, f'=COUNTIFS(Data_Blok!$B$2:$B${nb},E{baris},Data_Blok!$E$2:$E${nb},">="&F{baris},Data_Blok!$F$2:$F${nb},"<="&G{baris})')
            wsk.cell(baris, 13, f'=IF(L{baris}=D{baris},"OK","SALAH")')
        else:
            wsk.append([kid, k["prodi"], k["mk_id"], sks, None, None, None, None, None, None, dos])
            wsk.cell(baris, 12, 0)
            wsk.cell(baris, 13, "TIDAK TERJADWAL")
        baris += 1
    nk = baris - 1
    wsk.freeze_panes = "B2"
    for c, w in zip("ABCDEFGHIJKLM", (46, 8, 36, 9, 9, 9, 9, 9, 9, 12, 50, 14, 16)):
        wsk.column_dimensions[c].width = w
    wsk.conditional_formatting.add(f"M2:M{nk}", FormulaRule(formula=['M2<>"OK"'], fill=MERAH))
    wsk.conditional_formatting.add(f"M2:M{nk}", FormulaRule(formula=['M2="OK"'], fill=HIJAU))

    # ---------------- Data_Dosen_Blok dan peta dosen
    wsd = wb.create_sheet("Data_Dosen_Blok")
    wsd.append(["dosen", "hari", "blok_id", "sesi_id", "pertama_di_sel (rumus)", "kelas_id"])
    r = 2
    isi_dosen = defaultdict(set)
    for kid in sorted(iv):
        for dos in sorted(d.kelas_dosen.get(kid, ())):
            for b in cakup[kid]:
                wsd.append([nama.get(dos, dos), iv[kid][0], b, sesi_id(iv, kid), None, kid])
                wsd.cell(r, 5, f"=IF(COUNTIFS($A$2:A{r},A{r},$C$2:C{r},C{r},$D$2:D{r},D{r})=1,1,0)")
                isi_dosen[(nama.get(dos, dos), b)].add(kid)
                r += 1
    nd = r - 1
    dosen_pakai = sorted({nama.get(x, x) for kid in iv for x in d.kelas_dosen.get(kid, ())})
    wsm = wb.create_sheet("H3_Dosen")
    wsm["A1"] = "H3 - Tidak ada dosen yang mengajar dua sesi berbeda pada waktu yang sama"
    wsm["A1"].font = Font(bold=True, size=13)
    wsm["A2"] = "Sel = jumlah SESI berbeda dosen itu pada blok tersebut (rumus SUMIFS atas Data_Dosen_Blok). Hijau = 1 sesi. MERAH = lebih dari 1 (bentrok). Arahkan kursor ke sel hijau untuk melihat kelasnya."
    tulis_header(wsm, blok, 2, 3)
    wsm.cell(3, 1, "Hari").font = TEBAL
    r0 = 6
    for i, dos in enumerate(dosen_pakai):
        rr = r0 + i
        wsm.cell(rr, 1, dos)
        for j, b in enumerate(blok):
            c = 2 + j
            wsm.cell(rr, c, f"=SUMIFS(Data_Dosen_Blok!$E$2:$E${nd},Data_Dosen_Blok!$A$2:$A${nd},$A{rr},Data_Dosen_Blok!$C$2:$C${nd},{L(c)}$4)")
            ks = isi_dosen.get((dos, b["blok_id"]))
            if ks:
                wsm.cell(rr, c).comment = Comment("\n".join(sorted(ks)), "bukti_visual", width=380, height=20 + 16 * len(ks))
            wsm.cell(rr, c).number_format = "0;;"
    r1 = r0 + len(dosen_pakai) - 1
    rng = f"B{r0}:{L(1 + len(blok))}{r1}"
    wsm.conditional_formatting.add(rng, FormulaRule(formula=[f"B{r0}>1"], fill=MERAH, font=Font(bold=True, color="9C0006")))
    wsm.conditional_formatting.add(rng, FormulaRule(formula=[f"B{r0}=1"], fill=HIJAU))
    wsm.column_dimensions["A"].width = 40
    wsm.freeze_panes = f"B{r0}"
    wsm["A5"] = "Dosen"
    wsm["A5"].font = TEBAL

    # ---------------- Data_Ruang_Blok dan peta ruangan
    wsr = wb.create_sheet("Data_Ruang_Blok")
    wsr.append(["ruangan", "hari", "blok_id", "sesi_id", "kelas_id", "mata_kuliah", "pertama_sesi (rumus)", "kelas_di_sel (rumus)",
                "sesi_di_sel (rumus)", "batas_kapasitas", "pelanggaran_sel (rumus)"])
    r = 2
    isi_ruang = defaultdict(set)
    for kid in sorted(iv):
        h, m1, m2, ruang, mk, _ = iv[kid]
        rn = d.nama_ruang.get(ruang, ruang)
        cap_r = max(1, int(float(d.ruang[ruang].get("maks_kelas_simultan") or 1))) if ruang in d.ruang else 1
        cap_mk = int(float(d.mk.get(mk, {}).get("kapasitas_gabung") or 99))
        for b in cakup[kid]:
            wsr.append([rn, h, b, sesi_id(iv, kid), kid, mk, None, None, None, min(cap_r, cap_mk), None])
            wsr.cell(r, 7, f"=IF(COUNTIFS($A$2:A{r},A{r},$C$2:C{r},C{r},$D$2:D{r},D{r})=1,1,0)")
            r += 1
            isi_ruang[(rn, b)].add(kid)
    nr = r - 1
    for rr in range(2, nr + 1):
        wsr.cell(rr, 8, f"=COUNTIFS($A$2:$A${nr},A{rr},$C$2:$C${nr},C{rr})")
        wsr.cell(rr, 9, f"=SUMIFS($G$2:$G${nr},$A$2:$A${nr},A{rr},$C$2:$C${nr},C{rr})")
        wsr.cell(rr, 11, f"=IF(COUNTIFS($A$2:A{rr},A{rr},$C$2:C{rr},C{rr})=1,IF(OR(I{rr}>1,H{rr}>J{rr}),1,0),0)")
    ruang_pakai = sorted({d.nama_ruang.get(r, r) for r in d.ruang})   # semua ruangan, termasuk yang tidak terpakai
    nama2id = {d.nama_ruang.get(r, r): r for r in d.ruang}
    wsq = wb.create_sheet("H2_Ruangan")
    wsq["A1"] = "H2 - Ruangan: hanya satu mata kuliah pada satu waktu, jumlah kelas tidak melebihi kapasitas"
    wsq["A1"].font = Font(bold=True, size=13)
    wsq["A2"] = ("Sel = jumlah KELAS di ruangan itu pada blok tersebut. MERAH bila (a) ada dua mata kuliah berbeda atau waktu mulai berbeda, atau (b) "
                 "jumlah kelas melebihi kapasitas simultan ruangan/mata kuliah. Beberapa paralel mata kuliah yang sama pada jam yang sama = sesi gabungan (sah).")
    tulis_header(wsq, blok, 2, 3)
    for i, rn in enumerate(ruang_pakai):
        rr = r0 + i
        wsq.cell(rr, 1, rn)
        for j, b in enumerate(blok):
            c = 2 + j
            wsq.cell(rr, c, f"=COUNTIFS(Data_Ruang_Blok!$A$2:$A${nr},$A{rr},Data_Ruang_Blok!$C$2:$C${nr},{L(c)}$4)")
            ks = isi_ruang.get((rn, b["blok_id"]))
            if ks:
                wsq.cell(rr, c).comment = Comment("\n".join(sorted(ks)), "bukti_visual", width=380, height=20 + 16 * len(ks))
            wsq.cell(rr, c).number_format = "0;;"
    r1q = r0 + len(ruang_pakai) - 1
    rng = f"B{r0}:{L(1 + len(blok))}{r1q}"
    wsq.conditional_formatting.add(rng, FormulaRule(
        formula=[f"SUMIFS(Data_Ruang_Blok!$K$2:$K${nr},Data_Ruang_Blok!$A$2:$A${nr},$A{r0},Data_Ruang_Blok!$C$2:$C${nr},B$4)>0"],
        fill=MERAH, font=Font(bold=True, color="9C0006")))
    wsq.conditional_formatting.add(rng, FormulaRule(formula=[f"B{r0}>=1"], fill=HIJAU))
    wsq.column_dimensions["A"].width = 14
    wsq.freeze_panes = f"B{r0}"
    wsq["A5"] = "Ruangan"
    wsq["A5"].font = TEBAL

    # ---------------- Blok_Kosong: rumus dari peta ruangan (angka harus sama dengan hari_ringkasan/ruangan_ringkasan pada laporan)
    wbk = wb.create_sheet("Blok_Kosong")
    wbk["A1"] = "Blok ruangan kosong per ruangan dan per hari"
    wbk["A1"].font = Font(bold=True, size=13)
    wbk["A2"] = ("Tersedia = blok pada hari yang diizinkan untuk ruangan itu. Terisi = blok yang dipakai minimal satu kelas (dihitung dengan rumus dari peta H2_Ruangan). "
                 "Kosong = Tersedia dikurangi Terisi. Definisi sama dengan laporan (ruangan_ringkasan.csv dan hari_ringkasan.csv).")
    hari_ada = [h for h in HARI if any(b["hari"] == h for b in blok)]
    kolom_hari = {}
    for j, b in enumerate(blok):
        kolom_hari.setdefault(b["hari"], [2 + j, 2 + j])[1] = 2 + j
    wbk.cell(4, 1, "Ruangan").font = TEBAL
    for k, h in enumerate(hari_ada):
        c0 = 2 + 3 * k
        wbk.cell(4, c0, h).font = TEBAL
        wbk.merge_cells(start_row=4, start_column=c0, end_row=4, end_column=c0 + 2)
        wbk.cell(4, c0).alignment = Alignment(horizontal="center")
        wbk.cell(4, c0).fill = ABU
        for t, nm_ in enumerate(("Tersedia", "Terisi", "Kosong")):
            wbk.cell(5, c0 + t, nm_).font = TEBAL
            wbk.column_dimensions[L(c0 + t)].width = 9
    ct = 2 + 3 * len(hari_ada)
    for t, nm_ in enumerate(("Tersedia", "Terisi", "Kosong", "% terisi")):
        wbk.cell(5, ct + t, nm_).font = TEBAL
        wbk.cell(4, ct, "Seluruh minggu").font = TEBAL
    wbk.merge_cells(start_row=4, start_column=ct, end_row=4, end_column=ct + 3)
    wbk.cell(4, ct).fill = ABU
    wbk.cell(4, ct).alignment = Alignment(horizontal="center")
    bk0 = 6
    for i, rn in enumerate(ruang_pakai):
        rr = bk0 + i
        izin = himp(d.ruang[nama2id[rn]].get("hari_diizinkan"))
        wbk.cell(rr, 1, rn)
        r_h2 = r0 + i
        for k, h in enumerate(hari_ada):
            c0 = 2 + 3 * k
            n_h = sum(1 for b in blok if b["hari"] == h)
            wbk.cell(rr, c0, n_h if (izin is None or h.casefold() in izin) else 0)
            ca, cb = kolom_hari[h]
            wbk.cell(rr, c0 + 1, f'=COUNTIF(H2_Ruangan!{L(ca)}{r_h2}:{L(cb)}{r_h2},">0")')
            wbk.cell(rr, c0 + 2, f"={L(c0)}{rr}-{L(c0 + 1)}{rr}")
        for t in range(3):
            wbk.cell(rr, ct + t, "=" + "+".join(f"{L(2 + 3 * k + t)}{rr}" for k in range(len(hari_ada))))
        wbk.cell(rr, ct + 3, f"=IF({L(ct)}{rr}=0,0,{L(ct + 1)}{rr}/{L(ct)}{rr})").number_format = "0.0%"
    bk1 = bk0 + len(ruang_pakai) - 1
    tot = bk1 + 1
    wbk.cell(tot, 1, "SEMUA RUANGAN").font = TEBAL
    for c in range(2, ct + 3):
        wbk.cell(tot, c, f"=SUM({L(c)}{bk0}:{L(c)}{bk1})").font = TEBAL
    wbk.cell(tot, ct + 3, f"=IF({L(ct)}{tot}=0,0,{L(ct + 1)}{tot}/{L(ct)}{tot})").number_format = "0.0%"
    wbk.cell(tot, ct + 3).font = TEBAL
    wbk.conditional_formatting.add(f"{L(2)}{bk0}:{L(ct + 2)}{bk1}", FormulaRule(formula=[f'AND({L(2)}$5="Kosong",{L(2)}{bk0}>0)'], fill=PatternFill("solid", bgColor="FFF2CC", fgColor="FFF2CC")))
    wbk.column_dimensions["A"].width = 18
    blok_kosong_total = f"Blok_Kosong!{L(ct + 2)}{tot}"
    blok_tersedia_total = f"Blok_Kosong!{L(ct)}{tot}"

    # ---------------- Data_Paket_Blok dan peta paket
    wsp = wb.create_sheet("Data_Paket_Blok")
    wsp.append(["paket", "hari", "blok_id", "kelas_id"])
    r = 2
    isi_paket = defaultdict(set)
    for p, ks in sorted(d.paket.items()):
        for kid in sorted(ks):
            if kid in iv:
                for b in cakup[kid]:
                    wsp.append([p, iv[kid][0], b, kid])
                    isi_paket[(p, b)].add(kid)
                    r += 1
    npk = r - 1
    paket_pakai = sorted(d.paket)
    wsx = wb.create_sheet("H4_Paket")
    wsx["A1"] = "H4 - Mahasiswa satu paket (prodi, semester, kelas) tidak boleh punya dua kuliah pada waktu yang sama"
    wsx["A1"].font = Font(bold=True, size=13)
    wsx["A2"] = "Sel = jumlah kelas anggota paket pada blok tersebut. Hijau = 1. MERAH = lebih dari 1 (mahasiswa harus di dua kelas sekaligus)."
    tulis_header(wsx, blok, 2, 3)
    for i, p in enumerate(paket_pakai):
        rr = r0 + i
        wsx.cell(rr, 1, p)
        for j, b in enumerate(blok):
            c = 2 + j
            wsx.cell(rr, c, f"=COUNTIFS(Data_Paket_Blok!$A$2:$A${npk},$A{rr},Data_Paket_Blok!$C$2:$C${npk},{L(c)}$4)")
            ks = isi_paket.get((p, b["blok_id"]))
            if ks:
                wsx.cell(rr, c).comment = Comment("\n".join(sorted(ks)), "bukti_visual", width=380, height=20 + 16 * len(ks))
            wsx.cell(rr, c).number_format = "0;;"
    r1x = r0 + len(paket_pakai) - 1
    rng = f"B{r0}:{L(1 + len(blok))}{r1x}"
    wsx.conditional_formatting.add(rng, FormulaRule(formula=[f"B{r0}>1"], fill=MERAH, font=Font(bold=True, color="9C0006")))
    wsx.conditional_formatting.add(rng, FormulaRule(formula=[f"B{r0}=1"], fill=HIJAU))
    wsx.column_dimensions["A"].width = 16
    wsx.freeze_panes = f"B{r0}"
    wsx["A5"] = "Paket"
    wsx["A5"].font = TEBAL

    # ---------------- H5/H6
    ws56 = wb.create_sheet("H5_H6_Aturan")
    ws56.append(["kelas_id", "prodi", "jenis_mk", "hari", "jam", "ruangan", "aturan ruangan (hari | jenis | prodi)",
                 "aturan blok untuk prodi", "H5 ruangan", "H6 blok"])
    bad5 = {t.split(":")[0] for j, t in pelanggar if j == "H5"}
    bad6 = {t.split(":")[0] for j, t in pelanggar if j == "H6"}
    for kid in sorted(iv):
        k = d.kelas[kid]
        h, m1, m2, ruang, mk, _ = iv[kid]
        a = d.ruang.get(ruang, {})
        izin_blok = sorted({(b_ := d.blok[x]).get("prodi_diizinkan", "semua") for x in cakup[kid]})
        ws56.append([kid, k["prodi"], k["jenis_mk"], h, f"{r_fmt(m1)}-{r_fmt(m2)}", d.nama_ruang.get(ruang, ruang),
                     f"{a.get('hari_diizinkan', '?')} | {a.get('jenis_mk_diizinkan', '?')} | {a.get('prodi_diizinkan', '?')}",
                     ", ".join(izin_blok), "SALAH" if kid in bad5 else "OK", "SALAH" if kid in bad6 else "OK"])
    n56 = len(iv) + 1
    for c, w in zip("ABCDEFGHIJ", (46, 8, 14, 9, 13, 10, 36, 20, 12, 10)):
        ws56.column_dimensions[c].width = w
    ws56.freeze_panes = "B2"
    for col in ("I", "J"):
        ws56.conditional_formatting.add(f"{col}2:{col}{n56}", FormulaRule(formula=[f'{col}2="SALAH"'], fill=MERAH))
        ws56.conditional_formatting.add(f"{col}2:{col}{n56}", FormulaRule(formula=[f'{col}2="OK"'], fill=HIJAU))

    # ---------------- Ringkasan
    ring["A1"] = judul
    ring["A1"].font = Font(bold=True, size=15)
    ring["A2"] = f"Jadwal: {jalur}"
    ring["A3"] = f"Kelas pada data: {len(d.kelas)}  |  dosen: {len(d.dosen_nama)}  |  ruangan: {len(ruang_pakai)}  |  paket: {len(d.paket)}"
    if hanya_catatan:
        ring["A4"] = hanya_catatan
        ring["A4"].font = Font(bold=True, color="9C0006")
    ring["A6"] = "STATUS"
    ring["A6"].font = Font(bold=True, size=13)
    hdr = ["Constraint", "Arti dalam bahasa sederhana", "Cara membuktikan", "Jumlah pelanggaran", "Status"]
    for i, h in enumerate(hdr, 1):
        c = ring.cell(8, i, h)
        c.font = TEBAL
        c.fill = ABU
    n_h1 = f'COUNTIF(H1_Kelas!M2:M{nk},"<>OK")'
    baris = [
        ("H1", "Setiap kelas terjadwal tepat satu kali, dan lamanya sesuai SKS (blok berurutan dalam satu hari dan sesi)",
         "Sheet H1_Kelas: jumlah blok tercakup (rumus) harus sama dengan SKS", f"={n_h1}"),
        ("H2", "Satu ruangan tidak dipakai dua mata kuliah berbeda pada waktu yang sama; jumlah kelas tidak melebihi kapasitas",
         "Sheet H2_Ruangan: tidak ada sel merah", f"=SUM(Data_Ruang_Blok!K2:K{nr})"),
        ("H3", "Seorang dosen tidak mengajar dua sesi berbeda pada waktu yang sama",
         "Sheet H3_Dosen: tidak ada sel merah (sel > 1)", f"=COUNTIF(H3_Dosen!B{r0}:{L(1 + len(blok))}{r1},\">1\")"),
        ("H4", "Mahasiswa satu paket tidak punya dua kuliah pada waktu yang sama",
         "Sheet H4_Paket: tidak ada sel merah (sel > 1)", f"=COUNTIF(H4_Paket!B{r0}:{L(1 + len(blok))}{r1x},\">1\")"),
        ("H5", "Ruangan sesuai aturan (hari, jenis mata kuliah, prodi yang diizinkan)",
         "Sheet H5_H6_Aturan kolom H5: semua OK", f'=COUNTIF(H5_H6_Aturan!I2:I{n56},"SALAH")'),
        ("H6", "Blok waktu yang dipakai diizinkan untuk prodi kelas tersebut",
         "Sheet H5_H6_Aturan kolom H6: semua OK", f'=COUNTIF(H5_H6_Aturan!J2:J{n56},"SALAH")'),
    ]
    for i, (h, arti, cara, rumus) in enumerate(baris):
        rr = 9 + i
        ring.cell(rr, 1, h).font = TEBAL
        ring.cell(rr, 1).alignment = Alignment(vertical="top")
        ring.cell(rr, 4).alignment = Alignment(vertical="top", horizontal="center")
        ring.cell(rr, 5).alignment = Alignment(vertical="top", horizontal="center")
        ring.cell(rr, 2, arti).alignment = Alignment(wrap_text=True, vertical="top")
        ring.cell(rr, 3, cara).alignment = Alignment(wrap_text=True, vertical="top")
        ring.cell(rr, 4, rumus)
        ring.cell(rr, 5, f'=IF(D{rr}=0,"TERPENUHI","DILANGGAR")')
        ring.row_dimensions[rr].height = 44
    ring["A7"] = '=IF(SUM(D9:D14)=0,"SEMUA HARD CONSTRAINT TERPENUHI (0 pelanggaran)","ADA PELANGGARAN: "&SUM(D9:D14))'
    ring["A7"].font = Font(bold=True, size=14)
    ring.merge_cells("A7:E7")
    ring.row_dimensions[7].height = 26
    ring.conditional_formatting.add("A7", FormulaRule(formula=["SUM($D$9:$D$14)=0"], fill=HIJAU))
    ring.conditional_formatting.add("A7", FormulaRule(formula=["SUM($D$9:$D$14)>0"], fill=MERAH))
    ring.conditional_formatting.add("E9:E14", FormulaRule(formula=['E9="TERPENUHI"'], fill=HIJAU))
    ring.conditional_formatting.add("E9:E14", FormulaRule(formula=['E9="DILANGGAR"'], fill=MERAH))
    ring["A16"] = "H1 sampai H4 dihitung oleh rumus Excel atas tabel data mentah (klik sel Jumlah pelanggaran untuk melihat rumusnya). H5 dan H6 diperiksa skrip audit independen (audit_hard.py). Satuan hitung: H1 = kelas, H2/H3/H4 = sel peta yang merah (satu bentrok panjang bisa mencakup beberapa sel), H5/H6 = kelas."
    ring["A17"] = f"Sesi gabungan sah pada jadwal ini (dosen dan ruangan yang sama pada satu sesi untuk beberapa paralel): {stat['pasangan kelas dosen yang sama pada sesi gabungan (sah)']} pasangan kelas. Lihat sheet Legenda."
    ring["A18"] = f'="Blok ruangan kosong seluruh minggu: "&{blok_kosong_total}&" dari "&{blok_tersedia_total}&" blok tersedia (rincian per hari dan ruangan: sheet Blok_Kosong)"'
    cara = ["CARA MEMBACA PETA DALAM 5 LANGKAH",
            "1. Setiap BARIS adalah satu dosen (sheet H3), satu ruangan (H2), atau satu paket mahasiswa (H4).",
            "2. Setiap KOLOM adalah satu jam kuliah (sekitar 50 menit) selama seminggu: hari di atas, lalu kode blok, lalu jam mulai.",
            "3. Sel HIJAU = baris itu ada kegiatan pada jam itu. Sel PUTIH = kosong.",
            "4. Sel MERAH = bentrok (dosen di dua kelas sekaligus, ruangan dipakai dua mata kuliah, atau mahasiswa satu paket punya dua kuliah bersamaan).",
            "5. TIDAK ADA sel merah = tidak ada bentrok. Satu tampilan memeriksa semua baris sekaligus, bukan satu per satu. Arahkan kursor ke sel hijau untuk melihat kelasnya."]
    for i, t in enumerate(cara):
        ring.cell(20 + i, 1, t).font = TEBAL if i == 0 else Font()
    for c, w in zip("ABCDE", (16, 62, 52, 20, 14)):
        ring.column_dimensions[c].width = w
    # ---------------- Legenda
    isi = [
        ("CARA MEMBACA", ""),
        ("Hijau", "Sel terisi dan sah. Pada peta dosen: dosen itu mengajar tepat 1 sesi pada blok itu."),
        ("Merah", "Pelanggaran. Pada peta dosen: dosen berada di dua sesi berbeda sekaligus. Pada peta ruangan: ruangan terlalu penuh atau dipakai dua mata kuliah. Pada peta paket: mahasiswa satu paket punya dua kelas bersamaan."),
        ("Kosong", "Tidak ada kegiatan pada blok itu."),
        ("Komentar sel", "Arahkan kursor ke sel hijau (atau klik kanan: Show/Edit Comment) untuk melihat kelas apa yang menempati sel itu."),
        ("", ""),
        ("KENAPA DUA KELAS PADA JAM YANG SAMA BISA SAH (SESI GABUNGAN)", ""),
        ("Penjelasan", "Beberapa kelas paralel mata kuliah yang sama (mis. Kalkulus 2 kelas B dan D) boleh dijadwalkan pada hari, jam, dan ruangan yang sama sebagai satu pertemuan bersama. "
                       "Dosen pengampu mengajar SEKALI untuk semuanya, jadi bukan bentrok. Dalam peta dosen sesi gabungan dihitung 1 (kolom 'pertama_di_sel' pada Data_Dosen_Blok hanya menghitung satu kali per sesi)."),
        ("Syarat sesi gabungan", "Mata kuliah yang sama, hari yang sama, jam yang sama, ruangan yang sama, dan jumlah paralel tidak melebihi kapasitas gabung mata kuliah dan ruangan. Dua mata kuliah BERBEDA pada ruangan dan jam yang sama selalu pelanggaran (merah)."),
        ("", ""),
        ("ISTILAH", ""),
        ("Blok", "Satu jam kuliah, sekitar 50 menit. Kuliah 3 SKS menempati 3 blok berurutan pada hari yang sama."),
        ("Paket", "Sekelompok mahasiswa yang mengambil kuliah wajib yang sama (prodi, semester, dan kelas yang sama). Seorang mahasiswa tidak bisa berada di dua kelas sekaligus."),
        ("Bentrok dosen (H3)", "Seorang dosen dijadwalkan mengajar dua sesi berbeda pada jam yang sama."),
        ("Bentrok ruangan (H2)", "Satu ruangan dipakai dua mata kuliah berbeda pada jam yang sama, atau jumlah kelasnya melebihi kapasitas."),
        ("Bentrok paket (H4)", "Mahasiswa satu paket punya dua kuliah pada jam yang sama."),
        ("Kelengkapan dan durasi (H1)", "Semua kelas terjadwal tepat sekali dan lamanya sesuai SKS."),
        ("Aturan ruangan dan blok (H5, H6)", "Kelas hanya di ruangan yang diizinkan (hari, jenis mata kuliah, prodi) dan pada jam yang diizinkan untuk prodinya."),
        ("Blok kosong", "Jam pada suatu ruangan yang boleh dipakai tetapi tidak ada kelasnya. Lihat sheet Blok_Kosong."),
        ("", ""),
        ("BATAS BUKTI INI", ""),
        ("Data pengampu", "Bukti menyatakan 'tidak ada bentrok menurut data pengampu (BKD) yang dimasukkan'. Jika data BKD salah, atau satu dosen terdaftar dengan dua nama, bentrok nyata tidak terlihat. Cocokkan data dengan SK pengampu."),
        ("Sumber hitungan", "Peta H1-H4 dihitung oleh rumus Excel dari tabel mentah, bukan oleh algoritma penjadwal. Tabel mentah dibuat dari jadwal keluaran dengan jam nyata, bukan dari struktur internal algoritma."),
        ("Uji sabotase", "Berkas bukti_visual_SABOTASE.xlsx adalah salinan dengan satu pelanggaran sengaja. Buka berkas itu untuk melihat bahwa peta memang menampilkan bentrok sebagai sel merah."),
    ]
    for i, (a, b) in enumerate(isi, 1):
        leg.cell(i, 1, a).font = TEBAL
        leg.cell(i, 2, b).alignment = Alignment(wrap_text=True, vertical="top")
    leg.column_dimensions["A"].width = 26
    leg.column_dimensions["B"].width = 120
    leg.cell(2, 1).fill = HIJAU
    leg.cell(3, 1).fill = MERAH
    for r_ in range(1, len(isi) + 1):
        panjang = len(isi[r_ - 1][1])
        leg.row_dimensions[r_].height = 17 * max(1, -(-panjang // 105))
    # urutan sheet
    order = ["Ringkasan", "Legenda", "H3_Dosen", "H2_Ruangan", "Blok_Kosong", "H4_Paket", "H1_Kelas", "H5_H6_Aturan", "Data_Dosen_Blok", "Data_Ruang_Blok", "Data_Paket_Blok", "Data_Blok"]
    wb._sheets = [wb[n] for n in order]
    wb.save(jalur_out(jalur))
    return pelanggar, iv, cakup, blok, nama, dosen_pakai, ruang_pakai, paket_pakai, isi_dosen, isi_ruang, isi_paket


_OUT = {}


def jalur_out(jalur):
    return _OUT["xlsx"]


def heatmap(png, judul, baris, kolom_blok, nilai, merah_fn, jumlah_merah):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import numpy as np
    from matplotlib.colors import ListedColormap
    n, m = len(baris), len(kolom_blok)
    M = np.zeros((n, m))
    for i, rn in enumerate(baris):
        for j, b in enumerate(kolom_blok):
            v = nilai.get((rn, b["blok_id"]), 0)
            M[i, j] = 0 if v == 0 else (2 if merah_fn(rn, v) else 1)
    fig, ax = plt.subplots(figsize=(max(11, m * 0.2), max(3.2, n * 0.22 + 1.6)))
    ax.imshow(M, aspect="auto", cmap=ListedColormap(["#ffffff", "#8fc98f", "#d9534f"]), vmin=0, vmax=2, interpolation="nearest")
    ax.set_yticks(range(n))
    ax.set_yticklabels(baris, fontsize=6)
    hari_awal = []
    for j, b in enumerate(kolom_blok):
        if j == 0 or kolom_blok[j - 1]["hari"] != b["hari"]:
            hari_awal.append((j, b["hari"]))
    ax.set_xticks([j for j, _ in hari_awal])
    ax.set_xticklabels([h for _, h in hari_awal])
    for j, _ in hari_awal[1:]:
        ax.axvline(j - 0.5, color="black", lw=0.8)
    ax.set_xticks([x - 0.5 for x in range(m + 1)], minor=True)
    ax.set_yticks([x - 0.5 for x in range(n + 1)], minor=True)
    ax.grid(which="minor", color="#dddddd", lw=0.3)
    ax.tick_params(which="minor", length=0)
    ax.set_title(f"{judul}\nHijau = sah, merah = pelanggaran, putih = kosong. Sel merah: {jumlah_merah}", fontsize=10)
    fig.tight_layout()
    fig.savefig(png, dpi=150)
    plt.close(fig)


def sabotase(d, rows, iv):
    """Salinan jadwal dengan satu pelanggaran H3 sengaja: kelas kedua milik dosen yang sama dipindah ke jam kelas pertama."""
    peta = defaultdict(list)
    for kid in sorted(iv):
        for dos in d.kelas_dosen.get(kid, ()):
            peta[dos].append(kid)
    for dos, ks in sorted(peta.items()):
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                k1, k2 = ks[i], ks[j]
                if iv[k1][4] != iv[k2][4] and iv[k1][2] - iv[k1][1] == iv[k2][2] - iv[k2][1]:
                    salin = [dict(r) for r in rows]
                    b1 = next(x for x in rows if x["kelas_id"] == k1)
                    ruang_lain = next((x for x in d.ruang if x not in (iv[k1][3], iv[k2][3])), iv[k2][3])
                    for r in salin:
                        if r["kelas_id"] == k2:
                            r["hari"], r["jam_mulai"], r["jam_selesai"], r["blok_awal"], r["ruangan_id"] = (
                                b1["hari"], b1["jam_mulai"], b1["jam_selesai"], b1["blok_awal"], ruang_lain)
                    return salin, f"{d.dosen_nama.get(dos, dos)}: {k2} dipindah ke jam {k1}"
    return None, ""


def main():
    ap = argparse.ArgumentParser(description="Bukti visual hard constraint (Excel + PNG)")
    ap.add_argument("--jadwal")
    ap.add_argument("--hasil")
    ap.add_argument("--keluar", help="folder induk keluaran (bawaan: data\\pemeriksaan)")
    ap.add_argument("--anonim", action="store_true", help="nama dosen diganti 'Dosen NN'")
    a = ap.parse_args()
    jadwal, hasil = tentukan(a.jadwal, a.hasil)
    d = Data(hasil)
    rows = baca(jadwal)
    out = folder_keluar(jadwal, "bukti_visual", a.keluar)
    _OUT["xlsx"] = out / "bukti_visual.xlsx"
    pel, iv, cakup, blok, nama, dosen_pakai, ruang_pakai, paket_pakai, isi_d, isi_r, isi_p = bangun(
        d, rows, jadwal, "Bukti visual hard constraint", a.anonim)
    print(f"Workbook: {_OUT['xlsx']}   (pelanggaran menurut audit: {len(pel)})")

    # gambar
    # sesi berbeda per dosen/blok: gabungan = sesi sama
    nd = {}
    for (dos, b), ks in isi_d.items():
        nd[(dos, b)] = len({(iv[x][4], iv[x][0], iv[x][1], iv[x][2], iv[x][3]) for x in ks})
    nr = {k: len(v) for k, v in isi_r.items()}
    npk = {k: len(v) for k, v in isi_p.items()}
    capr = {d.nama_ruang.get(r, r): max(1, int(float(v.get("maks_kelas_simultan") or 1))) for r, v in d.ruang.items()}
    mr_dos = sum(1 for v in nd.values() if v > 1)
    mr_ru = sum(1 for (rn, b), ks in isi_r.items() if len(ks) > capr.get(rn, 1) or len({iv[x][4] for x in ks}) > 1)
    mr_pk = sum(1 for v in npk.values() if v > 1)
    heatmap(out / "heatmap_dosen.png", "H3: peta hunian dosen (baris) terhadap blok waktu (kolom)", dosen_pakai, blok, nd, lambda r_, v: v > 1, mr_dos)
    heatmap(out / "heatmap_ruangan.png", "H2: peta hunian ruangan", ruang_pakai, blok, nr, lambda r_, v: v > capr.get(r_, 1), mr_ru)
    heatmap(out / "heatmap_paket.png", "H4: peta hunian paket (mahasiswa satu angkatan)", paket_pakai, blok, npk, lambda r_, v: v > 1, mr_pk)
    print(f"Gambar: heatmap_dosen/ruangan/paket.png (sel merah: dosen {mr_dos}, ruangan {mr_ru}, paket {mr_pk})")

    # sabotase
    salin, ket = sabotase(d, rows, periksa(d, rows)[2])
    if salin:
        _OUT["xlsx"] = out / "bukti_visual_SABOTASE.xlsx"
        pel2, iv2, *_ = bangun(d, salin, jadwal, "CONTOH SABOTASE - bukan jadwal asli", a.anonim,
                               f"Satu pelanggaran sengaja: {ket}. Lihat sel merah pada H3_Dosen dan H2_Ruangan.")
        print(f"Contoh sabotase: {_OUT['xlsx']} ({len(pel2)} pelanggaran menurut audit)")
    pw = peringatan_data(d)
    for w in pw:
        print("PERINGATAN DATA:", w)
    print(f"Hasil di {out}")
    return 0 if not pel else 1


if __name__ == "__main__":
    sys.exit(main())