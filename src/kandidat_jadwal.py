"""
Katalog kandidat jadwal: memisahkan jadwal terbaik tiap KONFIGURASI (greedy heuristik, greedy penalti, GA mutasi 10%, GA mutasi 1%,
GA tanpa repair, dst), mengukurnya dengan skala yang sama, menyusun peringkat dengan aturan yang tertulis, dan merekomendasikan
jadwal yang dipakai.

Mengapa ada: tiap eksperimen menulis ke folder bernomor sendiri (greedyN, gaN), tetapi belum ada satu tempat yang memuat
"jadwal terbaik dari tiap konfigurasi" berdampingan beserta ALASAN memilih salah satunya. Skrip ini menjadi tempat itu.

Aturan pemilihan (berurutan; ditetapkan sebelum melihat hasil):
  K1 Syarat mutlak  : jadwal sah (H1-H6 = 0, semua kelas terjadwal) dan lolos audit independen. Yang tidak sah tidak diperingkat.
  K2 Kualitas       : skor terpadu f_ref terendah (skala acuan sama untuk semua kandidat).
  K3 Bukti GA       : kandidat GA hanya boleh disebut "hasil GA" bila terbukti membaik dari jadwal awalnya sendiri
                      (Wilcoxon berpasangan p < 0,05 dan median akhir < median awal). Bila tidak, ia dicatat sebagai
                      "setara hasil greedy (tanpa bukti sumbangan GA)". Peringkatnya tetap menurut K2.
  K4 Pemeriksa silang: bila selisih f_ref dua kandidat teratas <= 2%, yang menang pada lebih banyak dari 8 aspek pemeriksa silang
                      dipilih. Aspek yang lebih buruk dari greedy terbaik dilaporkan sebagai peringatan (trade-off).
  K5 Informasi saja : waktu komputasi, kestabilan (simpangan baku f_ref antarulangan), proporsi run sah. Tidak menentukan peringkat.

Keluaran: data\\jadwal\\kandidat\\kandidatN\\
  katalog_kandidat.xlsx / .csv   satu baris per konfigurasi, urut peringkat
  rekomendasi.txt                aturan, peringkat, jadwal yang direkomendasikan, peringatan, dan perintah finalisasi
  <kandidat>\\jadwal.csv         jadwal terbaik konfigurasi itu (+ asal.txt dan status_asal.txt)

Contoh:
  python src\\kandidat_jadwal.py
  python src\\kandidat_jadwal.py --greedy data\\greedy\\greedy1 data\\greedy\\greedy8 --ga data\\ga\\ga12 data\\ga\\ga13 data\\ga\\ga14
"""
import argparse
import csv
import re
import shutil
import statistics as st
import sys
from pathlib import Path

from audit import Data, baca as baca_csv, periksa
from bandingkan_jadwal import (ASPEK, KEY_ASPEK, NAMA_SUKU, _status, fmt, muat_ga, muat_greedy, ukur)
from pemodelan import BOBOT, SUKU, Masalah, baca_jadwal, nomor_berikut, tulis, tulis_jadwal

CEK_SILANG = [a[0] for a in ASPEK if a[1] == "Tidak dioptimalkan langsung"]
BATAS_SERI = 0.02


def _param(folder):
    d = {}
    for p in Path(folder).glob("parameter*.csv"):
        with open(p, encoding="utf-8-sig", newline="") as f:
            for r in csv.DictReader(f):
                d[r["parameter"]] = r["nilai"]
        break
    return d


def _kriteria_greedy(folder):
    stt = _status(folder)
    if stt.get("KRITERIA"):
        return stt["KRITERIA"], stt.get("PERCOBAAN", "?")
    for p in Path(folder).glob("analisis_greedy*.txt"):
        for ln in p.read_text(encoding="utf-8").splitlines():
            m = re.match(r"Kriteria:\s*(\w+)", ln)
            if m:
                return m.group(1), "?"
    return "heuristik", "?"


def _aman(s):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", s).strip("_")


def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="Katalog kandidat jadwal per konfigurasi dan rekomendasi jadwal yang dipakai")
    ap.add_argument("--greedy", nargs="*", default=[], help="folder greedyN (bawaan: terbaru tiap kriteria)")
    ap.add_argument("--ga", nargs="*", default=[], help="folder gaN (bawaan: terbaru tiap LABEL)")
    ap.add_argument("--bersih", default=str(base / "bersih"))
    ap.add_argument("--keluar", default=str(base / "jadwal" / "kandidat"))
    a = ap.parse_args()

    def semua(jenis):
        c = []
        d = base / jenis
        if d.exists():
            for p in d.iterdir():
                m = re.fullmatch(rf"{jenis}(\d+)", p.name)
                if m and list(p.glob("status*.txt")):
                    c.append((int(m.group(1)), p))
        return sorted(c)

    gr = [Path(x) for x in a.greedy]
    if not gr:
        per = {}
        for _, p in semua("greedy"):
            per[(_kriteria_greedy(p)[0], muat_greedy(p)[0].total)] = p   # terbaru untuk tiap (kriteria, jumlah percobaan)
        gr = list(per.values())
    gaf = [Path(x) for x in a.ga]
    if not gaf:
        per = {}
        for _, p in semua("ga"):
            per[_status(p).get("LABEL") or p.name] = p
        gaf = list(per.values())
    if not gr or not gaf:
        sys.exit("Perlu minimal satu folder greedyN dan satu folder gaN (isi --greedy dan --ga).")

    # ---- muat semua kelompok
    sumber = None
    kel = []   # dict: nama, jenis, folder, kum / (awal, akhir), file jadwal terpilih
    for f in gr:
        k, sm = muat_greedy(f)
        sumber = sumber or sm
        krit, n_perc = _kriteria_greedy(f)
        nama = f"greedy-{krit}-{k.total}"
        kel.append(dict(nama=nama, jenis="greedy", folder=f, akhir=k, awal=None,
                        konfig=f"kriteria {krit}, {k.total} percobaan",
                        berkas=next(iter(f.glob("jadwal_greedy*.csv")), None)))
    for f in gaf:
        aw, ak, label, sm = muat_ga(f)
        if sm and sumber and sm != sumber:
            print(f"PERINGATAN: {f.name} memakai {sm}, greedy memakai {sumber}. Kandidat hanya adil pada data yang sama.")
        sumber = sumber or sm
        p = _param(f)
        konfig = (f"mutasi {p.get('persen_mutasi', '?')}%, repair {'ya' if p.get('repair', 'True') == 'True' else 'tidak'}, "
                  f"populasi awal {p.get('awal', 'greedy')}, {ak.total} ulangan")
        kel.append(dict(nama=f"GA-{label}", jenis="ga", folder=f, akhir=ak, awal=aw, konfig=konfig,
                        berkas=next(iter(f.glob("jadwal_ga*.csv")), None)))
    ms = Masalah(Path(a.bersih) / sumber)
    n_kelas = len(ms.kelas)
    d_audit = Data(Path(a.bersih) / sumber)
    print(f"Data: {sumber} ({n_kelas} kelas); {len(kel)} konfigurasi")

    # ---- ukur semuanya
    for g in kel:
        for kum in (g["akhir"], g["awal"]):
            if kum is None:
                continue
            for sd, jd in kum.jd.items():
                kum.ukur[sd] = ukur(ms, jd)
        jd_t = baca_jadwal(g["berkas"])
        g["jd_terpilih"] = jd_t
        g["ukur_terpilih"] = ukur(ms, jd_t)

    # ---- skala f_ref: kumpulan greedy dengan jadwal sah terbanyak (sama dengan bandingkan_jadwal.py)
    kum_greedy = [g["akhir"] for g in kel if g["jenis"] == "greedy"]
    kum_greedy.sort(key=lambda k: (-len(k.sah(n_kelas)), k.total))   # seri: percobaan paling sedikit (greedy dasar Bab III)
    if not kum_greedy or not kum_greedy[0].sah(n_kelas):
        sys.exit("Tidak ada jadwal greedy yang sah untuk skala f_ref.")
    acuan = {n: max(1e-9, st.mean(m[n] for m in kum_greedy[0].sah(n_kelas).values())) for n in NAMA_SUKU}
    kb = {n: BOBOT[kk] for n, kk in SUKU}

    def fref(m):
        return sum(kb[n] * m[n] / acuan[n] for n in NAMA_SUKU)

    for g in kel:
        for kum in (g["akhir"], g["awal"]):
            if kum is None:
                continue
            for m in kum.ukur.values():
                m["f_ref"] = fref(m)
        g["ukur_terpilih"]["f_ref"] = fref(g["ukur_terpilih"])

    # ---- audit independen tiap jadwal terpilih
    for g in kel:
        rows = baca_csv(g["berkas"])
        pel, _, _ = periksa(d_audit, rows)
        g["audit_pel"] = len(pel)
        m = g["ukur_terpilih"]
        g["sah"] = m["_pel"] == 0 and m["_n"] == n_kelas and g["audit_pel"] == 0

    # ---- statistik grup + bukti GA
    try:
        from scipy import stats as sp
    except ImportError:
        sp = None
    for g in kel:
        sah = g["akhir"].sah(n_kelas)
        fr = [m["f_ref"] for m in sah.values()]
        g["n_sah"], g["n_total"] = len(sah), g["akhir"].total
        g["f_med"] = st.median(fr) if fr else float("nan")
        g["f_sd"] = st.pstdev(fr) if len(fr) > 1 else float("nan")
        g["f_buruk"] = max(fr) if fr else float("nan")
        g["waktu_med"] = st.median(g["akhir"].waktu.values()) if g["akhir"].waktu else float("nan")
        g["gen_med"] = st.median(g["akhir"].generasi.values()) if g["akhir"].generasi else float("nan")
        g["bukti"], g["p_bukti"], g["f_awal_med"] = "-", float("nan"), float("nan")
        if g["jenis"] == "ga":
            sa = g["awal"].sah(n_kelas)
            pas = sorted(set(sa) & set(sah))
            if len(pas) >= 5 and sp is not None:
                xa = [sa[s]["f_ref"] for s in pas]
                xk = [sah[s]["f_ref"] for s in pas]
                g["f_awal_med"] = st.median(xa)
                try:
                    p = float(sp.wilcoxon(xa, xk, alternative="two-sided").pvalue)
                except ValueError:       # semua selisih nol: GA tidak mengubah apa pun
                    p = 1.0
                g["p_bukti"] = p
                ok = p < 0.05 and st.median(xk) < st.median(xa)
                g["bukti"] = "GA terbukti membaik dari jadwal awalnya" if ok else "setara hasil greedy (tanpa bukti sumbangan GA)"
            else:
                g["bukti"] = "belum dapat dinilai (jadwal awal tidak tersimpan atau ulangan < 5)"

    # ---- peringkat (K1, K2, K4)
    layak = [g for g in kel if g["sah"]]
    layak.sort(key=lambda g: g["ukur_terpilih"]["f_ref"])
    if len(layak) >= 2:
        a0, a1 = layak[0], layak[1]
        f0, f1 = a0["ukur_terpilih"]["f_ref"], a1["ukur_terpilih"]["f_ref"]
        if f1 > 0 and (f1 - f0) / f1 <= BATAS_SERI:
            def skor_silang(g, lawan):
                return sum(1 for k in CEK_SILANG if g["ukur_terpilih"][k] < lawan["ukur_terpilih"][k])
            if skor_silang(a1, a0) > skor_silang(a0, a1):
                layak[0], layak[1] = a1, a0
                layak[0]["catatan_k4"] = "menang tipis atas peringkat sebelumnya pada pemeriksa silang (K4)"
    for i, g in enumerate(layak, 1):
        g["peringkat"] = i
    for g in kel:
        g.setdefault("peringkat", "")
    terbaik = layak[0] if layak else None
    greedy_terbaik = min((g for g in layak if g["jenis"] == "greedy"), key=lambda g: g["ukur_terpilih"]["f_ref"], default=None)

    # ---- keluaran
    induk = Path(a.keluar)
    induk.mkdir(parents=True, exist_ok=True)
    N = nomor_berikut(induk, "kandidat")
    out = induk / f"kandidat{N}"
    out.mkdir()
    kolom = ["peringkat", "kandidat", "algoritma", "konfigurasi", "folder", "sah", "pelanggaran_audit", "f_ref"] + NAMA_SUKU + CEK_SILANG + [
        "f_ref_median_grup", "f_ref_simpangan_baku", "f_ref_terburuk", "run_sah", "waktu_median_detik", "generasi_median",
        "f_ref_awal_median", "p_bukti_ga", "bukti_ga", "catatan"]
    baris = []
    for g in sorted(kel, key=lambda g: (g["peringkat"] == "", g["peringkat"] if g["peringkat"] != "" else 0)):
        m = g["ukur_terpilih"]
        baris.append([g["peringkat"], g["nama"], g["jenis"], g["konfig"], g["folder"].name, "ya" if g["sah"] else "TIDAK", g["audit_pel"],
                      fmt(m["f_ref"])] + [fmt(m[n]) for n in NAMA_SUKU] + [fmt(m[k]) for k in CEK_SILANG] + [
                      fmt(g["f_med"]), fmt(g["f_sd"]), fmt(g["f_buruk"]), f"{g['n_sah']}/{g['n_total']}", fmt(g["waktu_med"], 1),
                      fmt(g["gen_med"], 0), fmt(g["f_awal_med"]), "" if g["p_bukti"] != g["p_bukti"] else f"{g['p_bukti']:.4g}",
                      g["bukti"], g.get("catatan_k4", "")])
        d = out / _aman(g["nama"] + "_" + g["folder"].name)
        d.mkdir()
        tulis_jadwal(ms, g["jd_terpilih"], d / "jadwal.csv")
        for p in Path(g["folder"]).glob("status*.txt"):
            shutil.copy2(p, d / "status_asal.txt")
            break
        (d / "asal.txt").write_text(
            f"Kandidat: {g['nama']}\nFolder asal: {g['folder']}\nKonfigurasi: {g['konfig']}\nBerkas terpilih algoritma: {g['berkas'].name}\n"
            f"f_ref: {fmt(g['ukur_terpilih']['f_ref'])}  sah: {g['sah']}  pelanggaran audit: {g['audit_pel']}\n", encoding="utf-8")
    tulis(out / "katalog_kandidat.csv", kolom, baris)

    # xlsx
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Alignment, Font, PatternFill
        wb = Workbook()
        ws = wb.active
        ws.title = "Kandidat"
        ws.append(kolom)
        for r in baris:
            ws.append(r)
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="E8E8E8")
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for i, w in enumerate([9, 22, 10, 40, 10, 6, 9, 8] + [9] * (len(kolom) - 8), 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        if baris and baris[0][0] == 1:
            for c in ws[2]:
                c.fill = PatternFill("solid", fgColor="D7EBD7")
        ws.freeze_panes = "C2"
        wk = wb.create_sheet("Aturan pemilihan")
        for ln in __doc__.split("Aturan pemilihan")[1].split("Keluaran:")[0].strip().splitlines():
            wk.append([ln.rstrip()])
        wk.column_dimensions["A"].width = 130
        wa = wb.create_sheet("Arti aspek")
        wa.append(["kolom", "kelompok", "nama", "arti"])
        for key, kelmp, nama, arti, *_ in ASPEK:
            wa.append([key, kelmp, nama, arti])
        wb.save(out / "katalog_kandidat.xlsx")
    except ImportError:
        pass

    # rekomendasi
    L = ["REKOMENDASI JADWAL", f"Data: {sumber} ({n_kelas} kelas)", "",
         "ATURAN PEMILIHAN (berurutan, ditetapkan sebelum hasil dilihat)",
         "  K1 Jadwal harus sah (H1-H6 = 0, semua kelas terjadwal) dan lolos audit independen.",
         "  K2 Di antara yang sah, dipilih skor terpadu f_ref terendah (skala acuan sama untuk semua kandidat).",
         "  K3 Kandidat GA hanya disebut hasil GA bila terbukti membaik dari jadwal awalnya sendiri (Wilcoxon berpasangan p < 0,05).",
         f"  K4 Bila selisih f_ref dua teratas <= {int(BATAS_SERI * 100)}%, yang unggul pada lebih banyak aspek pemeriksa silang dipilih.",
         "  K5 Waktu, kestabilan, dan proporsi run sah dilaporkan tetapi tidak menentukan peringkat.", "",
         "PERINGKAT"]
    for g in layak:
        L.append(f"  {g['peringkat']}. {g['nama']} ({g['folder'].name}; {g['konfig']}): f_ref {fmt(g['ukur_terpilih']['f_ref'])}; "
                 f"median grup {fmt(g['f_med'])}; waktu median {fmt(g['waktu_med'], 1)} detik; {g['bukti'] if g['jenis'] == 'ga' else 'greedy'}")
    tak = [g for g in kel if not g["sah"]]
    for g in tak:
        L.append(f"  (tidak diperingkat) {g['nama']}: jadwal terpilih tidak sah (pelanggaran validator {g['ukur_terpilih']['_pel']}, audit {g['audit_pel']})")
    L.append("")
    if terbaik:
        mt = terbaik["ukur_terpilih"]
        L += ["YANG DIREKOMENDASIKAN",
              f"  {terbaik['nama']} dari folder {terbaik['folder'].name} (f_ref {fmt(mt['f_ref'])}).",
              f"  Bukti: {terbaik['bukti'] if terbaik['jenis'] == 'ga' else 'greedy murni'}."]
        if terbaik.get("catatan_k4"):
            L.append(f"  Catatan: {terbaik['catatan_k4']}.")
        if greedy_terbaik and greedy_terbaik is not terbaik:
            mg = greedy_terbaik["ukur_terpilih"]
            L.append(f"  Dibanding greedy terbaik ({greedy_terbaik['nama']}, f_ref {fmt(mg['f_ref'])}): lebih baik {fmt(100 * (mg['f_ref'] - mt['f_ref']) / mg['f_ref'], 1)}%.")
            buruk = [nm for key, _, nm, *_ in ASPEK if key in CEK_SILANG and mt[key] > mg[key]]
            if buruk:
                L.append("  PERINGATAN (trade-off, lebih buruk daripada greedy terbaik): " + "; ".join(buruk) + ".")
        if terbaik["jenis"] == "ga" and not terbaik["bukti"].startswith("GA terbukti"):
            L.append("  PERINGATAN: jadwal ini tidak dapat disebut hasil optimasi GA (lihat K3).")
        pj = next(iter((out / _aman(terbaik["nama"] + "_" + terbaik["folder"].name)).glob("jadwal.csv")))
        L += ["", "LANGKAH BERIKUTNYA", f'  python src\\finalisasi.py --berkas "{pj}" --catatan "kandidat {terbaik["nama"]}, peringkat 1"']
    else:
        L.append("Tidak ada kandidat yang sah.")
    (out / "rekomendasi.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))
    print(f"\nHasil di {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())