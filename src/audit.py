"""
Audit independen hard constraint (bukti bahwa jadwal tidak melanggar H1-H6).

Mengapa skrip ini ada: pemeriksaan di pemodelan.py memakai struktur data yang sama dengan greedy/GA (ID blok, tabel domain).
Skrip ini sengaja TIDAK mengimpor pemodelan.py. Ia hanya membaca (a) berkas jadwal keluaran (hari, jam mulai, jam selesai,
ruangan) dan (b) berkas bersih (kelas, kelas_dosen, kelompok_paket, aturan_ruangan, blok_waktu), lalu memeriksa dengan
logika sendiri: bentrok = dua interval JAM NYATA (menit) pada hari yang sama saling tumpang tindih.

Pemeriksaan:
  H1  tiap kelas terjadwal tepat sekali; jumlah blok yang tercakup = durasi_blok; blok berurutan, satu hari, satu sesi
  H2  ruangan: dua kelas tumpang tindih di ruangan yang sama hanya sah bila satu sesi gabungan (mata kuliah, hari, jam sama)
      dan jumlahnya <= kapasitas simultan ruangan dan kapasitas gabung mata kuliah
  H3  dosen: dua kelas milik dosen yang sama tidak boleh tumpang tindih, kecuali keduanya satu sesi gabungan
      (mata kuliah, hari, jam, ruangan sama; dosen mengajar sekali untuk kelas gabungan)
  H4  paket (mahasiswa satu angkatan/label): tidak ada kelas satu paket yang tumpang tindih
  H5  ruangan sesuai aturan_ruangan (hari, jenis mata kuliah, prodi)
  H6  blok yang dipakai diizinkan untuk prodi kelas itu

Tambahan: uji negatif (--uji-negatif) menyisipkan pelanggaran sengaja pada salinan jadwal dan memastikan audit menangkapnya;
peringatan data (dosen ganda ber-ID berbeda, kelas tanpa dosen); laporan per dosen (dosen_audit.csv); tampilan khusus
(--dosen, --mk) untuk menunjukkan jadwal dosen atau beberapa mata kuliah secara langsung.

Contoh:
  python src\\audit_hard.py                      # otomatis: gaN terbaru + data bersih dari status
  python src\\audit_hard.py --jadwal data\\ga\\ga3\\jadwal_ga3.csv --hasil data\\bersih\\hasil5
  python src\\audit_hard.py --jadwal ... --hasil ... --mk kalkulus matematika-diskrit kecerdasan-buatan
  python src\\audit_hard.py --jadwal ... --hasil ... --dosen "Helmy" --uji-negatif
Keluaran: folder audit di sebelah berkas jadwal (audit_hard/): audit.txt, pelanggaran.csv, dosen_audit.csv.
Kode keluar 0 bila semua hard constraint terpenuhi, 1 bila ada pelanggaran.
"""
import argparse
import csv
import re
import sys
from collections import defaultdict
from pathlib import Path


def baca(p):
    with open(p, encoding="utf-8-sig", newline="") as f:
        return list(csv.DictReader(f))


def menit(s):
    h, m = s.strip().split(":")[:2]
    return int(h) * 60 + int(m)


def himp(v):
    v = (v or "").strip().casefold()
    if v in ("", "semua", "*"):
        return None
    return {x.strip() for x in re.split(r"[;,|]", v) if x.strip()}


def tumpang(a, b):
    """a, b = (hari, mulai, selesai) -> True bila hari sama dan interval beririsan (bersentuhan di ujung TIDAK dihitung)."""
    return a[0] == b[0] and a[1] < b[2] and b[1] < a[2]


class Data:
    def __init__(self, hasil):
        hasil = Path(hasil)
        n = re.search(r"hasil(\d+)$", hasil.name).group(1)
        rd = lambda s: baca(hasil / f"{s}{n}.csv")
        self.kelas = {r["kelas_id"]: r for r in rd("kelas")}
        self.mk = {r["mk_id"]: r for r in rd("mata_kuliah")}
        self.dosen_nama = {r["dosen_id"]: r["nama_baku"] for r in rd("dosen")}
        self.kelas_dosen = defaultdict(set)
        for r in rd("kelas_dosen"):
            self.kelas_dosen[r["kelas_id"]].add(r["dosen_id"])
        self.paket = defaultdict(set)
        for r in rd("kelompok_paket"):
            self.paket[r["paket_id"]].add(r["kelas_id"])
        self.blok = {r["blok_id"]: r for r in rd("blok_waktu")}
        self.ruang = {r["ruangan_id"]: r for r in rd("aturan_ruangan")}
        self.nama_ruang = {r["ruangan_id"]: r.get("nama", r["ruangan_id"]) for r in rd("ruangan")}


def periksa(d, jadwal_rows):
    """Kembalikan daftar pelanggaran [(jenis, keterangan)], dan statistik. jadwal_rows: daftar dict baris jadwal."""
    out = []
    stat = defaultdict(int)
    per_kelas = defaultdict(list)
    for r in jadwal_rows:
        per_kelas[r["kelas_id"]].append(r)
    # ---- H1: kelengkapan
    for kid in d.kelas:
        if kid not in per_kelas:
            out.append(("H1", f"{kid}: tidak terjadwal"))
    for kid, rs in per_kelas.items():
        if kid not in d.kelas:
            out.append(("H1", f"{kid}: tidak ada pada data kelas"))
        elif len(rs) > 1:
            out.append(("H1", f"{kid}: terjadwal {len(rs)} kali"))
    iv = {}   # kelas -> (hari, mulai, selesai, ruangan, mk)
    for kid, rs in per_kelas.items():
        if kid not in d.kelas:
            continue
        r = rs[0]
        k = d.kelas[kid]
        iv[kid] = (r["hari"], menit(r["jam_mulai"]), menit(r["jam_selesai"]), r["ruangan_id"], k["mk_id"], r["blok_awal"])
    # ---- H1: durasi, berurutan, satu hari, satu sesi (dari tabel blok_waktu, tanpa tabel domain)
    for kid, (hari, m1, m2, ruang, mk, ba) in iv.items():
        k = d.kelas[kid]
        need = int(k["durasi_blok"] or k["sks"])
        cakup = [b for b in d.blok.values() if b["hari"] == hari and menit(b["jam_mulai"]) >= m1 and menit(b["jam_selesai"]) <= m2]
        cakup.sort(key=lambda b: menit(b["jam_mulai"]))
        ok = len(cakup) == need
        if ok:
            ok = len({b["sesi"] for b in cakup}) == 1 and all(
                int(cakup[i + 1]["urutan"]) == int(cakup[i]["urutan"]) + 1 for i in range(len(cakup) - 1))
        if not ok:
            out.append(("H1", f"{kid}: {hari} {r_fmt(m1)}-{r_fmt(m2)} mencakup {len(cakup)} blok (butuh {need}), atau tidak berurutan/satu sesi"))
        # ---- H6: blok per prodi
        for b in cakup:
            izin = himp(b.get("prodi_diizinkan"))
            if izin is not None and k["prodi"].casefold() not in izin:
                out.append(("H6", f"{kid}: blok {b['blok_id']} tidak diizinkan untuk prodi {k['prodi']}"))
                break
        # ---- H5: aturan ruangan
        a = d.ruang.get(ruang)
        if a is None:
            out.append(("H5", f"{kid}: ruangan {ruang} tidak dikenal"))
        else:
            h, j, p = himp(a["hari_diizinkan"]), himp(a["jenis_mk_diizinkan"]), himp(a["prodi_diizinkan"])
            jenis = (k["jenis_mk"] or "").casefold()
            if (h is not None and hari.casefold() not in h) or (j is not None and jenis not in j) or (
                    p is not None and k["prodi"].casefold() not in p):
                out.append(("H5", f"{kid}: ruangan {ruang} tidak diizinkan (hari {hari}, jenis {jenis}, prodi {k['prodi']})"))
    ids = sorted(iv)
    gabungan_sah = 0

    def satu_sesi(a, b):
        return iv[a][4] == iv[b][4] and iv[a][:4] == iv[b][:4]   # mk, hari, jam, ruangan sama
    # ---- H3: dosen
    peta = defaultdict(list)
    for kid in ids:
        for dos in d.kelas_dosen.get(kid, ()):
            peta[dos].append(kid)
    for dos, ks in peta.items():
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                a, b = ks[i], ks[j]
                if tumpang(iv[a][:3], iv[b][:3]):
                    if satu_sesi(a, b):
                        gabungan_sah += 1
                    else:
                        out.append(("H3", f"dosen {d.dosen_nama.get(dos, dos)} bentrok: {a} {iv[a][:3]} dan {b} {iv[b][:3]}"))
    stat["pasangan kelas dosen yang sama pada sesi gabungan (sah)"] = gabungan_sah
    # ---- H4: paket
    for p, ks in d.paket.items():
        ks = [k for k in ks if k in iv]
        for i in range(len(ks)):
            for j in range(i + 1, len(ks)):
                if tumpang(iv[ks[i]][:3], iv[ks[j]][:3]):
                    out.append(("H4", f"paket {p} bentrok: {ks[i]} dan {ks[j]}"))
    # ---- H2: ruangan
    per_r = defaultdict(list)
    for kid in ids:
        per_r[iv[kid][3]].append(kid)
    for r, ks in per_r.items():
        cap_r = max(1, int(float((d.ruang.get(r, {}).get("maks_kelas_simultan") or 1))) ) if r in d.ruang else 1
        for i in range(len(ks)):
            a = ks[i]
            grup = [b for b in ks if tumpang(iv[a][:3], iv[b][:3])]   # termasuk dirinya
            if len(grup) == 1:
                continue
            mks = {iv[b][4] for b in grup}
            sama = all(iv[b][:3] == iv[a][:3] for b in grup)
            capmk = min(int(float(d.mk.get(m, {}).get("kapasitas_gabung") or 99)) for m in mks)
            if len(mks) > 1 or not sama or len(grup) > min(cap_r, capmk):
                out.append(("H2", f"ruangan {r} {iv[a][0]} {r_fmt(iv[a][1])}: {', '.join(sorted(grup))}"))
    # satu pelanggaran H2 yang sama dilaporkan berkali-kali (sekali per anggota): hapus duplikat
    seen, uniq = set(), []
    for j, t in out:
        key = (j, t.split(": ", 1)[-1] if j == "H2" else t)
        if key in seen:
            continue
        seen.add(key)
        uniq.append((j, t))
    stat["kelas terjadwal"] = len(iv)
    stat["kelas pada data"] = len(d.kelas)
    return uniq, stat, iv


def r_fmt(m):
    return f"{m // 60:02d}:{m % 60:02d}"


def peringatan_data(d):
    """Risiko pada DATA, yang tidak bisa ditangkap validator mana pun: dosen yang sama ber-ID berbeda, kelas tanpa dosen."""
    w = []
    tanpa = [k for k in d.kelas if not d.kelas_dosen.get(k)]
    if tanpa:
        w.append(f"{len(tanpa)} kelas tidak memiliki dosen pada data (tidak mungkin diperiksa bentrok dosennya)")

    def inti(n):
        n = n.split(",")[0].casefold()
        n = re.sub(r"\b(dr|ir|prof|h|hj|eng|ing)\b\.?", " ", n)
        return re.sub(r"[^a-z ]", "", n).split()
    kunci = defaultdict(list)
    for did, nm in d.dosen_nama.items():
        t = inti(nm)
        if t:
            kunci[" ".join(sorted(t))].append(did)
    for k, v in kunci.items():
        if len(v) > 1:
            w.append(f"kemungkinan satu dosen ber-ID ganda: {', '.join(d.dosen_nama[x] for x in v)} -> bentrok antar-ID tidak terdeteksi")
    return w


def tabel_dosen(d, iv, dos_ids):
    rows = []
    for dos in dos_ids:
        ks = sorted((kid for kid in iv if dos in d.kelas_dosen.get(kid, ())), key=lambda k: (iv[k][0], iv[k][1]))
        for kid in ks:
            h, m1, m2, r, mk, _ = iv[kid]
            rows.append([d.dosen_nama.get(dos, dos), kid, d.kelas[kid]["prodi"], h, r_fmt(m1), r_fmt(m2), r])
    return rows


def main():
    ap = argparse.ArgumentParser(description="Audit independen hard constraint")
    ap.add_argument("--jadwal", help="jadwal_gaN.csv / jadwal_greedyN.csv, atau folder gaN/greedyN (bawaan: gaN terbaru di data\\ga)")
    ap.add_argument("--hasil", help="folder data bersih, mis. data\\bersih\\hasil5 (bawaan: dibaca dari SUMBER pada status folder jadwal)")
    ap.add_argument("--dosen", nargs="*", default=[], help="tampilkan jadwal dosen yang namanya memuat teks ini")
    ap.add_argument("--mk", nargs="*", default=[], help="tampilkan semua kelas mata kuliah yang id-nya memuat teks ini")
    ap.add_argument("--uji-negatif", action="store_true", help="sisipkan pelanggaran sengaja, pastikan audit menangkapnya")
    ap.add_argument("--keluar", help="folder induk keluaran (bawaan: data\\pemeriksaan)")
    a = ap.parse_args()

    a.jadwal, a.hasil = tentukan(a.jadwal, a.hasil)
    d = Data(a.hasil)
    rows = baca(a.jadwal)
    out_dir = folder_keluar(a.jadwal, "audit_hard", a.keluar, bernomor=not (Path(a.jadwal).resolve().parent / "asal_jadwal.txt").exists() or bool(a.keluar))
    L = []
    pr = lambda s="": (L.append(s), print(s))

    pelanggar, stat, iv = periksa(d, rows)
    pr(f"AUDIT HARD CONSTRAINT: {a.jadwal}")
    pr(f"Data: {a.hasil} ({len(d.kelas)} kelas, {len(d.dosen_nama)} dosen, {len(d.paket)} paket)")
    pr("")
    per = defaultdict(int)
    for j, _ in pelanggar:
        per[j] += 1
    for j in ("H1", "H2", "H3", "H4", "H5", "H6"):
        pr(f"  {j}: {per[j]} pelanggaran")
    pr(f"  Sesi gabungan yang sah (dosen sama pada sesi yang sama): {stat['pasangan kelas dosen yang sama pada sesi gabungan (sah)']} pasangan")
    pr(f"  Kelas terjadwal: {stat['kelas terjadwal']} dari {stat['kelas pada data']}")
    pr("  => " + ("SEMUA HARD CONSTRAINT TERPENUHI" if not pelanggar else f"ADA {len(pelanggar)} PELANGGARAN"))
    pr("")

    # tampilan langsung: dosen dan mata kuliah yang diminta
    for kata in a.dosen:
        cocok = [x for x, n in d.dosen_nama.items() if kata.casefold() in n.casefold()]
        pr(f"Jadwal dosen '{kata}':")
        for r in tabel_dosen(d, iv, cocok):
            pr(f"  {r[0]:<40} {r[3]:<7} {r[4]}-{r[5]}  {r[6]:<5} {r[1]}")
        pr("")
    for kata in a.mk:
        ks = [k for k in iv if kata.casefold() in d.kelas[k]["mk_id"].casefold()]
        pr(f"Mata kuliah '{kata}': {len(ks)} kelas")
        for k in sorted(ks, key=lambda k: (iv[k][0], iv[k][1])):
            h, m1, m2, r, mk, _ = iv[k]
            pr(f"  {k:<55} {h:<7} {r_fmt(m1)}-{r_fmt(m2)}  {r:<5} dosen: {'; '.join(d.dosen_nama.get(x, x) for x in sorted(d.kelas_dosen.get(k, ())))}")
        dosen_mk = {x for k in ks for x in d.kelas_dosen.get(k, ())}
        for x in sorted(dosen_mk):
            sendiri = [k for k in iv if x in d.kelas_dosen.get(k, ())]
            pr(f"  -> {d.dosen_nama.get(x, x)}: mengampu {len(sendiri)} kelas pada seluruh jadwal, bentrok: "
               f"{sum(1 for j, t in pelanggar if j == 'H3' and d.dosen_nama.get(x, x) in t)}")
        pr("")

    # peringatan data
    pw = peringatan_data(d)
    pr("Peringatan data (tidak dapat dibuktikan oleh validator, perlu dicek manual terhadap SK pengampu):")
    for w in pw or ["tidak ada"]:
        pr(f"  - {w}")
    pr("")

    # uji negatif: pelanggaran sengaja harus tertangkap
    if a.uji_negatif:
        pr("UJI NEGATIF (pada salinan jadwal; berkas asli tidak diubah):")
        hasil_neg = []
        # H3: dua kelas dosen yang sama, kelas kedua dipindah ke jam kelas pertama di ruangan lain
        for dos, ks0 in sorted(tabel_peta(d, iv).items()):
            ks0 = [k for k in ks0 if len({iv[k][:3]}) == 1]
            if len(ks0) >= 2:
                k1, k2 = ks0[0], ks0[1]
                if iv[k1][4] != iv[k2][4]:
                    salin = [dict(r) for r in rows]
                    for r in salin:
                        if r["kelas_id"] == k2:
                            r["hari"], r["jam_mulai"], r["jam_selesai"], r["blok_awal"] = (
                                iv[k1][0], r_fmt(iv[k1][1]), r_fmt(iv[k1][2]), next(x["blok_awal"] for x in rows if x["kelas_id"] == k1))
                            r["ruangan_id"] = next((x for x in d.ruang if x != iv[k1][3] and x != iv[k2][3]), r["ruangan_id"])
                    p2, _, _ = periksa(d, salin)
                    tangkap = any(j == "H3" and d.dosen_nama.get(dos, dos) in t for j, t in p2)
                    hasil_neg.append(("H3", f"{d.dosen_nama.get(dos, dos)}: {k2} dipindah ke jam {k1}", tangkap))
                    break
        # H1: hapus satu kelas
        salin = [dict(r) for r in rows][1:]
        p2, _, _ = periksa(d, salin)
        hasil_neg.append(("H1", f"menghapus kelas {rows[0]['kelas_id']}", any(j == "H1" for j, _ in p2)))
        # H2: dua kelas berbeda mata kuliah ke ruangan dan jam yang sama
        if len(iv) >= 2:
            ks = [k for k in iv if iv[k][4] != iv[ids_pertama(iv)][4]]
            if ks:
                k1, k2 = ids_pertama(iv), ks[0]
                salin = [dict(r) for r in rows]
                for r in salin:
                    if r["kelas_id"] == k2:
                        r["hari"], r["jam_mulai"], r["jam_selesai"], r["ruangan_id"] = (
                            iv[k1][0], r_fmt(iv[k1][1]), r_fmt(iv[k1][2]), iv[k1][3])
                p2, _, _ = periksa(d, salin)
                hasil_neg.append(("H2", f"{k2} dipindah ke ruangan dan jam {k1}", any(j == "H2" for j, _ in p2)))
        for j, ket, ok in hasil_neg:
            pr(f"  {j}: {ket} -> {'TERTANGKAP' if ok else 'LOLOS (audit tidak sensitif!)'}")
        pr("")

    # keluaran
    with open(out_dir / "audit.txt", "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")
    with open(out_dir / "pelanggaran.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["jenis", "keterangan"])
        w.writerows(pelanggar)
    with open(out_dir / "dosen_audit.csv", "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["dosen", "kelas_id", "prodi", "hari", "mulai", "selesai", "ruangan_id"])
        w.writerows(tabel_dosen(d, iv, sorted(d.dosen_nama, key=lambda x: d.dosen_nama[x])))
    print(f"Hasil di {out_dir}")
    return 0 if not pelanggar else 1


def folder_keluar(jadwal, awalan, keluar=None, bernomor=True):
    """Tempat keluaran alat bantu. Bawaan: data\\pemeriksaan\\ (terpisah dari folder algoritma).
    Bila jadwal berada di folder final (ada asal_jadwal.txt), keluaran ditaruh di folder final itu.
    --keluar mengganti tujuan."""
    jp = Path(jadwal).resolve()
    if keluar:
        induk = Path(keluar).resolve()
    elif (jp.parent / "asal_jadwal.txt").exists():
        induk = jp.parent
    else:
        induk = Path(__file__).resolve().parent.parent / "data" / "pemeriksaan"
    induk.mkdir(parents=True, exist_ok=True)
    if not bernomor:
        out = induk / awalan
        out.mkdir(exist_ok=True)
        return out
    n = 1
    while (induk / f"{awalan}{n}").exists():
        n += 1
    out = induk / f"{awalan}{n}"
    out.mkdir()
    return out


def tentukan(jadwal, hasil):
    """Lengkapi argumen yang tidak diisi: jadwal = gaN terbaru (lalu greedyN), hasil = SUMBER pada status folder jadwal."""
    base = Path(__file__).resolve().parent.parent / "data"
    if jadwal is None:
        cand = []
        for jenis in ("ga", "greedy"):
            for f in (base / jenis).glob(f"{jenis}[0-9]*"):
                n = re.fullmatch(rf"{jenis}(\d+)", f.name)
                if n and list(f.glob("jadwal_*.csv")):
                    cand.append((jenis == "ga", int(n.group(1)), f))
        if not cand:
            sys.exit(f"Tidak ada folder jadwal di {base}\\ga atau {base}\\greedy. Isi --jadwal.")
        jadwal = list(max(cand, key=lambda c: (c[0], c[1]))[2].glob("jadwal_*.csv"))[0]
        print(f"Jadwal otomatis: {jadwal}")
    jadwal = Path(jadwal)
    if jadwal.is_dir():
        csvs = list(jadwal.glob("jadwal_*.csv"))
        if not csvs:
            sys.exit(f"Tidak ada jadwal_*.csv di {jadwal}")
        jadwal = csvs[0]
    if hasil is None:
        sumber = None
        for st in jadwal.parent.glob("status*.txt"):
            for ln in st.read_text(encoding="utf-8").splitlines():
                if ln.startswith("SUMBER:"):
                    sumber = ln.split(":", 1)[1].strip()
        if not sumber:
            sys.exit("SUMBER tidak ditemukan pada status; isi --hasil, mis. --hasil data\\bersih\\hasil5")
        hasil = base / "bersih" / sumber
        print(f"Data bersih otomatis: {hasil}")
    return str(jadwal), str(hasil)


def tabel_peta(d, iv):
    m = defaultdict(list)
    for kid in sorted(iv):
        for x in d.kelas_dosen.get(kid, ()):
            m[x].append(kid)
    return m


def ids_pertama(iv):
    return sorted(iv)[0]


if __name__ == "__main__":
    sys.exit(main())