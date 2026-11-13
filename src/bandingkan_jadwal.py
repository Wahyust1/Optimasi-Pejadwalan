"""
Tahap 5b - Perbandingan jadwal greedy dan GA dari SEGALA ASPEK, lengkap dengan penjelasan tiap aspek dan alasan pemenangnya.

Beda dengan bandingkan.py (yang membandingkan skor terpadu dan suku lunak dari ringkasan CSV), skrip ini membuka JADWALNYA dan
mengukur ulang, termasuk aspek yang TIDAK dioptimalkan langsung oleh algoritma (sebaran hari, jeda mahasiswa, beban harian dosen,
dst). Gunanya dua: (1) membuktikan bahwa keunggulan GA bukan sekadar "pandai memperkecil rumusnya sendiri", dan (2) menjawab pertanyaan
"jadwal mana yang sebaiknya dipakai Kajur, dan mengapa".

Masukan (semua dari data pembersihan yang SAMA):
  --greedy  satu atau lebih folder greedyN (bawaan: terbaru). Memakai jadwal_per_percobaan/ bila ada; bila tidak, hanya jadwal terbaik.
            Beberapa folder boleh dipakai, mis. greedy 30 percobaan dan greedy 5000 percobaan (pembanding waktu setara GA).
  --ga      satu atau lebih folder gaN (bawaan: terbaru tiap LABEL). Memakai jadwal_per_run/ (awal_seedX.csv, akhir_seedX.csv) bila ada.
Keluaran: data/perbandingan_jadwal/perbandinganN/ :
  papan_skor.xlsx              satu lembar per konfigurasi GA: aspek, arti, nilai greedy / GA awal / GA akhir, pemenang, alasan
  papan_skor_<label>.csv       isi yang sama sebagai CSV
  perubahan_kelas_<label>.csv  kelas apa saja yang berpindah dari jadwal awal ke akhir GA, dan dari jadwal greedy terbaik ke GA
  nilai_per_jadwal.csv         semua ukuran untuk tiap jadwal (bahan mentah)
  kesimpulan.txt               ringkasan dalam kalimat: menang di mana, kalah di mana, dan peringatan
  grafik_*.png                 papan skor relatif terhadap greedy, jenis perubahan kelas, komposisi perbaikan

Cara menentukan pemenang tiap aspek (semua aspek: makin kecil makin baik, kecuali dinyatakan lain):
  - Greedy vs GA akhir : dua sampel independen, Mann-Whitney U dua sisi. Pemenang bila p < 0,05; selain itu SERI.
  - GA awal vs GA akhir: data berpasangan (seed yang sama), Wilcoxon dua sisi. Inilah kontribusi GA yang sebenarnya, sebab populasi
                         awal GA adalah yang terbaik dari 20 sampel greedy (perbandingan dengan greedy 1 sampel condong ke GA).
  - Bila satu kelompok kurang dari 5 data (mis. jadwal per run belum disimpan), dipakai selisih median dengan batas seri 2% dan
    diberi tanda "belum diuji".
Contoh:
  python src\\bandingkan_jadwal.py
  python src\\bandingkan_jadwal.py --greedy data\\greedy\\greedy1 data\\greedy\\greedy2 --ga data\\ga\\ga1 data\\ga\\ga2
"""
import argparse
import csv
import re
import statistics as st
import sys
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np

from pemodelan import BATAS_SORE, BOBOT, SUKU, Masalah, baca, baca_jadwal, nomor_berikut, tulis

NAMA_SUKU = [n for n, _ in SUKU]
HK = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat"]
AMBANG_BERAT = 6   # blok (sekitar 5 jam) dalam satu hari: dosen-hari "berat"

# kunci, kelompok, nama, arti (bahasa sederhana), mengapa GA bisa unggul, catatan bila seri/kalah
ASPEK = [
    ("f_ref", "Skor terpadu", "Skor terpadu f_ref",
     "Gabungan keenam suku lunak, tiap suku dibagi rata-ratanya pada greedy lalu dibobot. Angka 17 = setara rata-rata greedy.",
     "GA langsung memperkecil gabungan ini terhadap seluruh jadwal, sedangkan greedy hanya memilih slot terbaik untuk satu kelas pada satu waktu.",
     "Bila seri, GA tidak berhasil memperbaiki jadwal awalnya (lihat catatan konfigurasi)."),
    ("S1_gabung", "Dioptimalkan langsung", "S1 Penggabungan kelas paralel",
     "Berapa banyak kelas paralel yang disatukan dalam satu pertemuan. Makin sedikit makin baik (kelas lebih kecil).",
     "Penggabungan sebagian tak terhindarkan karena kebutuhan blok kelas melebihi slot ruangan; GA menata ulang slot sehingga lebih sedikit paralel yang terpaksa digabung.",
     "Greedy sudah memakai kriteria 'gabung dulu', dan ada batas bawah yang tak bisa dilewati, jadi ruang perbaikannya kecil."),
    ("S2_jeda", "Dioptimalkan langsung", "S2 Jam kosong mahasiswa",
     "Total jam kosong di antara dua kuliah wajib mahasiswa satu paket pada hari yang sama (tunggu antar kuliah).",
     "Jeda baru terlihat setelah seluruh kuliah satu paket ditempatkan. GA bisa menggeser satu kuliah agar menempel dengan kuliah lain, hal yang tak bisa dilihat greedy saat menempatkan kelas satu per satu.",
     "Bobot S2 kecil dibanding S3 dan S5, sehingga GA tidak memprioritaskannya."),
    ("S3_beban", "Dioptimalkan langsung", "S3 Keseimbangan beban dosen antarhari",
     "Seberapa tidak merata jam mengajar seorang dosen antarhari (selisih kuadrat dari rata-rata). Makin kecil makin merata.",
     "Beban dosen bergantung pada semua kelas dosen itu; GA memindahkan kelas antarhari dan langsung melihat dampaknya pada pemerataan.",
     "S3 bernilai ribuan sehingga sulit turun banyak dalam waktu terbatas; perbaikan relatif kecil."),
    ("S4_sore", "Dioptimalkan langsung", "S4 Kuliah wajib sore",
     "Jumlah blok kuliah wajib yang berakhir setelah 16.20 (mengganggu mahasiswa).",
     "GA menukar kuliah wajib dengan kuliah pilihan agar pagi dipakai wajib.",
     "Bobot S4 paling kecil (1), dan slot pagi terbatas sehingga banyak kuliah wajib memang harus ke sore."),
    ("S5_merata", "Dioptimalkan langsung", "S5 Pemerataan kelas antarhari",
     "Seberapa tidak sama banyak kelas Senin sampai Jumat. Makin kecil makin rata.",
     "Ini sifat jadwal secara keseluruhan: jumlah kelas per hari baru diketahui setelah semuanya ditempatkan. GA memindahkan kelas antarhari sampai rata, greedy tidak bisa melihatnya ke depan.",
     "Nilai sudah sangat kecil sehingga selisihnya tidak bermakna."),
    ("S6_utilisasi", "Dioptimalkan langsung", "S6 Keseimbangan pemakaian ruangan",
     "Seberapa tidak seimbang persentase pemakaian antarruangan (ada ruangan penuh, ada yang kosong).",
     "Sama dengan S5: pemakaian ruangan hanya terlihat dari jadwal utuh, dan GA bisa memindahkan kelas antarruangan.",
     "Pemakaian sebagian ditentukan aturan ruangan (hari dan jenis yang diizinkan), jadi ada batas perbaikan."),
    ("P_rentang_hari", "Tidak dioptimalkan langsung", "Selisih hari tersibuk dan tersepi",
     "Jumlah kelas pada hari tersibuk dikurangi hari tersepi. Hari tersibuk membuat kampus padat.",
     "Konsekuensi langsung dari S5; ukuran ini dihitung sendiri dari jadwal, bukan dari rumus GA.",
     "-"),
    ("P_rentang_ruang", "Tidak dioptimalkan langsung", "Selisih ruangan terpenuh dan terkosong (poin persen)",
     "Selisih persentase pemakaian antara ruangan paling padat dan paling longgar.",
     "Konsekuensi dari S6.", "-"),
    ("P_paket_hari_berjeda", "Tidak dioptimalkan langsung", "Hari kuliah mahasiswa yang berjeda",
     "Jumlah (paket, hari) yang punya jam kosong di antara kuliah. Makin sedikit, makin sedikit mahasiswa menunggu.",
     "Konsekuensi dari S2, dihitung sebagai jumlah hari terdampak, bukan total jam.", "-"),
    ("P_beban_harian_maks", "Tidak dioptimalkan langsung", "Beban harian dosen tertinggi (blok)",
     "Jumlah blok terbanyak yang diajar satu dosen dalam satu hari. 1 blok sekitar 50 menit.",
     "Konsekuensi dari S3 (beban merata antarhari berarti tidak ada hari yang terlalu padat).",
     "S3 mengukur pemerataan rata-rata, bukan hari terburuk, jadi nilai ekstrem bisa tetap ada."),
    ("P_dosen_hari_berat", "Tidak dioptimalkan langsung", f"Dosen-hari dengan beban berat (>= {AMBANG_BERAT} blok)",
     f"Berapa kali seorang dosen mengajar {AMBANG_BERAT} blok (sekitar 5 jam) atau lebih dalam satu hari.",
     "Konsekuensi dari S3.", "-"),
    ("P_rata_hari_mengajar", "Tidak dioptimalkan langsung", "Rata-rata hari mengajar per dosen",
     "Rata-rata jumlah hari seorang dosen harus datang ke kampus. Makin kecil makin ringan, tetapi bertentangan dengan S3 yang justru menyebarkan beban ke banyak hari.",
     "Tidak ada.", "Bisa memburuk sebagai harga dari S3 (menyebar beban antarhari). Ini trade-off yang wajar dan dilaporkan apa adanya."),
    ("P_kelas_sore", "Tidak dioptimalkan langsung", "Kelas wajib yang berakhir setelah 16.20",
     "Jumlah kelas (bukan blok) kuliah wajib yang berakhir lewat jam 16.20.",
     "Konsekuensi dari S4.", "-"),
    ("P_kelas_sabtu", "Tidak dioptimalkan langsung", "Kelas pada hari Sabtu",
     "Jumlah kelas yang dijadwalkan hari Sabtu.",
     "Tidak ada pada fungsi tujuan. Dilaporkan agar kelihatan bila GA 'mengorbankan' hal ini.",
     "Tidak termasuk fungsi tujuan, jadi tak ada alasan GA memperbaikinya."),
]
KEY_ASPEK = [a[0] for a in ASPEK]


def fmt(x, d=2):
    if isinstance(x, str):
        return x
    if x is None or (isinstance(x, float) and x != x):
        return "-"
    if abs(x - round(x)) < 1e-9 and abs(x) >= 10:
        return f"{int(round(x))}"
    return f"{x:.{d}f}".replace(".", ",")


def pteks(p):
    if p != p:
        return None
    return "< 0,0001" if p < 0.0001 else f"{p:.4f}".replace(".", ",")


def cliffs(x, y):
    x, y = np.asarray(x, float), np.asarray(y, float)
    if len(x) == 0 or len(y) == 0:
        return float("nan")
    return float(((x[:, None] > y[None, :]).sum() - (x[:, None] < y[None, :]).sum()) / (len(x) * len(y)))


# --------------------------------------------------------------------- pengukuran satu jadwal
def ukur(ms, jd):
    v, lun, _ = ms.validasi(jd, detail=False)
    m = {n: lun[n] for n in NAMA_SUKU}
    m["_pel"] = sum(v.values())
    m["_n"] = len(jd)
    L = Counter(ms.blok[b]["hari"] for b, r in jd.values())
    cnt = [L.get(h, 0) for h in HK]
    m["P_rentang_hari"] = max(cnt) - min(cnt)
    terpakai = defaultdict(set)
    for kid, (b, r) in jd.items():
        for x in ms.seq_pakai(kid, b):
            if ms.blok[x]["hari"] in HK:
                terpakai[r].add(x)
    U = [len(terpakai[r]) / ms._blok_tersedia[r] for r in ms.ruang]
    m["P_rentang_ruang"] = round(100 * (max(U) - min(U)), 2)
    ph = 0
    for p, ks in ms.paket.items():
        pos = defaultdict(set)
        for kid in ks:
            if kid in jd:
                for x in ms.seq_pakai(kid, jd[kid][0]):
                    pos[ms.blok[x]["hari"]].add(ms.posisi[x])
        ph += sum(1 for ps in pos.values() if (max(ps) - min(ps) + 1) - len(ps) > 0)
    m["P_paket_hari_berjeda"] = ph
    mx, berat, hs = 0, 0, []
    for d, ks in ms.dosen_kelas.items():
        sesi = {}
        for k in ks:
            if k in jd:
                sesi[jd[k]] = k
        if not sesi:
            continue
        beban = Counter()
        for (b, r), k in sesi.items():
            for x in ms.seq_pakai(k, b):
                beban[ms.blok[x]["hari"]] += 1
        mx = max(mx, max(beban.values()))
        berat += sum(1 for n in beban.values() if n >= AMBANG_BERAT)
        hs.append(len(beban))
    m["P_beban_harian_maks"] = mx
    m["P_dosen_hari_berat"] = berat
    m["P_rata_hari_mengajar"] = round(st.mean(hs), 3) if hs else 0
    m["P_kelas_sore"] = sum(1 for kid, (b, r) in jd.items()
                            if ms.kelas[kid]["jenis"] == "wajib" and ms.blok[ms.seq_pakai(kid, b)[-1]]["selesai"] > BATAS_SORE)
    m["P_kelas_sabtu"] = sum(1 for b, r in jd.values() if ms.blok[b]["hari"] == "Sabtu")
    return m


# --------------------------------------------------------------------- pemuatan
class Kumpulan:
    def __init__(self, nama, jenis, folder):
        self.nama, self.jenis, self.folder = nama, jenis, folder
        self.jd = {}        # seed -> jadwal
        self.ukur = {}      # seed -> ukuran
        self.waktu = {}     # seed -> detik
        self.generasi = {}  # seed -> generasi
        self.total = 0      # jumlah percobaan/run seluruhnya (termasuk yang tidak sah)

    def sah(self, n_kelas):
        return {s: m for s, m in self.ukur.items() if m["_pel"] == 0 and m["_n"] == n_kelas}


def _status(folder):
    d = {}
    p = next(iter(Path(folder).glob("status*.txt")), None)
    if p:
        for ln in p.read_text(encoding="utf-8").splitlines():
            if ":" in ln:
                a, b = ln.split(":", 1)
                d[a.strip()] = b.strip()
    return d


def muat_greedy(folder):
    folder = Path(folder)
    n = re.search(r"greedy(\d+)$", folder.name).group(1)
    stt = _status(folder)
    k = Kumpulan(f"greedy ({folder.name})", "greedy", folder)
    for r in baca(folder / f"ringkasan_percobaan{n}.csv"):
        k.waktu[int(r["seed"])] = float(r["detik"])
        k.total += 1
    pp = folder / "jadwal_per_percobaan"
    if pp.exists():
        for f in sorted(pp.glob("seed*.csv"), key=lambda p: int(re.search(r"(\d+)", p.stem).group(1))):
            k.jd[int(re.search(r"(\d+)", f.stem).group(1))] = baca_jadwal(f)
    else:
        k.jd[0] = baca_jadwal(folder / f"jadwal_greedy{n}.csv")
        k.nama = f"greedy ({folder.name}): hanya terbaik dari {k.total} percobaan"
    return k, stt.get("SUMBER")


def muat_ga(folder):
    folder = Path(folder)
    n = re.search(r"ga(\d+)$", folder.name).group(1)
    stt = _status(folder)
    label = stt.get("LABEL") or folder.name
    awal = Kumpulan(f"GA {label}: populasi awal", "ga_awal", folder)
    akhir = Kumpulan(f"GA {label}: hasil akhir", "ga_akhir", folder)
    for r in baca(folder / f"ringkasan_ulangan{n}.csv"):
        sd = int(r["seed"])
        akhir.waktu[sd] = float(r["detik"])
        akhir.generasi[sd] = int(float(r["generasi"]))
        akhir.total += 1
    awal.total = akhir.total
    pr = folder / "jadwal_per_run"
    if pr.exists():
        for f in pr.glob("akhir_seed*.csv"):
            sd = int(re.search(r"seed(\d+)", f.stem).group(1))
            akhir.jd[sd] = baca_jadwal(f)
            fa = pr / f"awal_seed{sd}.csv"
            if fa.exists():
                awal.jd[sd] = baca_jadwal(fa)
    else:
        akhir.jd[0] = baca_jadwal(folder / f"jadwal_ga{n}.csv")
    return awal, akhir, label, stt.get("SUMBER")


# --------------------------------------------------------------------- uji dua kelompok
def banding(xs, ys, berpasangan=False):
    """xs = kelompok A, ys = kelompok B (kecil lebih baik). Mengembalikan (pemenang 'A'/'B'/'seri', p, delta, catatan)."""
    xs = [x for x in xs if x == x]
    ys = [y for y in ys if y == y]
    if not xs or not ys:
        return "-", float("nan"), float("nan"), "tidak ada data"
    mx, my = st.median(xs), st.median(ys)
    dl = cliffs(xs, ys)
    n_min = min(len(xs), len(ys))
    if n_min >= 5:
        try:
            from scipy import stats
            if berpasangan and len(xs) == len(ys):
                if all(a == b for a, b in zip(xs, ys)):
                    return "seri", 1.0, 0.0, "identik pada setiap pasangan"
                p = float(stats.wilcoxon(xs, ys, alternative="two-sided").pvalue)
            else:
                p = float(stats.mannwhitneyu(xs, ys, alternative="two-sided").pvalue)
        except Exception:
            p = float("nan")
        if p == p:
            if p < 0.05 and mx != my:
                return ("A" if mx < my else "B"), p, dl, "diuji"
            return "seri", p, dl, "diuji"
    skala = max(abs(mx), abs(my), 1e-9)
    if abs(mx - my) / skala <= 0.02:
        return "seri", float("nan"), dl, "belum diuji (data sedikit)"
    return ("A" if mx < my else "B"), float("nan"), dl, "belum diuji (data sedikit)"


def ringkas(vals):
    vals = [v for v in vals if v == v]
    if not vals:
        return "-"
    if len(vals) == 1:
        return fmt(vals[0])
    return f"{fmt(st.median(vals))} ({fmt(min(vals))}-{fmt(max(vals))})"


def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="Perbandingan jadwal greedy vs GA dari segala aspek")
    ap.add_argument("--greedy", nargs="*", help="folder greedyN (bawaan: terbaru)")
    ap.add_argument("--ga", nargs="*", help="folder gaN (bawaan: terbaru tiap LABEL pada sumber yang sama)")
    ap.add_argument("--bersih", default=str(base / "bersih"))
    ap.add_argument("--keluar", default=str(base / "perbandingan_jadwal"))
    a = ap.parse_args()

    def terbaru(jenis):
        c = [(int(re.fullmatch(rf"{jenis}(\d+)", p.name).group(1)), p) for p in (base / jenis).glob(f"{jenis}[0-9]*")
             if re.fullmatch(rf"{jenis}\d+", p.name) and list(p.glob("status*.txt"))]
        return sorted(c)
    gr_f = [Path(x) for x in a.greedy] if a.greedy else ([terbaru("greedy")[-1][1]] if terbaru("greedy") else [])
    if not gr_f:
        sys.exit("Tidak ada folder greedyN. Jalankan greedy.py dulu atau isi --greedy.")
    if a.ga:
        ga_f = [Path(x) for x in a.ga]
    else:
        per_label = {}
        for n, p in terbaru("ga"):
            per_label[_status(p).get("LABEL") or p.name] = p
        ga_f = list(per_label.values())
    if not ga_f:
        sys.exit("Tidak ada folder gaN. Jalankan ga.py dulu atau isi --ga.")

    greedy_k, sumber = [], None
    for f in gr_f:
        k, sm = muat_greedy(f)
        greedy_k.append(k)
        sumber = sumber or sm
    ga_k = []
    for f in ga_f:
        awal, akhir, label, sm = muat_ga(f)
        ga_k.append((label, awal, akhir))
        if sm and sumber and sm != sumber:
            print(f"PERINGATAN: {f.name} memakai {sm}, greedy memakai {sumber}. Perbandingan hanya adil pada data yang sama.")
    ms = Masalah(Path(a.bersih) / sumber)
    n_kelas = len(ms.kelas)
    print(f"Data: {sumber} ({n_kelas} kelas)")

    # ukur semua jadwal
    semua_k = greedy_k + [x for _, aw, ak in ga_k for x in (aw, ak)]
    for k in semua_k:
        for sd, jd in k.jd.items():
            k.ukur[sd] = ukur(ms, jd)
    # skala f_ref: rata-rata suku pada jadwal greedy sah (kumpulan greedy pertama yang punya jadwal sah)
    acuan = None
    kandidat_skala = sorted(greedy_k, key=lambda k: -len(k.sah(n_kelas)))
    if kandidat_skala and kandidat_skala[0].sah(n_kelas):
        sah = kandidat_skala[0].sah(n_kelas)
        acuan = {n: max(1e-9, st.mean(m[n] for m in sah.values())) for n in NAMA_SUKU}
        if len(sah) < 5:
            print("PERINGATAN: skala f_ref dari kurang dari 5 jadwal greedy; hasil kurang stabil.")
    if acuan is None:
        sys.exit("Tidak ada jadwal greedy yang sah untuk dijadikan skala f_ref.")
    k_bobot = {n: BOBOT[kk] for n, kk in SUKU}
    for k in semua_k:
        for m in k.ukur.values():
            m["f_ref"] = sum(k_bobot[n] * m[n] / acuan[n] for n in NAMA_SUKU)

    induk = Path(a.keluar)
    induk.mkdir(parents=True, exist_ok=True)
    N = nomor_berikut(induk, "perbandingan")
    out = induk / f"perbandingan{N}"
    out.mkdir()

    # nilai mentah per jadwal
    tulis(out / "nilai_per_jadwal.csv", ["kelompok", "seed", "sah"] + KEY_ASPEK,
          [[k.nama, sd, int(m["_pel"] == 0 and m["_n"] == n_kelas)] + [m[x] for x in KEY_ASPEK]
           for k in semua_k for sd, m in sorted(k.ukur.items())])

    def vals(k, key):
        return [m[key] for m in k.sah(n_kelas).values()]

    # greedy gabungan: bila beberapa folder, tiap folder jadi kolom pembanding terpisah
    kes = []   # baris kesimpulan
    ikhtisar = []
    sheets = {}
    perubahan_ringkas = {}
    for label, awal, akhir in ga_k:
        punya_awal = bool(awal.jd)
        sa, sk = awal.sah(n_kelas), akhir.sah(n_kelas)
        # seed berpasangan: yang punya awal dan akhir sah
        pas = sorted(set(sa) & set(sk))
        baris = []
        for key, kel, nama, arti, mengapa, seri in ASPEK:
            row = dict(aspek=nama, kelompok=kel, arti=arti, kunci=key, sumber="")
            for g in greedy_k:
                row[f"nilai:{g.nama}"] = ringkas(vals(g, key))
            row["nilai:GA awal"] = ringkas([sa[s][key] for s in pas]) if punya_awal else "(tidak disimpan)"
            row["nilai:GA akhir"] = ringkas([sk[s][key] for s in sk])
            # pemenang vs greedy pertama dan tiap greedy lain
            pem = []
            for g in greedy_k:
                w, p, dl, cat = banding(vals(g, key), [sk[s][key] for s in sk])
                nm = {"A": "Greedy", "B": "GA", "seri": "Seri", "-": "-"}[w]
                pem.append((g.nama, w, p, dl, cat, nm))
            row["pemenang_greedy"] = "; ".join(f"{x[5]} (vs {x[0]})" for x in pem) if len(pem) > 1 else pem[0][5]
            row["p_greedy"] = pem[0][2]
            row["delta_greedy"] = pem[0][3]
            if punya_awal and pas:
                w2, p2, d2, c2 = banding([sa[s][key] for s in pas], [sk[s][key] for s in pas], berpasangan=True)
                row["pemenang_awal_akhir"] = {"A": "Awal", "B": "GA akhir", "seri": "Seri", "-": "-"}[w2]
                row["p_awal_akhir"] = p2
                row["delta_awal_akhir"] = d2
            else:
                w2, p2, c2 = "-", float("nan"), "tanpa jadwal awal"
                row["pemenang_awal_akhir"] = "-"
                row["p_awal_akhir"] = float("nan")
                row["delta_awal_akhir"] = float("nan")
            # alasan
            tmp = []
            wg = pem[0][1]
            mg, mk_ = (st.median(vals(greedy_k[0], key)) if vals(greedy_k[0], key) else float("nan")), \
                (st.median([sk[s][key] for s in sk]) if sk else float("nan"))
            pa = row["pemenang_awal_akhir"]
            if wg == "B":
                if pa == "GA akhir":
                    row["sumber"] = "GA (perbaikan nyata)"
                    tmp.append(f"GA lebih baik daripada greedy (median {fmt(mk_)} vs {fmt(mg)}), dan perbaikan itu benar berasal dari GA. {mengapa}")
                elif pa == "-":
                    row["sumber"] = "belum dapat dipastikan"
                    tmp.append(f"GA lebih baik daripada greedy (median {fmt(mk_)} vs {fmt(mg)}), tetapi jadwal awal tidak tersimpan sehingga belum bisa dipastikan apakah ini dari GA atau dari pemilihan terbaik-dari-20.")
                else:
                    row["sumber"] = "pemilihan terbaik dari 20 greedy (bukan GA)"
                    tmp.append(f"GA lebih baik daripada greedy tunggal (median {fmt(mk_)} vs {fmt(mg)}), tetapi BUKAN karena GA: jadwal awal GA (terbaik dari 20 greedy) sudah sebaik itu dan GA tidak memperbaikinya lagi pada aspek ini.")
            elif wg == "A":
                tmp.append(f"Greedy lebih baik (median {fmt(mg)} vs {fmt(mk_)}). {seri}")
            elif wg == "seri":
                tmp.append(f"Seri dengan greedy (median {fmt(mg)} vs {fmt(mk_)}). {seri}")
            if row["pemenang_awal_akhir"] == "GA akhir":
                tmp.append("Dibanding jadwal awalnya sendiri, GA memperbaiki aspek ini secara nyata.")
            elif row["pemenang_awal_akhir"] == "Awal":
                tmp.append("Dibanding jadwal awalnya, GA justru memburuk pada aspek ini (harga yang dibayar demi aspek lain).")
            elif row["pemenang_awal_akhir"] == "Seri":
                tmp.append("Dibanding jadwal awalnya, GA tidak mengubah aspek ini.")
            row["alasan"] = " ".join(tmp)
            baris.append(row)
        # ---- aspek operasional
        def lst(k, d):
            return [d[s] for s in sorted(d)]
        gr0 = greedy_k[0]
        op = []
        tot_g, sah_g = gr0.total, len(gr0.sah(n_kelas)) if len(gr0.jd) > 1 else None
        op.append(dict(aspek="Jadwal yang sah (jumlah / total percobaan)", kelompok="Keandalan", kunci="sah",
                       arti="Berapa persen percobaan menghasilkan jadwal tanpa pelanggaran dan semua kelas terjadwal.",
                       **{f"nilai:{g.nama}": f"{len(g.sah(n_kelas))}/{len(g.jd)}" if len(g.jd) > 1 else "(hanya terbaik)" for g in greedy_k},
                       **{"nilai:GA awal": f"{len(sa)}/{len(awal.jd)}" if punya_awal else "-", "nilai:GA akhir": f"{len(sk)}/{len(akhir.jd)}" if len(akhir.jd) > 1 else "(hanya terbaik)"},
                       pemenang_greedy="-", pemenang_awal_akhir="-", p_greedy=float("nan"), delta_greedy=float("nan"),
                       p_awal_akhir=float("nan"), delta_awal_akhir=float("nan"),
                       alasan="GA memulai dari jadwal sah (terbaik dari 20 greedy) dan setiap anak diperbaiki (repair), sehingga hasil akhirnya hampir selalu sah. Greedy tunggal bisa gagal menempatkan beberapa kelas."))
        tw_g = [gr0.waktu[s] for s in gr0.waktu]
        tw_a = [akhir.waktu[s] for s in akhir.waktu]
        w, p, dl, cat = banding(tw_g, tw_a)
        op.append(dict(aspek="Waktu komputasi per jadwal (detik)", kelompok="Biaya", kunci="waktu",
                       arti="Lama menghasilkan satu jadwal pada komputer yang sama. Pada GA belum termasuk membuat populasi awal (sekitar 20 kali greedy).",
                       **{f"nilai:{g.nama}": ringkas(list(g.waktu.values())) for g in greedy_k},
                       **{"nilai:GA awal": "(dibentuk dari 20 greedy)", "nilai:GA akhir": ringkas(tw_a)},
                       pemenang_greedy={"A": "Greedy", "B": "GA", "seri": "Seri", "-": "-"}[w], pemenang_awal_akhir="-",
                       p_greedy=p, delta_greedy=dl, p_awal_akhir=float("nan"), delta_awal_akhir=float("nan"),
                       alasan="Greedy hanya satu kali jalan melewati semua kelas; GA mengulang ribuan generasi, tiap generasi menilai ulang seluruh jadwal. Inilah harga dari kualitas yang lebih baik."))
        op.append(dict(aspek="Iterasi (generasi) GA sampai berhenti", kelompok="Biaya", kunci="generasi",
                       arti="Jumlah generasi GA sebelum berhenti (batas 3000, atau berhenti dini bila tidak membaik 200 generasi).",
                       **{f"nilai:{g.nama}": "-" for g in greedy_k},
                       **{"nilai:GA awal": "-", "nilai:GA akhir": ringkas(list(akhir.generasi.values()))},
                       pemenang_greedy="-", pemenang_awal_akhir="-", p_greedy=float("nan"), delta_greedy=float("nan"),
                       p_awal_akhir=float("nan"), delta_awal_akhir=float("nan"),
                       alasan="Greedy tidak beriterasi. Bila generasi jauh di bawah 3000, GA berhenti dini karena tidak ada perbaikan selama 200 generasi."))
        # stabilitas f_ref
        fg = vals(gr0, "f_ref")
        fa = [sk[s]["f_ref"] for s in sk]
        faw = [sa[s]["f_ref"] for s in pas]
        def sd_(x):
            return st.pstdev(x) if len(x) > 1 else float("nan")
        for key, nama, arti, f in (("sd", "Kestabilan: simpangan baku f_ref", "Seberapa beragam hasil antarpercobaan. Makin kecil, makin bisa diandalkan tanpa mengulang.", sd_),
                                   ("maks", "Kasus terburuk: f_ref tertinggi", "Hasil terburuk dari semua percobaan. Berguna bila hanya satu kali jalan.", max),
                                   ("min", "Kasus terbaik: f_ref terendah", "Hasil terbaik yang pernah dicapai. Inilah jadwal yang akan dipilih sebagai final.", min)):
            def hit(x):
                return f(x) if x else float("nan")
            vg, vaw, vak = hit(fg), (hit(faw) if faw else float("nan")), hit(fa)
            w = "GA" if vak < vg else ("Greedy" if vg < vak else "Seri")
            op.append(dict(aspek=nama, kelompok="Keandalan", kunci=key, arti=arti,
                           **{f"nilai:{g.nama}": (fmt(hit(vals(g, "f_ref"))) if len(g.jd) > 1 else "(hanya terbaik)") for g in greedy_k},
                           **{"nilai:GA awal": fmt(vaw) if punya_awal else "-", "nilai:GA akhir": fmt(vak)},
                           pemenang_greedy=w if len(fg) > 1 and len(fa) > 1 else "-", pemenang_awal_akhir="-",
                           p_greedy=float("nan"), delta_greedy=float("nan"), p_awal_akhir=float("nan"), delta_awal_akhir=float("nan"),
                           alasan="Dibandingkan langsung dari angka, tanpa uji (satu angka per kelompok)."))
        baris += op
        sheets[label] = baris

        # ---- perubahan kelas: representatif (seed dengan f_ref akhir terendah) vs jadwal awal dan vs greedy terbaik
        if sk:
            sd_b = min(sk, key=lambda s: sk[s]["f_ref"])
            jd_a = akhir.jd[sd_b]
            sah_g = gr0.sah(n_kelas)
            sd_g = min(sah_g, key=lambda s: sah_g[s]["f_ref"]) if sah_g else None
            perub = []

            def jenis(j1, j2):
                if j1 == j2:
                    return "tetap"
                (b1, r1), (b2, r2) = j1, j2
                if ms.blok[b1]["hari"] != ms.blok[b2]["hari"]:
                    return "pindah hari"
                if ms.blok[b1]["mulai"] != ms.blok[b2]["mulai"]:
                    return "ganti jam (hari sama)"
                return "ganti ruangan saja"
            pasangan = []
            if sd_b in awal.jd:
                pasangan.append(("GA awal -> GA akhir", awal.jd[sd_b], jd_a))
            if sd_g is not None:
                pasangan.append((f"Greedy terbaik (seed {sd_g}) -> GA akhir [jadwal berbeda sejak awal, bukan sumbangan GA]", gr0.jd[sd_g], jd_a))
            hdr = ["pembanding", "kelas_id", "prodi", "mata_kuliah", "hari_awal", "jam_awal", "ruangan_awal", "hari_akhir", "jam_akhir",
                   "ruangan_akhir", "jenis_perubahan"]
            rows_p, ring = [], {}
            for nm, j1, j2 in pasangan:
                c = Counter()
                for kid in sorted(ms.kelas):
                    if kid not in j1 or kid not in j2:
                        continue
                    (b1, r1), (b2, r2) = j1[kid], j2[kid]
                    jn = jenis(j1[kid], j2[kid])
                    c[jn] += 1
                    rows_p.append([nm, kid, ms.kelas[kid]["prodi"], ms.kelas[kid]["mk"], ms.blok[b1]["hari"], ms.blok[b1]["mulai"],
                                   ms.ruang[r1]["nama"], ms.blok[b2]["hari"], ms.blok[b2]["mulai"], ms.ruang[r2]["nama"], jn])
                ring[nm] = c
            tulis(out / f"perubahan_kelas_{re.sub(r'[^A-Za-z0-9_]', '', label)}.csv", hdr, rows_p)
            perubahan_ringkas[label] = (sd_b, ring)
        # ---- ikhtisar
        menang = [r for r in baris if r["kelompok"] in ("Skor terpadu", "Dioptimalkan langsung", "Tidak dioptimalkan langsung")]
        ikhtisar.append((label, menang, baris))

    # ------------------------------------------------ tulis papan skor (CSV dan XLSX)
    kolom_g = [f"nilai:{g.nama}" for g in greedy_k]
    kepala = ["aspek", "kelompok", "arti"] + kolom_g + ["nilai:GA awal", "nilai:GA akhir", "pemenang_greedy", "p_greedy", "delta_greedy",
                                                      "pemenang_awal_akhir", "p_awal_akhir", "delta_awal_akhir", "sumber", "alasan"]
    for label, baris in sheets.items():
        tulis(out / f"papan_skor_{re.sub(r'[^A-Za-z0-9_]', '', label)}.csv", kepala,
              [[r.get(c, "") if not (isinstance(r.get(c), float) and r.get(c) != r.get(c)) else "" for c in kepala] for r in baris])
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    wb = Workbook()
    ws0 = wb.active
    ws0.title = "Cara membaca"
    teks = [
        ("PAPAN SKOR PERBANDINGAN JADWAL GREEDY DAN GA", True),
        ("", False),
        ("Tiap baris adalah satu aspek jadwal. Kolom 'Arti' menjelaskan maksudnya dalam bahasa biasa. Semua aspek: makin kecil makin baik (kecuali bila tertulis lain).", False),
        ("Nilai ditulis median (terkecil sampai terbesar) dari semua percobaan/run yang sah.", False),
        ("", False),
        ("Dua pembanding yang sengaja dipisah:", True),
        ("1. Greedy vs GA akhir: dua kelompok jadwal yang berdiri sendiri. CATATAN: populasi awal GA adalah yang terbaik dari 20 sampel greedy, sehingga perbandingan ini condong menguntungkan GA.", False),
        ("2. GA awal vs GA akhir (berpasangan, seed yang sama): inilah sumbangan GA yang sebenarnya, karena titik awalnya identik.", False),
        ("", False),
        ("Pemenang ditentukan uji statistik (p < 0,05). Bila tidak berbeda nyata, tertulis Seri. Aspek 'Tidak dioptimalkan langsung' tidak ada di fungsi tujuan, sehingga menunjukkan apakah perbaikan GA nyata atau hanya akal-akalan rumus.", False),
        ("", False),
        ("Warna: hijau = GA unggul, biru = greedy unggul, abu-abu = seri.", False),
    ]
    for i, (t, b) in enumerate(teks, 1):
        ws0.cell(i, 1, t).font = Font(bold=b, size=13 if b and i == 1 else 11)
        ws0.cell(i, 1).alignment = Alignment(wrap_text=True, vertical="top")
    ws0.column_dimensions["A"].width = 130
    hijau, biru, abu = (PatternFill("solid", fgColor=c) for c in ("CFE8CF", "C9DAF0", "E5E5E5"))
    warna = {"GA": hijau, "GA akhir": hijau, "Greedy": biru, "Awal": biru, "Seri": abu}
    for label, baris in sheets.items():
        ws = wb.create_sheet(("Papan skor " + label)[:31])
        ws.append(["Aspek", "Kelompok", "Arti"] + [c.replace("nilai:", "") for c in kolom_g] + ["GA awal", "GA akhir", "Pemenang: greedy vs GA akhir",
                                                                                                "p", "Pemenang: GA awal vs akhir (berpasangan)", "p", "Sumber keunggulan", "Alasan"])
        for c in ws[1]:
            c.font = Font(bold=True)
            c.fill = PatternFill("solid", fgColor="DDDDDD")
            c.alignment = Alignment(wrap_text=True, vertical="top")
        for r in baris:
            ws.append([r["aspek"], r["kelompok"], r["arti"]] + [r.get(c, "") for c in kolom_g] + [r["nilai:GA awal"], r["nilai:GA akhir"],
                                                                                              r["pemenang_greedy"], pteks(r["p_greedy"]),
                                                                                              r["pemenang_awal_akhir"], pteks(r["p_awal_akhir"]),
                                                                                              r.get("sumber", ""), r["alasan"]])
            idx = ws.max_row
            pc = 4 + len(kolom_g) + 2
            for col, key in ((pc, r["pemenang_greedy"]), (pc + 2, r["pemenang_awal_akhir"])):
                first = key.split(" ")[0] if key else ""
                f = warna.get(key) or warna.get(first)
                if f:
                    ws.cell(idx, col).fill = f
            for c in ws[idx]:
                c.alignment = Alignment(wrap_text=True, vertical="top")
        lebar = [34, 20, 52] + [18] * len(kolom_g) + [18, 18, 16, 8, 18, 8, 24, 80]
        for i, w_ in enumerate(lebar, 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w_
        ws.freeze_panes = "D2"
    # lembar perubahan kelas
    for label, (sd_b, ring) in perubahan_ringkas.items():
        ws = wb.create_sheet(("Perubahan " + label)[:31])
        ws.append([f"Perubahan kelas pada run terbaik GA {label} (seed {sd_b})"])
        ws["A1"].font = Font(bold=True, size=12)
        ws.append([])
        ws.append(["Pembanding", "Tetap", "Ganti ruangan saja", "Ganti jam (hari sama)", "Pindah hari", "Total berubah", "% berubah"])
        for c in ws[3]:
            c.font = Font(bold=True)
        for nm, c in ring.items():
            tot = sum(c.values())
            ber = tot - c.get("tetap", 0)
            ws.append([nm, c.get("tetap", 0), c.get("ganti ruangan saja", 0), c.get("ganti jam (hari sama)", 0), c.get("pindah hari", 0), ber,
                       round(100 * ber / tot, 1) if tot else 0])
        ws.append([])
        ws.append(["Daftar kelas lengkap ada di perubahan_kelas_*.csv"])
        for i, w_ in enumerate((46, 10, 20, 24, 14, 14, 12), 1):
            ws.column_dimensions[ws.cell(1, i).column_letter].width = w_
    wb.save(out / "papan_skor.xlsx")

    # ------------------------------------------------ kesimpulan teks
    L = ["KESIMPULAN OTOMATIS PERBANDINGAN JADWAL (dibangun dari papan skor; baca bersama papan_skor.xlsx)", ""]
    L.append(f"Data: {sumber}, {n_kelas} kelas. Jadwal sah dihitung: pelanggaran keras 0 dan semua kelas terjadwal.")
    L.append("Greedy: " + "; ".join(f"{g.nama}: {len(g.sah(n_kelas))} jadwal sah" for g in greedy_k))
    for label, menang, baris in ikhtisar:
        L += ["", f"=== GA {label} ==="]
        sejati = [r["aspek"] for r in menang if r.get("sumber", "").startswith("GA (")]
        pilih = [r["aspek"] for r in menang if r.get("sumber", "").startswith("pemilihan")]
        ragu = [r["aspek"] for r in menang if r.get("sumber", "").startswith("belum")]
        gr_menang = [r["aspek"] for r in menang if r["pemenang_greedy"].startswith("Greedy")]
        seri = [r["aspek"] for r in menang if r["pemenang_greedy"].startswith("Seri")]
        L.append(f"Dari {len(menang)} aspek mutu jadwal: GA unggul atas greedy pada {len(sejati) + len(pilih) + len(ragu)}, seri pada {len(seri)}, greedy unggul pada {len(gr_menang)}.")
        if sejati:
            L.append(f"  Unggul DAN perbaikan itu nyata berasal dari GA ({len(sejati)}): " + "; ".join(sejati))
        if pilih:
            L.append(f"  Unggul tetapi bukan karena GA, melainkan karena populasi awal = terbaik dari 20 greedy ({len(pilih)}): " + "; ".join(pilih))
        if ragu:
            L.append(f"  Unggul, sumbernya belum bisa dipastikan karena jadwal awal tidak tersimpan ({len(ragu)}): " + "; ".join(ragu))
        if gr_menang:
            L.append("  Greedy unggul: " + "; ".join(gr_menang))
        if seri:
            L.append("  Seri: " + "; ".join(seri))
        aw = [r["aspek"] for r in menang if r["pemenang_awal_akhir"] == "GA akhir"]
        aw_buruk = [r["aspek"] for r in menang if r["pemenang_awal_akhir"] == "Awal"]
        L.append(f"Dibanding jadwal awalnya sendiri (berpasangan, sumbangan GA yang sebenarnya): membaik pada {len(aw)} aspek"
                 + (f" ({'; '.join(aw)})" if aw else "") + (f"; memburuk pada: {'; '.join(aw_buruk)}" if aw_buruk else "") + ".")
        for r in baris:
            if r["kunci"] == "waktu":
                L.append(f"Biaya: waktu per jadwal greedy {r['nilai:' + greedy_k[0].nama]} detik vs GA {r['nilai:GA akhir']} detik.")
        if not any(r["nilai:GA awal"] != "(tidak disimpan)" for r in baris):
            L.append("PERINGATAN: jadwal awal GA tidak tersimpan (run lama). Jalankan ulang ga.py agar jadwal_per_run/ terbentuk; tanpa itu sumbangan GA belum bisa diuji berpasangan.")
        if label in perubahan_ringkas:
            sd_b, ring = perubahan_ringkas[label]
            for nm, c in ring.items():
                tot = sum(c.values())
                ber = tot - c.get("tetap", 0)
                L.append(f"Kelas yang berubah, {nm}: {ber} dari {tot} ({100 * ber / tot:.0f}%); " + ", ".join(f"{k} {v}" for k, v in c.items() if k != "tetap"))
    L += ["", "KETERBATASAN",
          "- Bobot S1-S6 masih titik awal, belum dikalibrasi Kajur; keunggulan di suku lunak berlaku untuk bobot ini.",
          "- Aspek 'Tidak dioptimalkan langsung' dipilih penulis kode sebagai pemeriksa silang; bukan standar baku.",
          "- Perbandingan greedy vs GA akhir condong menguntungkan GA (populasi awal = terbaik dari 20 greedy); gunakan perbandingan berpasangan sebagai sumbangan GA.",
          "- Bila GA mutasi 10% (rancangan Bab 3) tidak membaik dari jadwal awalnya, jadwal itu pada dasarnya hasil greedy; keunggulan GA hanya terbukti untuk konfigurasi yang benar-benar membaik.",
          "- Perbandingan waktu hanya sahih bila dijalankan pada komputer dan jumlah proses yang sama."]
    (out / "kesimpulan.txt").write_text("\n".join(L) + "\n", encoding="utf-8")
    print("\n".join(L))

    # ------------------------------------------------ grafik
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
    except ImportError:
        print("matplotlib tidak ada; grafik dilewati")
        print(f"Hasil di {out}")
        return 0
    asp_g = [x for x in ASPEK if x[0] != "P_kelas_sabtu"]
    gr0 = greedy_k[0]
    for label, awal, akhir in ga_k:
        sa, sk = awal.sah(n_kelas), akhir.sah(n_kelas)
        pas = sorted(set(sa) & set(sk))
        namas, rel_aw, rel_ak = [], [], []
        for key, kel, nama, *_ in asp_g:
            vg = vals(gr0, key)
            if not vg or st.median(vg) == 0:
                continue
            m0 = st.median(vg)
            namas.append(nama)
            rel_aw.append(100 * st.median([sa[s][key] for s in pas]) / m0 if pas else float("nan"))
            rel_ak.append(100 * st.median([sk[s][key] for s in sk]) / m0 if sk else float("nan"))
        if namas:
            fig, ax = plt.subplots(figsize=(11, 0.55 * len(namas) + 2))
            y = np.arange(len(namas))
            ax.barh(y - 0.2, rel_aw, 0.38, color="#9a9a9a", label="GA populasi awal")
            ax.barh(y + 0.2, rel_ak, 0.38, color="#2f6fb0", label="GA hasil akhir")
            ax.axvline(100, color="black", lw=1)
            ax.set_xlim(0, max(120, np.nanmax(rel_aw + rel_ak) * 1.08))
            ax.text(101, len(namas) - 0.4, "greedy = 100%", fontsize=8, va="bottom")
            ax.set_yticks(y)
            ax.set_yticklabels(namas, fontsize=8)
            ax.invert_yaxis()
            ax.set_xlabel("Median aspek sebagai % dari median greedy (makin ke kiri makin baik)")
            ax.set_title(f"Papan skor relatif terhadap greedy: GA {label}")
            ax.legend(fontsize=8, loc="lower right")
            fig.tight_layout()
            fig.savefig(out / f"grafik_papan_skor_{re.sub(r'[^A-Za-z0-9_]', '', label)}.png", dpi=150)
            plt.close(fig)
        if label in perubahan_ringkas and perubahan_ringkas[label][1]:
            ring = perubahan_ringkas[label][1]
            fig, ax = plt.subplots(figsize=(9, 1.2 * len(ring) + 1.8))
            jen = ["ganti ruangan saja", "ganti jam (hari sama)", "pindah hari"]
            col = ["#8fc98f", "#f0c36d", "#d9534f"]
            for i, (nm, c) in enumerate(ring.items()):
                kiri = 0
                for jn, cc in zip(jen, col):
                    ax.barh(i, c.get(jn, 0), left=kiri, color=cc, label=jn if i == 0 else None)
                    if c.get(jn, 0):
                        ax.text(kiri + c.get(jn, 0) / 2, i, str(c.get(jn, 0)), ha="center", va="center", fontsize=8)
                    kiri += c.get(jn, 0)
            ax.set_yticks(range(len(ring)))
            ax.set_yticklabels(list(ring), fontsize=8)
            ax.set_xlabel("Jumlah kelas yang berubah (dari seluruh kelas)")
            ax.set_title(f"Jenis perubahan kelas, GA {label} (run terbaik)")
            ax.legend(fontsize=8)
            fig.tight_layout()
            fig.savefig(out / f"grafik_perubahan_kelas_{re.sub(r'[^A-Za-z0-9_]', '', label)}.png", dpi=150)
            plt.close(fig)
        # komposisi perbaikan f_ref (awal -> akhir), tiap suku
        if pas:
            sh = {}
            for nn, kk in SUKU:
                sh[nn] = st.mean(k_bobot[nn] * (sa[s][nn] - sk[s][nn]) / acuan[nn] for s in pas)
            tot = sum(sh.values())
            if abs(tot) > 1e-9:
                fig, ax = plt.subplots(figsize=(8, 3.6))
                ks = list(sh)
                ax.barh(ks, [100 * sh[x] / tot for x in ks], color="#2f6fb0")
                ax.invert_yaxis()
                ax.axvline(0, color="black", lw=0.8)
                ax.set_xlabel("% dari total penurunan f_ref (rata-rata run), awal ke akhir")
                ax.set_title(f"Dari suku mana perbaikan GA berasal: {label}")
                fig.tight_layout()
                fig.savefig(out / f"grafik_komposisi_perbaikan_{re.sub(r'[^A-Za-z0-9_]', '', label)}.png", dpi=150)
                plt.close(fig)
    print(f"Hasil di {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())