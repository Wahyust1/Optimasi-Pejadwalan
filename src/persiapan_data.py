#!/usr/bin/env python3
"""Pipeline pembersih data penjadwalan JTE Unila (v2).

- Data mentah hanya dibaca.
- Keputusan manual ditulis di <ref>/keputusan.csv (tidak pernah ditimpa; hanya ditambah baris baru).
- Setiap run menulis ke folder baru: <out>/hasil1/, <out>/hasil2/, ... dengan berkas bernomor (kelas1.csv, dst).
  Tidak ada berkas keluaran yang ditimpa.

    python src/cleaner.py --raw data/raw --ref data/referensi --out data/bersih
"""
import argparse
import csv
import difflib
import re
import sys
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path

import openpyxl

TITLES = {"prof", "dr", "ir", "ing", "eng", "drs", "dra", "h", "hj", "dipl"}
HARI = ["Senin", "Selasa", "Rabu", "Kamis", "Jumat"]
EVEN = {2, 4, 6, 8}
JENIS_OK = {"wajib", "konsentrasi", "pilihan"}


# ----------------------------------------------------------------- util
def ws(s):
    return re.sub(r"\s+", " ", str(s if s is not None else "").replace(" ", " ").replace("\n", " ")).strip()


def slug(s):
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode().casefold()
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")


def sniff_delim(path):
    """Excel berbahasa Indonesia menyimpan CSV dengan titik koma; deteksi otomatis."""
    with open(path, encoding="utf-8-sig", newline="") as f:
        head = f.readline()
    return ";" if head.count(";") > head.count(",") else ","


def read_csv(path):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return list(csv.reader(f, delimiter=sniff_delim(path)))


def write_csv(path, header, rows, delim=","):
    with open(path, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, delimiter=delim)
        w.writerow(header)
        w.writerows(rows)


def to_num(x):
    x = ws(x).replace(",", ".")
    try:
        return float(x)
    except ValueError:
        return None


def split_list(v):
    return [x for x in re.split(r"[;,\s]+", v) if x]


def parse_kv(v):
    """'sem=4;jenis=pilihan' atau '4' -> dict(sem=4, jenis=...)."""
    out = {}
    for part in re.split(r"[;|]", v):
        part = ws(part)
        if not part:
            continue
        if re.fullmatch(r"\d", part):
            out["sem"] = int(part)
        elif "=" in part:
            a, b = [ws(x).casefold() for x in part.split("=", 1)]
            if a in ("sem", "semester") and b.isdigit():
                out["sem"] = int(b)
            elif a == "jenis" and b in JENIS_OK:
                out["jenis"] = b
        elif part.casefold() in JENIS_OK:
            out["jenis"] = part.casefold()
    return out


# ----------------------------------------------------------- nama mata kuliah
def parse_course(raw):
    s = ws(raw)
    low = s.casefold().replace("pendidikankewarganegaraan", "pendidikan kewarganegaraan")
    flags, variant, label, ptag = set(), "", "", ""
    m = re.search(r"\(([^)]*)\)\s*$", low)
    if m:
        flags.add("tag:" + m.group(1).strip())
        low = low[: m.start()].strip()
    if re.search(r"mbkm\s*$", low):
        flags.add("mbkm")
        low = re.sub(r"\s*-?\s*mbkm\s*$", "", low).strip(" -")
    if re.match(r"^(praktikum|prak\.?)\s", low):
        flags.add("praktikum")
    m = re.search(r"\s*-\s*st$", low)
    if m:
        variant = "st"
        low = low[: m.start()].strip()
    m = re.match(r"^(.*\S)\s+([a-f])$", low)
    if m:
        low, label = m.group(1), m.group(2).upper()
    m = re.match(r"^(.*\S)\s+(ttl|elkaken|telti)$", low)
    if m:
        low, variant = m.group(1), (variant + m.group(2)).strip()
    m = re.match(r"^(.*\S)\s+(te|ti)$", low)
    if m:
        low, ptag = m.group(1), m.group(2).upper()
    return dict(base=low.strip(" -"), label=label, variant=variant, flags=flags, ptag=ptag)


def display_name(raw):
    s = ws(raw)
    s = re.sub(r"\s*\([^)]*\)\s*$", "", s)
    s = re.sub(r"\s*-?\s*MBKM$", "", s, flags=re.I)
    s = re.sub(r"\s*-\s*ST$", "", s, flags=re.I)
    s = re.sub(r"\s+[A-F]$", "", s)
    s = re.sub(r"\s+(TTL|Elkaken|Telti)$", "", s, flags=re.I)
    s = re.sub(r"\s+(TE|TI)$", "", s)
    return s.strip(" -")


def cur_key(name):
    s = ws(name).casefold().replace("*", "")
    s = re.sub(r"\s*\(.*?\)", "", s)
    return ws(s)


# ------------------------------------------------------------------- dosen
def person_tokens(raw):
    for p in ws(raw).split(","):
        toks = [t for t in re.split(r"[\s.]+", p.casefold()) if t]
        toks = [t for t in toks if t not in TITLES]
        if toks:
            return toks
    return []


def _tok_eq(x, y):
    return x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))


def loose_match(A, B):
    i = j = 0
    while i < len(A) and j < len(B):
        if _tok_eq(A[i], B[j]):
            i += 1
            j += 1
        elif len(A[i]) == 1:
            i += 1
        elif len(B[j]) == 1:
            j += 1
        else:
            return False
    return all(len(t) == 1 for t in A[i:] + B[j:])


class Dosen:
    def __init__(self, did, nama, toks, prodi, status):
        self.id, self.nama, self.toks = did, nama, toks
        self.prodi = {prodi} if prodi else set()
        self.status = status
        self.mentah = {nama}
        self.kelas = set()

    @property
    def key(self):
        return "".join(self.toks)


class DosenMaster:
    def __init__(self):
        self.items = []
        self.by_key = {}
        self.used_ids = set()

    def _new_id(self, toks):
        base = "D-" + slug(" ".join(toks))
        did, n = base, 2
        while did in self.used_ids:
            did, n = f"{base}-{n}", n + 1
        self.used_ids.add(did)
        return did

    def by_id(self, did):
        return next((d for d in self.items if d.id == did), None)

    def find(self, toks):
        k = "".join(toks)
        if k in self.by_key:
            return self.by_key[k], "exact"
        loose = [d for d in self.items if loose_match(toks, d.toks)]
        if len(loose) == 1:
            return loose[0], "loose"
        return None, None

    def add(self, raw, prodi, status, force=False):
        toks = person_tokens(raw)
        if not force:
            d, _ = self.find(toks)
            if d:
                if prodi:
                    d.prodi.add(prodi)
                d.mentah.add(ws(raw))
                if len(ws(raw)) > len(d.nama) and status == "terdaftar":
                    d.nama = ws(raw)
                return d
        d = Dosen(self._new_id(toks), ws(raw), toks, prodi, status)
        self.items.append(d)
        self.by_key["".join(toks)] = d
        return d

    def suggest(self, toks):
        k = "".join(toks)
        scored = []
        for d in self.items:
            r = difflib.SequenceMatcher(None, k, d.key).ratio()
            bonus = 0.3 if toks and d.toks and toks[0] == d.toks[0] else 0
            scored.append((r + bonus, r, d))
        scored.sort(key=lambda x: -x[0])
        return [(r, d) for s, r, d in scored[:2] if r >= 0.62 or s >= 0.7]


# -------------------------------------------------------------- kurikulum
def load_kur_pste(path):
    wb = openpyxl.load_workbook(path, data_only=True)
    ent = {}
    peer_sheets = [n for n in wb.sheetnames if n not in ("Ringkasan", "MK Wajib Bersama (Sem 1-3)")]
    for name in wb.sheetnames:
        if name == "Ringkasan":
            continue
        for r in wb[name].iter_rows(values_only=True):
            if not (isinstance(r[0], int) and r[2]):
                continue
            kode, nama, wp, sks, sem = ws(r[1]), ws(r[2]), ws(r[3]), r[4], r[7]
            if kode in ("", "-"):
                continue
            e = ent.setdefault((kode, cur_key(nama)), dict(prodi="PSTE", kode=kode, nama=re.sub(r"\*+$", "", nama), sks=sks,
                                                          sems=set(), wp=set(), sheets=set(), pil=False))
            if isinstance(sem, int):
                e["sems"].add(sem)
            e["wp"].add(wp)
            e["sheets"].add(name)
            if wp == "P":
                e["pil"] = True
    for e in ent.values():
        if e["pil"] and "W" not in e["wp"]:
            e["jenis"] = "pilihan"
        elif "MK Wajib Bersama (Sem 1-3)" in e["sheets"] or len(e["sheets"] & set(peer_sheets)) == len(peer_sheets):
            e["jenis"] = "wajib"
        else:
            e["jenis"] = "konsentrasi"
    return list(ent.values())


def load_kur_psti(path):
    ent = {}
    sem, mode = None, "wajib"
    for r in read_csv(path):
        r = (r + ["", "", ""])[:3]
        a, b, c = ws(r[0]), ws(r[1]), ws(r[2])
        if re.match(r"^Semester \d", a):
            sem, mode = int(a.split()[1]), "wajib"
        elif a.lower().startswith("mata kuliah pilihan"):
            sem, mode = None, "pilihan"
        elif a.lower() == "mata kuliah wajib konsentrasi":
            mode = "konsentrasi"
        elif re.match(r"^[A-Z]{3}\w+\*?$", a) and b:
            e = ent.setdefault((a, cur_key(b)), dict(prodi="PSTI", kode=a, nama=b, sks=to_num(c), sems=set(), jenis=mode))
            if sem:
                e["sems"].add(sem)
    return list(ent.values())


# -------------------------------------------------------------------- BKD
def prodi_from_code(kode, ket):
    k = kode.upper()
    if k.startswith("INF") or k.startswith("UNI625108"):
        return ["PSTI"]
    if k.startswith(("ELT825", "UNI825")):
        return ["MTE"]
    if k.startswith(("EET", "EEC", "ELT625")):
        return ["PSTE"]
    k2 = ws(ket).upper()
    return [k2] if k2 in ("PSTE", "PSTI", "MTE") else []


def parse_bkd(path, sumber):
    rows, cur = [], None
    for i, r in enumerate(read_csv(path), start=1):
        r = (r + [""] * 8)[:8]
        if ws(r[1]) == "Nama Dosen" or ws(r[3]) == "Mata Kuliah":
            continue
        if ws(r[1]):
            cur = ws(r[1])
        if not ws(r[3]) or ws(r[2]) == "Total SKS" or ws(r[0]) == "Total SKS":
            continue
        rows.append(dict(dosen=cur, kode=ws(r[2]), mk=ws(r[3]), sks=to_num(r[4]), ket=ws(r[7]), sumber=sumber, line=i))
    return rows


def parse_bkd_univ(path):
    rows = []
    for i, r in enumerate(read_csv(path), start=1):
        r = (r + [""] * 14)[:14]
        for off, prodi in ((0, "PSTI"), (8, "PSTE")):
            nama = ws(r[off])
            if not nama or nama in ("PSTI", "PSTE"):
                continue
            for d in (r[off + 4], r[off + 5]):
                if ws(d):
                    rows.append(dict(dosen=ws(d), kode="", mk=nama, sks=to_num(r[off + 1]), ket=prodi,
                                     sumber="bkd_univ.csv", line=i))
    return rows


# ------------------------------------------------------------------- ref
BLOK_DEF = [("Senin-Kamis", "pagi", "07:30", 6), ("Senin-Kamis", "sore", "13:00", 5),
            ("Jumat", "pagi", "07:30", 5), ("Jumat", "sore", "13:30", 5)]
KEP_HEADER = ["kunci", "jenis", "nilai_mentah", "usulan", "keputusan", "catatan", "run_pertama", "tingkat"]


def hm(t, add):
    h, m = map(int, t.split(":"))
    tot = h * 60 + m + add
    return f"{tot // 60:02d}:{tot % 60:02d}"


def ensure_ref(REF):
    f = REF / "alias_mk.csv"
    if not f.exists():
        write_csv(f, ["konteks", "nama_mentah", "prodi", "sks", "nama_baku", "catatan"], [
            ["kurikulum", "pendidikan agama islam", "", "", "pendidikan agama", "nama kurikulum tanpa agama"],
            ["bkd", "matdis", "PSTI", "3", "matematika diskrit", "BKD tanpa kode, disetujui dipetakan dari nama"],
            ["bkd", "kalkulus", "PSTE", "3", "kalkulus 2", "BKD tanpa kode, 3 SKS = Kalkulus 2"],
        ])
    f = REF / "blok_waktu.csv"
    if not f.exists():
        rows, n = [], 0
        for grp, sesi, start, jml in BLOK_DEF:
            for h in (HARI[:4] if grp == "Senin-Kamis" else ["Jumat"]):
                for u in range(jml):
                    n += 1
                    rows.append([f"B{n:02d}", h, sesi, u + 1, hm(start, 50 * u), hm(start, 50 * (u + 1)), "semua"])
        write_csv(f, ["blok_id", "hari", "sesi", "urutan", "jam_mulai", "jam_selesai", "prodi_diizinkan"], rows)
    f = REF / "aturan_ruangan.csv"
    if not f.exists():
        simultan = [4, 2, 1, 1, 1, 2, 1]
        rows = [[f"R0{i}", "semua", "semua", "semua", simultan[i - 1]] for i in range(1, 8)]
        rows += [[rid, "Senin", "pilihan;konsentrasi", "semua", 1] for rid in ("R08", "R09")]
        write_csv(f, ["ruangan_id", "hari_diizinkan", "jenis_mk_diizinkan", "prodi_diizinkan", "maks_kelas_simultan"], rows)
    f = REF / "keputusan.csv"
    if not f.exists():
        write_csv(f, KEP_HEADER, [])


def next_run(OUT):
    """Nomor run berikutnya: dari folder hasilN dan (untuk run lama) berkas bernomor di folder OUT."""
    nums = []
    for p in OUT.iterdir():
        m = re.match(r"^hasil(\d+)$", p.name) if p.is_dir() else re.match(r"^.*?(\d+)\.(csv|txt)$", p.name)
        if m:
            nums.append(int(m.group(1)))
    return max(nums, default=0) + 1


# ------------------------------------------------------------------- main
def main():
    base = Path(__file__).resolve().parent.parent / "data"
    ap = argparse.ArgumentParser()
    ap.add_argument("--raw", default=str(base / "raw"))
    ap.add_argument("--ref", default=str(base / "referensi"))
    ap.add_argument("--out", default=str(base / "bersih"))
    a = ap.parse_args()
    if not Path(a.raw).is_dir():
        sys.exit(f"Folder data mentah tidak ditemukan: {a.raw}")
    RAW, REF, OUT = Path(a.raw), Path(a.ref), Path(a.out)
    OUT.mkdir(parents=True, exist_ok=True)
    REF.mkdir(parents=True, exist_ok=True)
    ensure_ref(REF)
    N = next_run(OUT)
    RUN_DIR = OUT / f"hasil{N}"
    RUN_DIR.mkdir(parents=True, exist_ok=False)
    written = []

    def O(name):
        stem, ext = name.rsplit(".", 1)
        p = RUN_DIR / f"{stem}{N}.{ext}"
        written.append(p.name)
        return p

    # ---- keputusan manual (persisten)
    kep_path = REF / "keputusan.csv"
    kep_delim = sniff_delim(kep_path)
    kep_rows = read_csv(kep_path)
    kep_hdr = kep_rows[0] if kep_rows else KEP_HEADER
    KEP = {}
    for r in kep_rows[1:]:
        d = dict(zip(kep_hdr, r + [""] * (len(kep_hdr) - len(r))))
        if d.get("kunci"):
            KEP[ws(d["kunci"]).casefold()] = d

    T, APPLIED, NEW = [], [], {}

    def kunci_of(jenis, k):
        return ws(f"{jenis}|{k}").casefold()

    def dec(jenis, k):
        r = KEP.get(kunci_of(jenis, k))
        v = ws(r["keputusan"]) if r else ""
        return v or None

    def tj(jenis, sumber, nilai, usulan="", tingkat="verifikasi", status="belum_diputuskan", k=None):
        kk = k if k is not None else nilai
        v = dec(jenis, kk)
        if v:
            APPLIED.append([kunci_of(jenis, kk), jenis, nilai, v])
            return v
        key = kunci_of(jenis, kk)
        T.append([key, tingkat, jenis, sumber, nilai, usulan, status])
        if tingkat != "info":
            NEW[key] = (jenis, nilai, usulan, tingkat)
        return None

    stat = {}

    # ---- referensi umum
    alias_mk = [dict(zip(["konteks", "nama_mentah", "prodi", "sks", "nama_baku", "catatan"], r + [""] * 6))
                for r in read_csv(REF / "alias_mk.csv")[1:] if r]

    # ---- kelas mentah
    kelas, dikecualikan, seen = [], [], {}
    for prodi in ("PSTE", "PSTI", "MTE"):
        rows = read_csv(RAW / f"kelas_{prodi.lower()}.csv")
        listed_total, sum_sks, n_rows = None, 0, 0
        for i, r in enumerate(rows[1:], start=2):
            r = (r + ["", "", ""])[:3]
            if ws(r[1]) == "Total SKS":
                listed_total = to_num(r[2])
                continue
            if not ws(r[0]).isdigit():
                continue
            n_rows += 1
            sks = int(to_num(r[2]))
            sum_sks += sks
            p = parse_course(r[1])
            k = dict(prodi=prodi, raw=ws(r[1]), sks=sks, line=i, **p)
            k["mk_key"] = (prodi, p["base"], p["variant"])
            k["kelas_id"] = f"{prodi}:{slug(p['base'])}{'~' + p['variant'] if p['variant'] else ''}:{p['label'] or '-'}"
            if "mbkm" in p["flags"]:
                dikecualikan.append([f"{prodi}:{i}", prodi, k["raw"], "MBKM"])
            elif "praktikum" in p["flags"]:
                dikecualikan.append([f"{prodi}:{i}", prodi, k["raw"], "Praktikum"])
            elif k["kelas_id"] in seen:
                dikecualikan.append([f"{prodi}:{i}", prodi, k["raw"], "Duplikat"])
                tj("duplikat_kelas", f"kelas_{prodi.lower()}.csv baris {i}", k["raw"],
                   "dibuang. Isi keputusan: setuju. Jika ternyata dua kelas paralel, beri label A dan B di berkas mentah",
                   "verifikasi", "dipakai_sementara", k=k["kelas_id"])
            else:
                seen[k["kelas_id"]] = k
                kelas.append(k)
        stat[f"kelas_mentah_{prodi}"] = n_rows
        if listed_total is not None and int(listed_total) != sum_sks:
            tj("total_sks_tidak_cocok", f"kelas_{prodi.lower()}.csv", f"tertulis {listed_total:.0f}, hitung {sum_sks}",
               "cek apakah ada kelas yang hilang. Isi keputusan: setuju jika sudah dicek", "verifikasi", k=prodi)
    stat["kelas_akhir"] = len(kelas)
    stat["kelas_dikecualikan"] = len(dikecualikan)

    # ---- kurikulum
    kur = load_kur_pste(RAW / "kurikulum_pste.xlsx") + load_kur_psti(RAW / "kurikulum_psti.csv")
    kur_by_name, kur_by_code = defaultdict(list), defaultdict(list)
    for e in kur:
        kur_by_name[(e["prodi"], cur_key(e["nama"]))].append(e)
        kur_by_code[(e["prodi"], e["kode"])].append(e)
    stat["kurikulum_entri"] = len(kur)

    # ---- dosen dari berkas dosen
    master = DosenMaster()
    for prodi in ("PSTI", "PSTE", "MTE"):
        for r in read_csv(RAW / f"dosen_{prodi.lower()}.csv")[1:]:
            if len(r) > 1 and ws(r[1]):
                master.add(r[1], prodi, "terdaftar")
    stat["dosen_terdaftar"] = len(master.items)

    # ---- BKD
    bkd_rows = (parse_bkd(RAW / "bkd.csv", "bkd.csv") + parse_bkd(RAW / "bkdpste.csv", "bkdpste.csv")
                + parse_bkd(RAW / "bkdpsti.csv", "bkdpsti.csv") + parse_bkd_univ(RAW / "bkd_univ.csv"))
    stat["bkd_baris"] = len(bkd_rows)

    idx = defaultdict(dict)
    for k in kelas:
        idx[k["mk_key"]][k["label"]] = k
    mk_labels = {mk: sorted(v) for mk, v in idx.items()}

    def resolve(raw, prodi_hint):
        toks = person_tokens(raw)
        key = "".join(toks)
        v = dec("dosen_tidak_terdaftar", key) or dec("dosen_nama_mirip", key)
        if v:
            lv = v.casefold()
            d = master.by_id(v)
            if d:
                return d, "diputuskan"
            if lv in ("baru", "tolak"):
                return master.add(raw, prodi_hint, "ditambah_manual", force=True), "diputuskan"
            if lv == "setuju":
                d, _ = master.find(toks)
                if not d:
                    sug = master.suggest(toks)
                    d = sug[0][1] if sug else None
                if d:
                    return d, "diputuskan"
            tj("keputusan_tidak_valid", "keputusan.csv", raw,
               f'nilai "{v}" tidak dikenal: isi ID dosen, baru, atau setuju', "blokir", k=key)
        d, how = master.find(toks)
        if d:
            return d, ("exact" if how == "exact" else "provisional")
        sug = master.suggest(toks)
        if sug and sug[0][0] >= 0.95:
            return sug[0][1], "provisional"
        return None, "tidak_ketemu"

    assign = defaultdict(lambda: defaultdict(set))
    unlabeled = defaultdict(list)
    skip_n, orphan = Counter(), []
    kode_per_mk = defaultdict(set)
    resolved_log = {}

    for row in bkd_rows:
        p = parse_course(row["mk"])
        if "mbkm" in p["flags"] or "praktikum" in p["flags"]:
            skip_n[row["sumber"]] += 1
            continue
        prodis = prodi_from_code(row["kode"], row["ket"])
        base, label, variant = p["base"], p["label"], p["variant"]
        if row["kode"].upper().startswith("EEC616") and not variant:
            variant = "st"
        if not row["kode"]:
            for al in alias_mk:
                if al["konteks"] == "bkd" and al["nama_mentah"] == base and (
                        not al["sks"] or float(al["sks"]) == (row["sks"] or 0)):
                    base = al["nama_baku"]
                    if al["prodi"] and not prodis:
                        prodis = [al["prodi"]]
        d, st = resolve(row["dosen"], prodis[0] if prodis else "")
        if d is None:
            d = master.add(row["dosen"], prodis[0] if prodis else "", "belum_terdaftar")
            st = "tidak_ketemu"
        else:
            d.mentah.add(ws(row["dosen"]))
        if st != "exact":
            resolved_log.setdefault(ws(row["dosen"]), (d, st))
        found = False
        for pr in (prodis or ["PSTE", "PSTI", "MTE"]):
            mk = (pr, base, variant)
            if mk not in idx:
                continue
            if row["kode"]:
                kode_per_mk[mk].add(row["kode"])
            if label and label in idx[mk]:
                kl = idx[mk][label]
            elif not label and "" in idx[mk]:
                kl = idx[mk][""]
            elif not label:
                unlabeled[(d.id, mk)].append(row)
                found = True
                continue
            else:
                continue
            assign[kl["kelas_id"]][d.id].add(row["sumber"])
            d.kelas.add(kl["kelas_id"])
            found = True
        if not found:
            orphan.append(row)

    # ---- BKD tanpa label
    for (did, mk), rws in unlabeled.items():
        labels = [l for l in mk_labels[mk] if l]
        mkid = f"{mk[0]}:{slug(mk[1])}{'~' + mk[2] if mk[2] else ''}"
        dsn = master.by_id(did)
        kk = f"{did}|{mkid}"
        v = dec("bkd_tanpa_label", kk)
        status = "belum_diputuskan"
        if v:
            lv = v.casefold()
            if lv in ("abaikan", "tolak"):
                chosen = []
            elif lv == "setuju":
                chosen = labels
            else:
                chosen = [x.upper() for x in split_list(v) if x.upper() in labels]
                if not chosen:
                    tj("keputusan_tidak_valid", "keputusan.csv", kk, f'label "{v}" tidak ada ({", ".join(labels)})',
                       "blokir", k=kk + "|valid")
        elif len(rws) == len(labels):
            chosen, status = labels, "dipakai_sementara"
        else:
            chosen = []
        for l in chosen:
            kl = idx[mk][l]
            assign[kl["kelas_id"]][did].add("tanpa_label:" + rws[0]["sumber"])
            dsn.kelas.add(kl["kelas_id"])
        tj("bkd_tanpa_label", f"{rws[0]['sumber']} baris {rws[0]['line']}", f"{dsn.nama} | {rws[0]['mk']} x{len(rws)}",
           f"paralel tersedia: {', '.join(labels)}. Isi keputusan: setuju (semua paralel), abaikan, atau label mis. A;B",
           "verifikasi", status, k=kk)

    # ---- keputusan nama dosen
    for raw, (d, st) in sorted(resolved_log.items()):
        key = "".join(person_tokens(raw))
        if st == "diputuskan":
            tj("dosen_tidak_terdaftar" if dec("dosen_tidak_terdaftar", key) else "dosen_nama_mirip", "BKD", raw, k=key)
        elif st == "provisional":
            tj("dosen_nama_mirip", "BKD", raw, f"dipakai sementara sebagai {d.id} ({d.nama}). Isi keputusan: setuju atau tolak",
               "verifikasi", "dipakai_sementara", k=key)
        elif st == "tidak_ketemu":
            sug = master.suggest(person_tokens(raw))
            hint = "; ".join(f"{dd.id} (mirip {r:.2f})" for r, dd in sug if dd.id != d.id)
            tj("dosen_tidak_terdaftar", "BKD", raw,
               f"kandidat: {hint or '-'}. Isi keputusan: ID dosen yang benar, atau baru (dosen baru), atau setuju (kandidat pertama)",
               "blokir", k=key)
    stat["dosen_total"] = len(master.items)

    # ---- BKD tanpa kelas
    orph = Counter((r["dosen"], r["mk"]) for r in orphan)
    tagged = Counter((r["dosen"], r["mk"]) for r in orphan if any(f.startswith("tag:") for f in parse_course(r["mk"])["flags"]))
    for (dn, mkn), n in sorted(tagged.items()):
        tj("bkd_penanda_tidak_dikenal", "BKD", f"{dn} | {mkn} x{n}",
           "jelaskan arti penanda dalam kurung dan apakah kelasnya masuk dataset. Isi keputusan: abaikan setelah ditinjau",
           "verifikasi", k=f"{dn}|{mkn}")
    stat["bkd_tanpa_kelas"] = len(orph)

    # ---- mata kuliah
    mks = {}
    for k in kelas:
        mks.setdefault(k["mk_key"], []).append(k)
    mk_rows, mk_info, sks_conflict = [], {}, []
    for mk, ks in sorted(mks.items()):
        prodi, base, variant = mk
        mk_id = f"{prodi}:{slug(base)}{'~' + variant if variant else ''}"
        kodes = sorted(kode_per_mk.get(mk, []))
        akey = base
        for al in alias_mk:
            if al["konteks"] == "kurikulum" and al["nama_mentah"] == base:
                akey = al["nama_baku"]
        cand = kur_by_name.get((prodi, akey), [])
        how = "nama" if cand else ""
        if not cand:
            for kd in kodes:
                if kur_by_code.get((prodi, kd)):
                    cand, how = kur_by_code[(prodi, kd)], "kode"
                    break
        sem, jenis, kkur, sks_kur, sem_src = None, "", "", None, ""
        if cand:
            sems = sorted({s for e in cand for s in e["sems"]})
            even = [s for s in sems if s in EVEN]
            sem = even[0] if len(even) == 1 else (sems[0] if len(sems) == 1 else None)
            if len(sems) > 1 and len(even) != 1:
                dv = dec("semester_ambigu", mk_id)
                if dv and parse_kv(dv).get("sem"):
                    sem = parse_kv(dv)["sem"]
                tj("semester_ambigu", f"mk {mk_id}", f"semester kandidat {sems}", "Isi keputusan: angka semester, mis. 4",
                   "verifikasi", k=mk_id)
            jenis, kkur, sks_kur = cand[0]["jenis"], cand[0]["kode"], cand[0]["sks"]
            sem_src = f"kurikulum_2020({how})"
        if any(re.match(r"^(INF|ELT|UNI)625", kd) for kd in kodes):
            sem_src = "kode_625"
            sem = 2
            jenis = jenis or "wajib"
        needs = not cand and not sem_src
        if needs:
            hint = " Kode seri EET6204xx pada kurikulum PSTE berisi mata kuliah pilihan." if any(
                re.match(r"^EET6204", kd) for kd in kodes) else ""
            dv = dec("mk_tidak_ada_di_kurikulum", mk_id)
            if dv:
                kv = parse_kv(dv)
                sem = kv.get("sem", sem)
                jenis = kv.get("jenis", jenis)
                sem_src = "keputusan"
            tj("mk_tidak_ada_di_kurikulum", f"{prodi} {display_name(ks[0]['raw'])}", f"kode BKD: {', '.join(kodes) or '-'}",
               f"Isi keputusan: sem=<angka>;jenis=<wajib|konsentrasi|pilihan> (salah satu boleh).{hint}", "verifikasi", k=mk_id)
        jenis = jenis or "tidak_diketahui"
        if sem and sem % 2 == 1:
            tj("semester_ganjil_pada_semester_genap", f"{prodi} {base}", f"semester {sem} via {sem_src}",
               "cek apakah memang dibuka pada semester genap. Isi keputusan: setuju", "verifikasi", k=mk_id)
        sks_set = {k["sks"] for k in ks}
        if sks_kur and sks_set != {int(sks_kur)} and sem_src != "kode_625":
            sks_conflict.append((mk_id, sorted(sks_set), sks_kur))
        mk_info[mk] = dict(id=mk_id, sem=sem, jenis=jenis, kode_kur=kkur, kodes=kodes)
        mk_rows.append([mk_id, prodi, display_name(ks[0]["raw"]) + (f" ({variant.upper()})" if variant else ""), variant, kkur,
                        ";".join(kodes), sem if sem else "", jenis, sem_src, sorted(sks_set)[0], len(ks), len(ks), "otomatis"])
    for mk_id, s, sk in sks_conflict:
        tj("sks_beda_dengan_kurikulum", mk_id, f"kelas {s}, kurikulum {sk}", "Isi keputusan: setuju jika SKS kelas yang benar",
           "verifikasi", k=mk_id)

    # ---- kelas final
    kelas_rows = []
    for k in sorted(kelas, key=lambda x: (x["prodi"], x["kelas_id"])):
        m = mk_info[k["mk_key"]]
        k["mk_id"], k["sem"], k["jenis"] = m["id"], m["sem"], m["jenis"]
        kelas_rows.append([k["kelas_id"], k["prodi"], m["id"], k["raw"], k["label"], k["variant"], m["kode_kur"],
                           ";".join(m["kodes"]), m["sem"] or "", m["jenis"], k["sks"], k["sks"]])

    # ---- kelas tanpa dosen (keputusan: ID dosen)
    for k in kelas:
        if assign.get(k["kelas_id"]):
            continue
        v = dec("kelas_tanpa_dosen", k["kelas_id"])
        if v:
            ids = [x for x in split_list(v) if master.by_id(x)]
            if ids:
                for did in ids:
                    assign[k["kelas_id"]][did].add("keputusan")
                    master.by_id(did).kelas.add(k["kelas_id"])
            else:
                tj("keputusan_tidak_valid", "keputusan.csv", k["kelas_id"], f'ID dosen "{v}" tidak dikenal', "blokir",
                   k=k["kelas_id"] + "|valid")
    no_dosen = [k for k in kelas if not assign.get(k["kelas_id"])]
    for k in kelas:
        kid = k["kelas_id"]
        if k in no_dosen:
            tj("kelas_tanpa_dosen", f"{k['prodi']} {k['raw']}", kid,
               "Isi keputusan: ID dosen (pisahkan dengan ;), atau perbaiki BKD", "blokir", k=kid)
        elif dec("kelas_tanpa_dosen", kid):
            tj("kelas_tanpa_dosen", "", kid, k=kid)
    stat["kelas_tanpa_dosen"] = len(no_dosen)

    diff_sumber = 0
    for kid, dd in assign.items():
        a_ = {d for d, s in dd.items() if "bkd.csv" in s}
        b_ = {d for d, s in dd.items() if s & {"bkdpste.csv", "bkdpsti.csv", "bkd_univ.csv"}}
        if a_ and b_ and a_ != b_:
            diff_sumber += 1
    stat["kelas_beda_antar_sumber_bkd"] = diff_sumber

    # ---- kelompok paket
    paket = defaultdict(list)
    for k in kelas:
        if k["jenis"] == "wajib" and k["sem"] in (2, 4) and k["prodi"] in ("PSTE", "PSTI") and k["label"]:
            paket[(k["prodi"], k["sem"], k["label"])].append(k)
    paket_rows = []
    for (pr, sm, lb), ks in sorted(paket.items()):
        for k in sorted(ks, key=lambda x: x["kelas_id"]):
            paket_rows.append([f"{pr}-S{sm}-{lb}", pr, sm, lb, k["kelas_id"], ""])
    unl = [k for k in kelas if k["jenis"] == "wajib" and k["sem"] in (2, 4) and k["prodi"] in ("PSTE", "PSTI") and not k["label"]]
    for k in unl:
        allg = sorted(l for (pr, sm, l) in paket if pr == k["prodi"] and sm == k["sem"])
        v = dec("kelas_wajib_tanpa_label", k["kelas_id"])
        status, grp = "dipakai_sementara", allg
        if v:
            lv = v.casefold()
            if lv in ("tolak", "abaikan"):
                grp = []
            elif lv != "setuju":
                grp = [x.upper() for x in split_list(v) if x.upper() in allg]
        for l in grp:
            paket_rows.append([f"{k['prodi']}-S{k['sem']}-{l}", k["prodi"], k["sem"], l, k["kelas_id"], "gabungan_tanpa_label"])
        tj("kelas_wajib_tanpa_label", f"{k['prodi']} {k['raw']} (semester {k['sem']})", k["kelas_id"],
           f"diasumsikan satu kelas untuk semua paralel ({', '.join(allg)}). Isi keputusan: setuju, tolak (tidak masuk paket), "
           "atau label mis. A;B", "verifikasi", status, k=k["kelas_id"])
    stat["paket_kelompok"] = len(paket)
    stat["paket_kelas_berlabel"] = sum(len(v) for v in paket.values())
    stat["paket_kelas_tanpa_label"] = len(unl)

    # ---- dosen keluaran
    dos_rows = []
    for d in sorted(master.items, key=lambda x: x.nama.casefold()):
        mk_kinds = {kl.split(":")[1] for kl in d.kelas}
        d.univ = bool(d.kelas) and all(x.startswith(("pendidikan-agama", "pendidikan-kewarganegaraan")) for x in mk_kinds)
        dos_rows.append([d.id, d.nama, ";".join(sorted(d.prodi)), "universitas" if d.univ else "", d.status,
                         " | ".join(sorted(d.mentah)), len(d.kelas)])
    write_csv(O("dosen.csv"), ["dosen_id", "nama_baku", "prodi_asal", "unit", "status", "nama_mentah", "jumlah_kelas"], dos_rows)
    for prodi in ("PSTI", "PSTE", "MTE"):
        rows = [[d.id, d.nama, d.status, " | ".join(sorted(d.mentah))] for d in sorted(master.items, key=lambda x: x.nama.casefold())
                if prodi in d.prodi and not d.univ]
        write_csv(O(f"dosen_{prodi.lower()}.csv"), ["dosen_id", "nama_baku", "status", "nama_mentah"], rows)
    write_csv(O("dosen_univ.csv"), ["dosen_id", "nama_baku", "terdaftar_di_berkas", "status"],
              [[d.id, d.nama, ";".join(sorted(d.prodi)), d.status] for d in sorted(master.items, key=lambda x: x.nama.casefold()) if d.univ])
    for d in master.items:
        if d.univ and d.prodi:
            tj("dosen_univ_ada_di_berkas_prodi", ";".join(sorted(d.prodi)), d.nama,
               "hanya mengampu MK universitas; dipindah ke dosen_univ", "info", "dipakai_sementara", k=d.id)
        if not d.kelas and d.status == "terdaftar":
            tj("dosen_terdaftar_tanpa_kelas", ";".join(sorted(d.prodi)), d.nama, "tidak muncul di BKD kelas yang dibuka", "info",
               "dipakai_sementara", k=d.id)
    stat["dosen_lintas_prodi"] = sum(1 for d in master.items if len(d.prodi) > 1 and d.status == "terdaftar")
    stat["dosen_mengampu_kelas"] = sum(1 for d in master.items if d.kelas)

    kd_rows = [[kid, did, "", ";".join(sorted(assign[kid][did]))] for kid in sorted(assign) for did in sorted(assign[kid])]
    write_csv(O("kelas_dosen.csv"), ["kelas_id", "dosen_id", "peran", "sumber"], kd_rows)

    # ---- ruangan, blok waktu, aturan
    ruang = read_csv(RAW / "ruangan.csv")[1:]
    write_csv(O("ruangan.csv"), ["ruangan_id", "nama", "kapasitas_peserta_info"], [[r[0], r[1], r[2]] for r in ruang if r])
    blok = read_csv(REF / "blok_waktu.csv")
    write_csv(O("blok_waktu.csv"), blok[0], blok[1:])
    stat["blok_waktu"] = len(blok) - 1
    ar = read_csv(REF / "aturan_ruangan.csv")
    write_csv(O("aturan_ruangan.csv"), ar[0], ar[1:])
    ids_ruang = {r[0] for r in ruang if r}
    # validasi bentuk aturan_ruangan: kolom lengkap dan maks_kelas_simultan berupa bilangan bulat >= 1
    if "maks_kelas_simultan" not in ar[0]:
        tj("aturan_ruangan_rusak", "referensi/aturan_ruangan.csv", "kolom maks_kelas_simultan tidak ada",
           "perbaiki berkas referensi (hapus boleh_gabung/boleh_paralel, tambah maks_kelas_simultan)", "blokir", k="kolom")
    else:
        ci = ar[0].index("maks_kelas_simultan")
        for n, r in enumerate(ar[1:], start=2):
            if not r:
                continue
            ok = len(r) == len(ar[0]) and r[ci].strip().isdigit() and int(r[ci]) >= 1
            if not ok:
                tj("aturan_ruangan_rusak", "referensi/aturan_ruangan.csv", f"baris {n}: {','.join(r)}",
                   "jumlah kolom harus sama dengan header (cek koma yang hilang) dan maks_kelas_simultan bilangan bulat >= 1",
                   "blokir", k=f"baris{n}")
    if {r[0] for r in ar[1:] if r} != ids_ruang:
        tj("aturan_ruangan_tidak_lengkap", "referensi/aturan_ruangan.csv", "ruangan_id tidak sama dengan ruangan.csv",
           "perbaiki berkas referensi", "blokir")
    if not any(len(r) > 1 and r[1] == "Sabtu" for r in blok[1:]):
        tj("blok_sabtu_belum_ada", "referensi/blok_waktu.csv", "blok Sabtu (khusus MTE) belum didefinisikan",
           "tambahkan baris Sabtu di blok_waktu.csv dengan prodi_diizinkan=MTE, atau isi keputusan: tidak_ada", "verifikasi", k="-")

    # ---- berkas utama
    write_csv(O("kelas.csv"), ["kelas_id", "prodi", "mk_id", "nama_kelas_mentah", "label_paralel", "varian", "kode_kurikulum",
                               "kode_bkd", "semester", "jenis_mk", "sks", "durasi_blok"], kelas_rows)
    write_csv(O("mata_kuliah.csv"), ["mk_id", "prodi", "nama_mk", "varian", "kode_kurikulum", "kode_bkd", "semester", "jenis_mk",
                                     "sumber_semester", "sks", "jumlah_paralel", "kapasitas_gabung", "sumber_kapasitas"], mk_rows)
    write_csv(O("kelompok_paket.csv"), ["paket_id", "prodi", "semester", "label", "kelas_id", "catatan"], paket_rows)
    write_csv(O("kelas_dikecualikan.csv"), ["kelas_id_mentah", "prodi", "nama_kelas", "alasan"], dikecualikan)

    # ---- kelayakan awal
    fe = []

    def n_blok_for(prodis):
        """Jumlah blok per minggu yang boleh dipakai oleh salah satu prodi pada himpunan prodis."""
        n = 0
        for r in blok[1:]:
            if len(r) < 7:
                continue
            izin = {x.upper() for x in split_list(r[6])}
            if "SEMUA" in izin or izin & set(prodis):
                n += 1
        return n

    n_blok = n_blok_for(set())                       # blok yang terbuka untuk semua prodi
    n_blok_mte = n_blok_for({"MTE"})                 # termasuk blok khusus MTE (Sabtu)
    total_ruang = len(ids_ruang) * n_blok
    kelas_non_mte = [k for k in kelas if k["prodi"] != "MTE"]
    sum_sks = sum(k["sks"] for k in kelas)
    sum_sks_non_mte = sum(k["sks"] for k in kelas_non_mte)
    min_sks = sum(ks[0]["sks"] for ks in mks.values())
    fe.append(["total_blok_ruangan_per_minggu_semua_prodi", total_ruang, f"{len(ids_ruang)} ruangan x {n_blok} blok"])
    fe.append(["blok_per_minggu_untuk_mte", n_blok_mte, "termasuk blok khusus MTE"])
    fe.append(["kebutuhan_blok_tanpa_penggabungan", sum_sks, "jumlah SKS seluruh kelas"])
    fe.append(["kebutuhan_blok_paralel_digabung_penuh", min_sks, "satu sesi per mata kuliah"])
    fe.append(["rasio_tanpa_gabung_non_mte", round(sum_sks_non_mte / total_ruang, 3) if total_ruang else "",
               ">1 berarti kelas PSTE+PSTI tidak muat tanpa penggabungan"])
    kmap = {k["kelas_id"]: k for k in kelas}
    load = defaultdict(dict)
    for kid, dd in assign.items():
        for did in dd:
            load[did][kid] = kmap[kid]
    for did, cls in load.items():
        batas = n_blok_for({k["prodi"] for k in cls.values()})
        mx = sum(k["sks"] for k in cls.values())
        mn = sum({k["mk_key"]: k["sks"] for k in cls.values()}.values())
        if mn > batas:
            tj("beban_dosen_melebihi_blok", master.by_id(did).nama, f"min {mn} blok > {batas}", "", "blokir", k=did)
        if mx > batas:
            fe.append([f"dosen_butuh_penggabungan:{did}", mx, f"maks {mx} blok jika tak digabung; min {mn}; batas {batas}"])
    for (pr, sm, lb), ks in paket.items():
        s = sum(k["sks"] for k in ks)
        batas = n_blok_for({pr})
        fe.append([f"paket:{pr}-S{sm}-{lb}", s, f"{len(ks)} kelas, total {s} SKS (batas {batas})"])
        if s > batas:
            tj("beban_paket_melebihi_blok", f"{pr}-S{sm}-{lb}", f"{s} SKS", "", "blokir")
    write_csv(O("laporan_kelayakan_awal.csv"), ["indikator", "nilai", "keterangan"], fe)

    # ---- tinjauan, keputusan, laporan
    write_csv(O("bkd_tanpa_kelas.csv"), ["dosen_mentah", "mata_kuliah_bkd", "jumlah_baris"],
              [[d, m, n] for (d, m), n in sorted(orph.items())])
    T.sort(key=lambda t: ({"blokir": 0, "verifikasi": 1, "info": 2}[t[1]], t[2], t[4]))
    write_csv(O("tinjauan_manual.csv"), ["kunci", "tingkat", "jenis", "sumber", "nilai_mentah", "usulan", "status"], T)
    write_csv(O("keputusan_diterapkan.csv"), ["kunci", "jenis", "nilai_mentah", "keputusan"], APPLIED)
    # arsip: salinan utuh keputusan.csv persis seperti yang dipakai run ini
    # (sebelum baris baru ditambahkan), supaya isi keputusan tiap run tetap tersimpan
    O("keputusan_dipakai.csv").write_bytes(kep_path.read_bytes())

    newd = [dict(kunci=key, jenis=j, nilai_mentah=n, usulan=u, keputusan="", catatan="", run_pertama=f"run{N}", tingkat=tg)
            for key, (j, n, u, tg) in NEW.items() if key not in KEP]
    newd.sort(key=lambda d: {"blokir": 0, "verifikasi": 1}.get(d["tingkat"], 2))
    new_rows = [[d.get(c, "") for c in kep_hdr] for d in newd]  # ikuti kolom berkas keputusan yang sudah ada
    kep_note = ""
    if new_rows:
        try:
            raw_bytes = kep_path.read_bytes()
            with open(kep_path, "a", encoding="utf-8", newline="") as f:
                if raw_bytes and not raw_bytes.endswith(b"\n"):
                    f.write("\r\n")
                csv.writer(f, delimiter=kep_delim).writerows(new_rows)
        except PermissionError:
            alt = O("keputusan_baru.csv")
            write_csv(alt, kep_hdr, new_rows)
            kep_note = f"keputusan.csv sedang terkunci (tutup di Excel); baris baru ditulis di {alt.name}, salin manual"
    cnt = Counter((t[2], t[1]) for t in T)
    lap = [[k, v, ""] for k, v in stat.items()] + [[f"tinjauan:{j}", n, tg] for (j, tg), n in sorted(cnt.items())]
    lap.append(["keputusan_diterapkan", len(APPLIED), ""])
    write_csv(O("laporan_temuan.csv"), ["indikator", "jumlah", "keterangan"], lap)

    # ---- validasi integritas
    err = []
    kid_set = {r[0] for r in kelas_rows}
    if len(kid_set) != len(kelas_rows):
        err.append("kelas_id tidak unik")
    mk_set = {r[0] for r in mk_rows}
    err += [f"mk_id hilang: {r[2]}" for r in kelas_rows if r[2] not in mk_set]
    dset = {d.id for d in master.items}
    err += [f"referensi kelas_dosen rusak: {r}" for r in kd_rows if r[0] not in kid_set or r[1] not in dset]
    err += [f"referensi paket rusak: {r}" for r in paket_rows if r[4] not in kid_set]
    if len(kelas_rows) + len(dikecualikan) != sum(v for k, v in stat.items() if k.startswith("kelas_mentah_")):
        err.append("jumlah kelas + dikecualikan != kelas mentah")
    block = sum(1 for t in T if t[1] == "blokir")
    verif = sum(1 for t in T if t[1] == "verifikasi")
    with open(O("status.txt"), "w", encoding="utf-8") as f:
        f.write(f"RUN: {N}\nINTEGRITAS: {'GAGAL' if err else 'OK'}\n"
                f"TINJAUAN_MANUAL: {len(T)}\nBLOKIR: {block}\nVERIFIKASI: {verif}\nINFO: {len(T) - block - verif}\n"
                f"KEPUTUSAN_DITERAPKAN: {len(APPLIED)}\nKEPUTUSAN_BARU_DITAMBAHKAN: {len(new_rows)}\n")
        for judul, tingkat in (("BLOKIR", "blokir"), ("VERIFIKASI", "verifikasi"), ("INFO", "info")):
            f.write(f"\nRINCIAN {judul}\n")
            for t in T:
                if t[1] == tingkat:
                    f.write(f"  {t[2]} | {t[4]}\n")
        f.write("\nRINCIAN KEPUTUSAN BARU (ditambahkan ke keputusan.csv)\n")
        for d in newd:
            f.write(f"  {d['tingkat']} | {d['jenis']} | {d['nilai_mentah']}\n")
        if kep_note:
            f.write(kep_note + "\n")
        for e in err:
            f.write("ERR " + e + "\n")
    print(f"RUN {N} | INTEGRITAS: {'GAGAL' if err else 'OK'} | BLOKIR: {block} | VERIFIKASI: {verif} | "
          f"keputusan diterapkan: {len(APPLIED)} | baris baru di keputusan.csv: {len(new_rows)}")
    if kep_note:
        print("PERINGATAN:", kep_note)
    for e in err[:5]:
        print("ERR", e)
    print(f"Hasil run ini ada di folder: {RUN_DIR}  ({len(written)} berkas)")
    return 1 if (err or block) else 0


if __name__ == "__main__":
    sys.exit(main())