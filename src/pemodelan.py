"""
Tahap 2 - Pemodelan masalah: pembangun domain, validator, dan uji kelayakan awal.

Membaca berkas bersih dari tahap pembersihan data (data/bersih/hasilN/) dan:
  1. membangun domain Dom_k tiap kelas (blok awal + ruangan yang mungkin);
     H1 (blok berurutan, sehari, satu sesi), H5 (aturan ruangan), dan H6 (blok per prodi)
     dijamin lewat domain;
  2. memvalidasi sebuah jadwal terhadap H1-H6 dan menghitung S1-S4;
  (Heuristik greedy ada di greedy.py dan GA di ga.py; keduanya terpisah dan memakai model ini.)

Contoh:
  python src/pemodelan.py                         # run terbaru: ringkasan domain
  python src/pemodelan.py --hasil hasil2          # pilih run tertentu
  python src/pemodelan.py --validasi jadwal.csv   # validasi jadwal buatan tangan (kolom: kelas_id,blok_awal,ruangan_id)

Keluaran ditulis ke data/jadwal/jadwalN/ (nomor bertambah, tidak menimpa).
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path

# Bobot usulan awal (padanan CB-CTT, lihat dokumen pemodelan). BUKAN hasil jurnal; ubah sesuai prioritas Kajur.
# Suku lunak. S1-S4: model baru (bobot awal improvisasi). S5-S6: soft constraint Bab 3 (Even Distribution w1=5,
# Room Utilization Balance w2=3), rumus Bab 3: S = sum |nilai - rata-rata|.
BOBOT = dict(gabung=1, jeda=2, beban=5, sore=1, merata=5, utilisasi=3)
SUKU = [("S1_gabung", "gabung"), ("S2_jeda", "jeda"), ("S3_beban", "beban"), ("S4_sore", "sore"),
        ("S5_merata", "merata"), ("S6_utilisasi", "utilisasi")]
BATAS_SORE = "16:20"  # blok yang berakhir setelah jam ini dihitung "sore" (S4)


# ------------------------------------------------------------------ util
def baca(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return [{k.strip(): (v or "").strip() for k, v in row.items() if k} for row in csv.DictReader(f)]


def tulis(path, header, rows):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(header)
        w.writerows(rows)


def himpunan(v):
    """'semua' atau kosong -> None (tak dibatasi); selain itu himpunan nilai huruf kecil."""
    v = (v or "").strip()
    if not v or v.casefold() == "semua":
        return None
    return {x.strip().casefold() for x in re.split(r"[;,|]", v) if x.strip()}


def int_aman(v, default=0):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


# --------------------------------------------------------------- masalah
class Masalah:
    def __init__(self, folder):
        folder = Path(folder)
        m = re.search(r"hasil(\d+)$", folder.name)
        if not m:
            raise SystemExit(f"Nama folder harus berbentuk hasilN: {folder}")
        self.nomor_hasil = int(m.group(1))
        self.folder = folder

        def rd(stem):
            p = folder / f"{stem}{self.nomor_hasil}.csv"
            if not p.exists():
                raise SystemExit(f"Berkas tidak ditemukan: {p}")
            return baca(p)

        # kelas
        self.kelas = {}
        for r in rd("kelas"):
            self.kelas[r["kelas_id"]] = dict(prodi=r["prodi"], mk=r["mk_id"], jenis=(r["jenis_mk"] or "").casefold(),
                                             sks=int_aman(r["durasi_blok"] or r["sks"]), semester=r["semester"],
                                             label=r["label_paralel"], nama=r["nama_kelas_mentah"])
        # mata kuliah: batas gabung per mata kuliah (kapasitas_gabung; bawaan = jumlah paralel)
        self.cap_mk = {r["mk_id"]: max(1, int_aman(r["kapasitas_gabung"], 1)) for r in rd("mata_kuliah")}
        # dosen pengampu
        self.dosen_kelas = defaultdict(set)
        self.kelas_dosen = defaultdict(set)
        for r in rd("kelas_dosen"):
            self.dosen_kelas[r["dosen_id"]].add(r["kelas_id"])
            self.kelas_dosen[r["kelas_id"]].add(r["dosen_id"])
        self.nama_dosen = {r["dosen_id"]: r["nama_baku"] for r in rd("dosen")}
        # paket
        self.paket = defaultdict(set)
        self.kelas_paket = defaultdict(set)
        for r in rd("kelompok_paket"):
            self.paket[r["paket_id"]].add(r["kelas_id"])
            self.kelas_paket[r["kelas_id"]].add(r["paket_id"])
        # blok waktu
        self.blok = {}
        self.blok_ids = []
        self.kunci_blok = {}
        for r in rd("blok_waktu"):
            b = r["blok_id"]
            self.blok[b] = dict(hari=r["hari"], sesi=r["sesi"], urutan=int_aman(r["urutan"]), mulai=r["jam_mulai"],
                                selesai=r["jam_selesai"], prodi=himpunan(r["prodi_diizinkan"]))
            self.blok_ids.append(b)
            self.kunci_blok[(r["hari"], r["sesi"], int_aman(r["urutan"]))] = b
        self.hari_blok = defaultdict(list)  # hari -> blok terurut waktu
        for b in self.blok_ids:
            self.hari_blok[self.blok[b]["hari"]].append(b)
        for h in self.hari_blok:
            self.hari_blok[h].sort(key=lambda b: self.blok[b]["mulai"])
        self.posisi = {b: i for h, lst in self.hari_blok.items() for i, b in enumerate(lst)}
        # ruangan + aturan
        nama_ruang = {r["ruangan_id"]: r["nama"] for r in rd("ruangan")}
        self.ruang = {}
        for r in rd("aturan_ruangan"):
            self.ruang[r["ruangan_id"]] = dict(
                nama=nama_ruang.get(r["ruangan_id"], r["ruangan_id"]), cap=max(1, int_aman(r["maks_kelas_simultan"], 1)),
                hari=himpunan(r["hari_diizinkan"]), jenis=himpunan(r["jenis_mk_diizinkan"]), prodi=himpunan(r["prodi_diizinkan"]))
        self.bangun_domain()

    # ---- domain
    def blok_ok(self, b, prodi):
        izin = self.blok[b]["prodi"]
        return izin is None or prodi.casefold() in izin

    def urutan_blok(self, b, n):
        """n blok berurutan mulai dari b, dalam hari dan sesi yang sama; None bila tidak ada."""
        info = self.blok[b]
        seq = [b]
        for i in range(1, n):
            nb = self.kunci_blok.get((info["hari"], info["sesi"], info["urutan"] + i))
            if nb is None:
                return None
            seq.append(nb)
        return seq

    def ruang_ok(self, r, hari, jenis, prodi):
        a = self.ruang[r]
        return ((a["hari"] is None or hari.casefold() in a["hari"])
                and (a["jenis"] is None or jenis in a["jenis"])
                and (a["prodi"] is None or prodi.casefold() in a["prodi"]))

    def bangun_domain(self):
        self.domain = {}
        self.urut = {}  # (kelas, blok_awal) -> daftar blok yang dipakai
        for kid, k in self.kelas.items():
            opsi = []
            for b in self.blok_ids:
                if not self.blok_ok(b, k["prodi"]):
                    continue
                seq = self.urutan_blok(b, k["sks"])
                if seq is None or not all(self.blok_ok(x, k["prodi"]) for x in seq):
                    continue
                self.urut[(kid, b)] = seq
                hari = self.blok[b]["hari"]
                for r in self.ruang:
                    if self.ruang_ok(r, hari, k["jenis"], k["prodi"]):
                        opsi.append((b, r))
            self.domain[kid] = opsi
        self.domain_set = {kid: set(v) for kid, v in self.domain.items()}

    def seq_pakai(self, kid, b):
        return self.urut.get((kid, b)) or self.urutan_blok(b, self.kelas[kid]["sks"]) or [b]

    # ---- alasan penempatan di luar domain
    def alasan_di_luar(self, kid, b, r):
        k = self.kelas[kid]
        if b not in self.blok:
            return "H1: blok awal tidak dikenal"
        if r not in self.ruang:
            return "H5: ruangan tidak dikenal"
        seq = self.urutan_blok(b, k["sks"])
        if seq is None:
            return "H1: blok tidak berurutan dalam satu hari dan sesi"
        if not all(self.blok_ok(x, k["prodi"]) for x in seq):
            return "H6: blok tidak diizinkan untuk prodi"
        if not self.ruang_ok(r, self.blok[b]["hari"], k["jenis"], k["prodi"]):
            return "H5: ruangan tidak diizinkan (hari, jenis mata kuliah, atau prodi)"
        return "H1: di luar domain"

    # ---- validator dan nilai batasan lunak
    def validasi(self, jadwal, detail=True):
        """jadwal: {kelas_id: (blok_awal, ruangan_id)}. Mengembalikan (pelanggaran, lunak, rincian).
        detail=False melewati penyusunan teks rincian (dipakai GA agar evaluasi lebih cepat)."""
        rinci = defaultdict(list)
        v = dict(H1=0, H2=0, H3=0, H4=0, H5=0, H6=0)
        for kid in self.kelas:
            if kid not in jadwal:
                v["H1"] += 1
                if detail:
                    rinci["H1"].append(f"{kid}: tidak terjadwal")
        for kid, (b, r) in jadwal.items():
            if kid not in self.kelas:
                if detail:
                    rinci["info"].append(f"{kid}: kelas tidak dikenal, diabaikan")
                continue
            if (b, r) not in self.domain_set[kid]:
                alasan = self.alasan_di_luar(kid, b, r)
                v[alasan[:2]] += 1
                if detail:
                    rinci[alasan[:2]].append(f"{kid} @ {b},{r}: {alasan}")
        valid = {kid: br for kid, br in jadwal.items() if kid in self.kelas}
        # H2: isi ruangan per blok
        sel = defaultdict(list)
        for kid, (b, r) in valid.items():
            for x in self.seq_pakai(kid, b):
                sel[(r, x)].append(kid)
        for (r, x), ks in sel.items():
            mks = {self.kelas[k]["mk"] for k in ks}
            awal = {valid[k][0] for k in ks}
            batas = self.ruang[r]["cap"] if r in self.ruang else 1
            batas = min(batas, min(self.cap_mk.get(m, 99) for m in mks))
            n = (len(mks) - 1) + ((len(awal) - 1) if len(mks) == 1 else 0) + max(0, len(ks) - batas)
            if n:
                v["H2"] += n
                if detail:
                    rinci["H2"].append(f"ruangan {r} blok {x}: {', '.join(sorted(ks))}")
        # H3: dosen
        for d, ks in self.dosen_kelas.items():
            per_blok = defaultdict(set)
            for kid in ks:
                if kid in valid:
                    b, r = valid[kid]
                    for x in self.seq_pakai(kid, b):
                        per_blok[x].add((b, r))
            for x, sesi in per_blok.items():
                if len(sesi) > 1:
                    v["H3"] += len(sesi) - 1
                    if detail:
                        rinci["H3"].append(f"dosen {self.nama_dosen.get(d, d)} blok {x}: {sorted(sesi)}")
        # H4: paket
        for p, ks in self.paket.items():
            per_blok = defaultdict(list)
            for kid in ks:
                if kid in valid:
                    for x in self.seq_pakai(kid, valid[kid][0]):
                        per_blok[x].append(kid)
            for x, lst in per_blok.items():
                if len(lst) > 1:
                    v["H4"] += len(lst) - 1
                    if detail:
                        rinci["H4"].append(f"paket {p} blok {x}: {', '.join(sorted(lst))}")
        return v, self.lunak(valid), rinci

    def lunak(self, valid):
        # S1: sesi gabungan
        sesi = defaultdict(int)
        for kid, (b, r) in valid.items():
            sesi[(self.kelas[kid]["mk"], b, r)] += 1
        s1 = sum(n - 1 for n in sesi.values())
        # S2: jeda kosong kelas wajib dalam satu paket per hari
        s2 = 0
        for p, ks in self.paket.items():
            pos = defaultdict(set)
            for kid in ks:
                if kid in valid:
                    for x in self.seq_pakai(kid, valid[kid][0]):
                        pos[self.blok[x]["hari"]].add(self.posisi[x])
            for ps in pos.values():
                s2 += (max(ps) - min(ps) + 1) - len(ps)
        # S3: beban dosen antarhari (jumlah kuadrat selisih terhadap rata-rata)
        hari_prodi = defaultdict(set)
        for b in self.blok_ids:
            for pr in ("PSTE", "PSTI", "MTE"):
                if self.blok_ok(b, pr):
                    hari_prodi[pr].add(self.blok[b]["hari"])
        s3 = 0.0
        for d, ks in self.dosen_kelas.items():
            sesi_d = {(valid[k][0], valid[k][1]): k for k in ks if k in valid}
            if not sesi_d:
                continue
            hari_d = set().union(*(hari_prodi[self.kelas[k]["prodi"]] for k in ks if k in valid))
            beban = {h: 0 for h in hari_d}
            for (b, r), k in sesi_d.items():
                for x in self.seq_pakai(k, b):
                    beban[self.blok[x]["hari"]] = beban.get(self.blok[x]["hari"], 0) + 1
            rata = sum(beban.values()) / len(beban)
            s3 += sum((n - rata) ** 2 for n in beban.values())
        # S4: blok-kelas wajib yang berakhir setelah BATAS_SORE
        s4 = 0
        for kid, (b, r) in valid.items():
            if self.kelas[kid]["jenis"] == "wajib":
                s4 += sum(1 for x in self.seq_pakai(kid, b) if self.blok[x]["selesai"] > BATAS_SORE)
        # S5 (Bab 3, Even Distribution): jumlah kelas per hari kerja (Senin-Jumat); S5 = sum_h |L_h - rata-rata|
        hari_kerja = [h for h in ("Senin", "Selasa", "Rabu", "Kamis", "Jumat")]
        L = {h: 0 for h in hari_kerja}
        for kid, (b, r) in valid.items():
            h = self.blok[b]["hari"]
            if h in L:
                L[h] += 1
        rata_L = sum(L.values()) / len(L)
        s5 = sum(abs(v - rata_L) for v in L.values())
        # S6 (Bab 3, Room Utilization Balance): U_r = blok terpakai / blok tersedia pada ruangan r; S6 = sum_r |U_r - rata-rata|
        # [INTERPRETASI penulis kode: Bab 3 tidak merinci "kapasitas blok"; dipakai blok Senin-Jumat pada hari yang diizinkan
        #  ruangan itu, dan blok dihitung terpakai bila ruangan memuat minimal satu sesi pada blok tsb]
        if not hasattr(self, "_blok_tersedia"):
            self._blok_tersedia = {}
            for r, a in self.ruang.items():
                self._blok_tersedia[r] = max(1, sum(1 for b in self.blok_ids if self.blok[b]["hari"] in L
                                                    and (a["hari"] is None or self.blok[b]["hari"].casefold() in a["hari"])))
        terpakai = defaultdict(set)
        for kid, (b, r) in valid.items():
            if self.blok[b]["hari"] in L:
                terpakai[r].update(x for x in self.seq_pakai(kid, b) if self.blok[x]["hari"] in L)
        U = {r: len(terpakai[r]) / self._blok_tersedia[r] for r in self.ruang}
        rata_U = sum(U.values()) / len(U)
        s6 = sum(abs(v - rata_U) for v in U.values())
        nilai = dict(S1_gabung=s1, S2_jeda=s2, S3_beban=round(s3, 3), S4_sore=s4, S5_merata=round(s5, 3), S6_utilisasi=round(s6, 4))
        total = sum(BOBOT[k] * nilai[n] for n, k in SUKU)
        nilai["f_berbobot"] = round(total, 3)
        return nilai

    # ---- kelas yang terlibat pelanggaran H2-H4 (dipakai repair pada GA)
    def kelas_konflik(self, valid):
        """Himpunan kelas yang terlibat pelanggaran H2, H3, atau H4 pada jadwal valid {kelas: (blok, ruangan)}."""
        out = set()
        sel = defaultdict(list)
        for kid, (b, r) in valid.items():
            for x in self.seq_pakai(kid, b):
                sel[(r, x)].append(kid)
        for (r, x), ks in sel.items():
            if len(ks) < 2:
                continue
            mks = {self.kelas[k]["mk"] for k in ks}
            awal = {valid[k][0] for k in ks}
            batas = min(self.ruang[r]["cap"], min(self.cap_mk.get(m, 99) for m in mks))
            if len(mks) > 1 or len(awal) > 1 or len(ks) > batas:
                out.update(ks)
        for d, ks in self.dosen_kelas.items():
            per_blok = defaultdict(list)
            for kid in ks:
                if kid in valid:
                    for x in self.seq_pakai(kid, valid[kid][0]):
                        per_blok[x].append(kid)
            for x, lst in per_blok.items():
                if len({valid[k] for k in lst}) > 1:
                    out.update(lst)
        for p, ks in self.paket.items():
            per_blok = defaultdict(list)
            for kid in ks:
                if kid in valid:
                    for x in self.seq_pakai(kid, valid[kid][0]):
                        per_blok[x].append(kid)
            for lst in per_blok.values():
                if len(lst) > 1:
                    out.update(lst)
        return out


class Keadaan:
    """Keadaan hunian yang konsisten (tanpa pelanggaran H2-H4) untuk pengecekan penempatan secara bertahap."""

    def __init__(self, ms):
        self.ms = ms
        self.sel = {}         # (ruangan, blok) -> [mk, blok_awal, jumlah]
        self.dosen_sel = {}   # (dosen, blok) -> (blok_awal, ruangan)
        self.paket_sel = set()

    def bisa(self, kid, b, r):
        ms = self.ms
        seq = ms.seq_pakai(kid, b)
        mk = ms.kelas[kid]["mk"]
        awal = self.sel.get((r, seq[0]))
        if awal is None:  # sesi baru: semua sel ruangan harus kosong
            if any((r, x) in self.sel for x in seq):
                return False
        else:             # gabung: mata kuliah dan blok awal sama, kapasitas masih ada
            if awal[0] != mk or awal[1] != b or awal[2] >= min(ms.ruang[r]["cap"], ms.cap_mk.get(mk, 99)):
                return False
        for d in ms.kelas_dosen[kid]:
            for x in seq:
                t = self.dosen_sel.get((d, x))
                if t is not None and t != (b, r):
                    return False
        for p in ms.kelas_paket[kid]:
            if any((p, x) in self.paket_sel for x in seq):
                return False
        return True

    def tambah(self, kid, b, r):
        ms = self.ms
        mk = ms.kelas[kid]["mk"]
        for x in ms.seq_pakai(kid, b):
            if (r, x) in self.sel:
                self.sel[(r, x)][2] += 1
            else:
                self.sel[(r, x)] = [mk, b, 1]
            for d in ms.kelas_dosen[kid]:
                self.dosen_sel[(d, x)] = (b, r)
            for p in ms.kelas_paket[kid]:
                self.paket_sel.add((p, x))


# ------------------------------------------------------------------ keluaran
def nomor_berikut(induk, awalan):
    nums = [int(m.group(1)) for p in induk.iterdir() if (m := re.fullmatch(awalan + r"(\d+)", p.name))] if induk.exists() else []
    return max(nums, default=0) + 1


def tulis_jadwal(masalah, jadwal, path):
    sesi = defaultdict(int)
    for kid, (b, r) in jadwal.items():
        sesi[(masalah.kelas[kid]["mk"], b, r)] += 1
    rows = []
    for kid in sorted(jadwal, key=lambda k: (masalah.kelas[k]["prodi"], masalah.blok_ids.index(jadwal[k][0]), k)):
        b, r = jadwal[kid]
        seq = masalah.seq_pakai(kid, b)
        k = masalah.kelas[kid]
        rows.append([kid, k["prodi"], k["mk"], b, r, masalah.ruang[r]["nama"], masalah.blok[b]["hari"], masalah.blok[b]["mulai"],
                     masalah.blok[seq[-1]]["selesai"],
                     "; ".join(masalah.nama_dosen.get(d, d) for d in sorted(masalah.kelas_dosen[kid])), sesi[(k["mk"], b, r)]])
    tulis(path, ["kelas_id", "prodi", "mk_id", "blok_awal", "ruangan_id", "nama_ruangan", "hari", "jam_mulai", "jam_selesai",
                 "dosen", "ukuran_sesi"], rows)


def baca_jadwal(path):
    jd = {}
    for r in baca(path):
        jd[r["kelas_id"]] = (r["blok_awal"], r["ruangan_id"])
    return jd


def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser(description="Pemodelan: domain dan validator")
    ap.add_argument("--bersih", default=str(base / "bersih"))
    ap.add_argument("--hasil", help="nama folder run pembersihan, mis. hasil2 (bawaan: yang terbaru)")
    ap.add_argument("--keluar", default=str(base / "jadwal"))
    ap.add_argument("--validasi", help="berkas jadwal CSV (kelas_id,blok_awal,ruangan_id) untuk divalidasi")
    a = ap.parse_args()

    bersih = Path(a.bersih)
    if a.hasil:
        folder = bersih / a.hasil
    else:
        cand = [p for p in bersih.glob("hasil*") if re.fullmatch(r"hasil\d+", p.name) and (p / f"status{p.name[5:]}.txt").exists()]
        if not cand:
            sys.exit(f"Tidak ada folder hasilN di {bersih}. Jalankan pembersihan data dulu.")
        folder = max(cand, key=lambda p: int(p.name[5:]))
    ms = Masalah(folder)
    print(f"Memakai {folder.name}: {len(ms.kelas)} kelas, {len(ms.ruang)} ruangan, {len(ms.blok)} blok, {len(ms.paket)} paket")

    keluar = Path(a.keluar)
    keluar.mkdir(parents=True, exist_ok=True)
    N = nomor_berikut(keluar, "jadwal")
    out = keluar / f"jadwal{N}"
    out.mkdir()

    # ringkasan domain
    kosong = [kid for kid, d in ms.domain.items() if not d]
    tulis(out / f"ringkasan_domain{N}.csv", ["kelas_id", "prodi", "mk_id", "jenis", "sks", "ukuran_domain"],
          [[kid, k["prodi"], k["mk"], k["jenis"], k["sks"], len(ms.domain[kid])] for kid, k in sorted(ms.kelas.items())])
    ukur = sorted(len(d) for d in ms.domain.values())
    print(f"Domain: min {ukur[0]}, median {ukur[len(ukur) // 2]}, maks {ukur[-1]}; kelas dengan domain kosong: {len(kosong)}")
    for kid in kosong:
        print("  DOMAIN KOSONG:", kid, ms.kelas[kid]["nama"])

    if a.validasi:
        jd = baca_jadwal(a.validasi)
        v, lunak, rinci = ms.validasi(jd)
        print("Pelanggaran keras:", v)
        print("Batasan lunak:", lunak)
        tulis(out / f"laporan_validasi{N}.csv", ["indikator", "nilai"], list(v.items()) + list(lunak.items()))
        tulis(out / f"rincian_pelanggaran{N}.csv", ["jenis", "keterangan"], [[j, t] for j, lst in rinci.items() for t in lst])
        print(f"Hasil di {out}")
        return 0 if sum(v.values()) == 0 and not kosong else 1

    with open(out / f"status{N}.txt", "w", encoding="utf-8") as f:
        f.write(f"JADWAL: {N}\nSUMBER: {folder.name}\nDOMAIN_KOSONG: {len(kosong)}\n")
    print(f"Hasil di {out}")
    print("Untuk membuat jadwal: python src\\greedy.py  atau  python src\\ga.py")
    return 0 if not kosong else 1


if __name__ == "__main__":
    sys.exit(main())