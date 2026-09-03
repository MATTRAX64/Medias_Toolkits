"""
MediaToolkit.py  —  v2
Interface simple et propre : Format Changer / YouTube / Couleurs
- FFmpeg configuré UNE fois dans Paramètres, partagé partout
- Fenêtre bien dimensionnée et centree
- Nécessite : Python 3.8+ (tkinter inclus sur Windows)
"""

import os, sys, json, shutil, threading, subprocess, unicodedata, re, pathlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

# ═══════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════
CFG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "mediatoolkit.json")

DEFAULTS = {
    "ffmpeg":       "",
    "fc_folder":    "",
    "fc_fmt_video": "mp4",
    "fc_fmt_image": "webp",
    "fc_fmt_audio": "mp3",
    "yt_out":       "",
    "col_path":     "",
    "col_mode":     "folder",
    "col_fmt":      "same",
    "col_sat": 0, "col_bri": 0, "col_con": 0, "col_gam": 0,
    "rsz_path":     "",
    "rsz_mode":     "folder",
    "rsz_height":   1080,
    "rsz_fmt":      "same",
    "rsz_suffix":   "_resized",
}

def cfg_load():
    if os.path.isfile(CFG_FILE):
        try:
            d = json.loads(open(CFG_FILE, encoding="utf-8").read())
            c = dict(DEFAULTS); c.update(d); return c
        except Exception:
            pass
    return dict(DEFAULTS)

def cfg_save(c):
    try:
        open(CFG_FILE, "w", encoding="utf-8").write(json.dumps(c, indent=2, ensure_ascii=False))
    except Exception:
        pass

# ═══════════════════════════════════════════════════════
#  UTILITAIRES
# ═══════════════════════════════════════════════════════
def find_ffmpeg(hint=""):
    if hint and os.path.isfile(hint):
        return hint
    found = shutil.which("ffmpeg")
    if found:
        return found
    base = os.path.dirname(os.path.abspath(__file__))
    for n in ("ffmpeg.exe", "ffmpeg"):
        p = os.path.join(base, n)
        if os.path.isfile(p):
            return p
    return ""

def has_gpu(ff):
    try:
        r = subprocess.run([ff, "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=8,
                           creationflags=subprocess.CREATE_NO_WINDOW)
        return "hevc_nvenc" in r.stdout
    except Exception:
        return False

def clean(name, n=80):
    name = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    name = re.sub(r'[\\/:*?"<>|]+', "", re.sub(r'\s+', " ", name)).strip()
    return name[:n]

def ensure_ytdlp():
    try:
        import yt_dlp
        return yt_dlp
    except ImportError:
        pass
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", "yt-dlp"],
                          creationflags=subprocess.CREATE_NO_WINDOW)
    import yt_dlp
    return yt_dlp

# ═══════════════════════════════════════════════════════
#  THÈME
# ═══════════════════════════════════════════════════════
BG   = "#1c1c1e"
CARD = "#2c2c2e"
LINE = "#3a3a3c"
ACC  = "#5e5ce6"
ACC2 = "#7b79f0"
FG   = "#f2f2f7"
FG2  = "#8e8e93"
OK   = "#30d158"
WARN = "#ffd60a"
ERR  = "#ff453a"

F    = ("Segoe UI", 10)
FB   = ("Segoe UI", 10, "bold")
FSM  = ("Segoe UI", 9)
FMO  = ("Consolas", 9)
FTTL = ("Segoe UI", 14, "bold")

# ═══════════════════════════════════════════════════════
#  WIDGETS RÉUTILISABLES
# ═══════════════════════════════════════════════════════
class Btn(tk.Label):
    def __init__(self, p, text="", cmd=None, primary=False, danger=False, **kw):
        bg = ACC if primary else (ERR if danger else CARD)
        fg = FG if primary or danger else FG2
        hv = ACC2 if primary else ("#ff6961" if danger else LINE)
        super().__init__(p, text=text, bg=bg, fg=fg, font=F,
                         cursor="hand2", padx=12, pady=5, **kw)
        self._bg = bg; self._hv = hv; self._cmd = cmd
        self.bind("<Enter>",    lambda e: self.config(bg=self._hv))
        self.bind("<Leave>",    lambda e: self.config(bg=self._bg))
        self.bind("<Button-1>", lambda e: self._cmd() if self._cmd else None)


class PathRow(tk.Frame):
    def __init__(self, p, label="", mode="dir", ftypes=None, on_change=None, **kw):
        bg = CARD
        super().__init__(p, bg=bg, **kw)
        self._mode = mode
        self._ft   = ftypes or [("Tous", "*.*")]
        self._cb   = on_change
        self.var   = tk.StringVar()
        if label:
            tk.Label(self, text=label, bg=bg, fg=FG2, font=FSM,
                     width=13, anchor="w").pack(side="left")
        e = tk.Entry(self, textvariable=self.var, bg=LINE, fg=FG,
                     insertbackground=FG, relief="flat", font=FMO, bd=0)
        e.pack(side="left", fill="x", expand=True, ipady=5, padx=(0, 4))
        e.bind("<KeyRelease>", lambda ev: self._cb() if self._cb else None)
        Btn(self, text="📂", cmd=self._browse).pack(side="left")

    def _browse(self):
        p = (filedialog.askopenfilename(filetypes=self._ft)
             if self._mode == "file" else filedialog.askdirectory())
        if p:
            self.var.set(p)
            if self._cb:
                self._cb()

    def get(self): return self.var.get().strip()
    def set(self, v): self.var.set(v)


class Dot(tk.Frame):
    def __init__(self, p, label="", **kw):
        super().__init__(p, bg=CARD, **kw)
        self._d = tk.Label(self, text="●", fg=LINE, bg=CARD, font=("Segoe UI", 12))
        self._d.pack(side="left")
        self._l = tk.Label(self, text=label, fg=FG2, bg=CARD, font=FSM)
        self._l.pack(side="left", padx=(3, 0))
        self._link = ""
        self._l.bind("<Button-1>", lambda e: self._open())

    def ok(self, t=""):      self._d.config(fg=OK);   self._set(t, FG2)
    def warn(self, t=""):    self._d.config(fg=WARN);  self._set(t, WARN)
    def err(self, t="", url=""):
        self._d.config(fg=ERR); self._set(t, ERR)
        self._link = url
        if url: self._l.config(cursor="hand2")
    def pending(self):       self._d.config(fg=WARN);  self._l.config(fg=FG2)
    def _set(self, t, c):
        if t: self._l.config(text=t, fg=c)
    def _open(self):
        if self._link:
            import webbrowser; webbrowser.open(self._link)


class Log(tk.Frame):
    def __init__(self, p, h=8, **kw):
        super().__init__(p, bg=BG, **kw)
        self._t = tk.Text(self, height=h, bg="#111113", fg=FG2, font=FMO,
                          relief="flat", bd=0, wrap="word", state="disabled",
                          padx=8, pady=6, selectbackground=ACC)
        sb = tk.Scrollbar(self, orient="v", command=self._t.yview,
                          bg=CARD, troughcolor=LINE, width=7)
        self._t.config(yscrollcommand=sb.set)
        self._t.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")

    def write(self, msg):
        self._t.config(state="normal")
        self._t.insert("end", msg + "\n")
        self._t.see("end")
        self._t.config(state="disabled")

    def clear(self):
        self._t.config(state="normal")
        self._t.delete("1.0", "end")
        self._t.config(state="disabled")


def mk_card(parent, title=""):
    outer = tk.Frame(parent, bg=BG)
    outer.pack(fill="x", padx=16, pady=(8, 0))
    if title:
        tk.Label(outer, text=title.upper(), bg=BG, fg=FG2,
                 font=("Segoe UI", 8, "bold"), anchor="w").pack(fill="x", pady=(0, 3))
    c = tk.Frame(outer, bg=CARD)
    c.pack(fill="x")
    inner = tk.Frame(c, bg=CARD)
    inner.pack(fill="x", padx=10, pady=8)
    return inner

def mk_row(parent, label=""):
    f = tk.Frame(parent, bg=CARD)
    f.pack(fill="x", pady=2)
    if label:
        tk.Label(f, text=label, bg=CARD, fg=FG2, font=FSM,
                 width=13, anchor="w").pack(side="left")
    return f

def mk_sep(parent):
    tk.Frame(parent, bg=LINE, height=1).pack(fill="x", padx=16, pady=6)

def mk_menu(parent, var, choices):
    om = tk.OptionMenu(parent, var, *choices)
    om.config(bg=LINE, fg=FG, activebackground=ACC, activeforeground=FG,
              relief="flat", font=FSM, bd=0, highlightthickness=0, cursor="hand2")
    om["menu"].config(bg=CARD, fg=FG, activebackground=ACC, activeforeground=FG, bd=0)
    return om

def scrollable(parent):
    cv = tk.Canvas(parent, bg=BG, highlightthickness=0)
    sb = tk.Scrollbar(parent, orient="v", command=cv.yview,
                      bg=BG, troughcolor=CARD, width=7)
    cv.config(yscrollcommand=sb.set)
    sb.pack(side="right", fill="y")
    cv.pack(fill="both", expand=True)
    inner = tk.Frame(cv, bg=BG)
    inner.bind("<Configure>", lambda e: cv.config(scrollregion=cv.bbox("all")))
    cv.create_window((0, 0), window=inner, anchor="nw")
    cv.bind("<MouseWheel>", lambda e: cv.yview_scroll(-1 * (e.delta // 120), "units"))
    return inner

# ═══════════════════════════════════════════════════════
#  DONNÉES FORMATS
# ═══════════════════════════════════════════════════════
V_SRC = {".mp4",".mkv",".mov",".avi",".webm",".flv",".ts",".m2ts",".wmv",".3gp",".ogv",".m4v",".mpg",".mpeg"}
I_SRC = {".jpg",".jpeg",".png",".webp",".avif",".tiff",".tif",".bmp",".gif",".heic",".jfif"}
A_SRC = {".mp3",".aac",".ogg",".flac",".opus",".wav",".m4a",".wma",".aiff",".ac3"}
ALL   = V_SRC | I_SRC | A_SRC

V_TARGETS = ["mp4","mkv","mov","webm","avi"]
I_TARGETS = ["jpg","png","webp","avif","tiff","bmp","gif"]
A_TARGETS = ["mp3","flac","aac","ogg","opus","wav","m4a"]

VA_GPU = {
    "mp4":  ["-c:v","hevc_nvenc","-cq","0","-c:a","aac","-b:a","320k"],
    "mkv":  ["-c:v","hevc_nvenc","-cq","0","-c:a","flac"],
    "mov":  ["-c:v","hevc_nvenc","-cq","0","-c:a","aac","-b:a","320k"],
    "webm": ["-c:v","libvpx-vp9","-crf","0","-b:v","0","-c:a","libopus","-b:a","320k"],
    "avi":  ["-c:v","libx264","-crf","0","-c:a","mp3","-b:a","320k"],
}
VA_CPU = {
    "mp4":  ["-c:v","libx265","-crf","0","-c:a","aac","-b:a","320k"],
    "mkv":  ["-c:v","libx265","-crf","0","-c:a","flac"],
    "mov":  ["-c:v","libx265","-crf","0","-c:a","aac","-b:a","320k"],
    "webm": ["-c:v","libvpx-vp9","-crf","0","-b:v","0","-c:a","libopus","-b:a","320k"],
    "avi":  ["-c:v","libx264","-crf","0","-c:a","mp3","-b:a","320k"],
}
IA = {
    "jpg": ["-q:v","1"], "png": ["-compression_level","0"],
    "webp":["-q:v","100","-lossless","1"], "avif":["-crf","0"],
    "tiff":[], "bmp":[], "gif":[],
}
AA = {
    "mp3": ["-vn","-c:a","libmp3lame","-q:a","0"],
    "flac":["-vn","-c:a","flac"],
    "aac": ["-vn","-c:a","aac","-b:a","320k"],
    "ogg": ["-vn","-c:a","libvorbis","-q:a","10"],
    "opus":["-vn","-c:a","libopus","-b:a","320k"],
    "wav": ["-vn","-c:a","pcm_s24le"],
    "m4a": ["-vn","-c:a","aac","-b:a","320k"],
}
IMG_Q = {
    "jpg": ["-q:v","1"], "jpeg":["-q:v","1"],
    "png": ["-compression_level","0"],
    "webp":["-q:v","100","-lossless","1"],
    "avif":["-crf","0"],
}
YT_FMTS = {
    "MP3  192 kbps": ("mp3","192","bestaudio/best"),
    "MP3  320 kbps": ("mp3","320","bestaudio/best"),
    "MP4  meilleure":("mp4","best","bestvideo+bestaudio/best"),
    "MP4  1080p":    ("mp4","1080","bestvideo[height<=1080]+bestaudio/best"),
    "MP4  720p":     ("mp4","720","bestvideo[height<=720]+bestaudio/best"),
    "FLAC lossless": ("flac","0","bestaudio/best"),
    "AAC  320k":     ("aac","320","bestaudio/best"),
    "Opus 320k":     ("opus","320","bestaudio/best"),
}

# ═══════════════════════════════════════════════════════
#  ONGLET FORMAT CHANGER
# ═══════════════════════════════════════════════════════
class TabFormat(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._running = False
        self._build()
        self._load()

    def _build(self):
        p = scrollable(self)

        tk.Label(p, text="Format Changer", bg=BG, fg=FG, font=FTTL,
                 anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(p, text="Convertit tous les médias d'un dossier vers les formats choisis.",
                 bg=BG, fg=FG2, font=FSM, anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        c1 = mk_card(p, "Dossier source")
        self._folder = PathRow(c1, mode="dir", on_change=self._save)
        self._folder.pack(fill="x")

        c2 = mk_card(p, "Formats cibles")
        self._fvars = {}
        for lbl, key, choices in [("Vidéo →","fc_fmt_video",V_TARGETS),
                                   ("Image →","fc_fmt_image",I_TARGETS),
                                   ("Audio →","fc_fmt_audio",A_TARGETS)]:
            r = mk_row(c2, lbl)
            v = tk.StringVar(value=self.app.cfg.get(key, choices[0]))
            self._fvars[key] = v
            mk_menu(r, v, choices).pack(side="left", fill="x", expand=True)
            v.trace_add("write", lambda *a: self._save())

        c3 = mk_card(p, "Progression")
        self._lbl = tk.Label(c3, text="En attente", bg=CARD, fg=FG2, font=FSM, anchor="w")
        self._lbl.pack(fill="x", pady=(0, 4))
        self._bar = ttk.Progressbar(c3, mode="determinate", style="A.Horizontal.TProgressbar")
        self._bar.pack(fill="x", pady=(0, 6))
        self._log = Log(c3, h=7)
        self._log.pack(fill="both", expand=True)

        br = tk.Frame(p, bg=BG)
        br.pack(fill="x", padx=16, pady=(10, 16))
        Btn(br, "▶  Lancer la conversion", cmd=self._start, primary=True).pack(side="left")
        Btn(br, "Arrêter", cmd=self._stop, danger=True).pack(side="right")

    def _save(self):
        self.app.cfg["fc_folder"] = self._folder.get()
        for k, v in self._fvars.items():
            self.app.cfg[k] = v.get()
        cfg_save(self.app.cfg)

    def _load(self):
        self._folder.set(self.app.cfg.get("fc_folder", ""))
        for k, v in self._fvars.items():
            val = self.app.cfg.get(k)
            if val:
                v.set(val)

    def _stop(self): self._running = False

    def _start(self):
        if self._running: return
        ff = self.app.cfg.get("ffmpeg", "")
        if not ff or not os.path.isfile(ff):
            messagebox.showerror("FFmpeg manquant",
                                 "FFmpeg n'est pas configuré.\nVa dans l'onglet ⚙ Paramètres.")
            return
        folder = self._folder.get()
        if not os.path.isdir(folder):
            messagebox.showerror("Erreur", "Dossier source introuvable.")
            return
        fv = self._fvars["fc_fmt_video"].get()
        fi = self._fvars["fc_fmt_image"].get()
        fa = self._fvars["fc_fmt_audio"].get()
        self._save(); self._running = True; self._log.clear()
        threading.Thread(target=self._run, args=(folder, ff, fv, fi, fa), daemon=True).start()

    def _run(self, folder, ff, fv, fi, fa):
        gpu = has_gpu(ff)
        files = [
            os.path.join(r, f)
            for r, _, fs in os.walk(folder)
            for f in fs
            if "_CONV." not in f and os.path.splitext(f)[1].lower() in ALL
        ]
        total = len(files)
        self._log.write(f"  {total} fichiers  |  GPU: {'OUI ✓' if gpu else 'NON (CPU)'}")
        self._bar.config(maximum=max(total, 1), value=0)
        st = {"ok": 0, "sk": 0, "er": 0}
        lk = Lock()

        def run_ff(extra, path, tmp, dst):
            cmd = [ff, "-y", "-i", path] + extra + [tmp]
            try:
                subprocess.run(cmd, capture_output=True, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                    os.remove(path); os.rename(tmp, dst)
                    with lk: st["ok"] += 1
                    self._log.write(f"  ✓  {os.path.basename(dst)}")
                else:
                    if os.path.isfile(tmp): os.remove(tmp)
                    with lk: st["er"] += 1
                    self._log.write(f"  ✗  {os.path.basename(path)}")
            except Exception as e:
                if os.path.isfile(tmp): os.remove(tmp)
                with lk: st["er"] += 1
                self._log.write(f"  ✗  {os.path.basename(path)}  {e}")

        def proc(path):
            if not self._running: return
            base, ext = os.path.splitext(path); ext = ext.lower()
            if ext in V_SRC and fv and ext != "." + fv:
                args = (VA_GPU if gpu else VA_CPU).get(fv, [])
                hw   = ["-hwaccel", "cuda"] if gpu and fv not in ("webm", "avi") else []
                tmp  = base + "_CONV." + fv
                cmd  = [ff, "-y"] + hw + ["-i", path] + args + [tmp]
                try:
                    subprocess.run(cmd, capture_output=True, check=True,
                                   creationflags=subprocess.CREATE_NO_WINDOW)
                    if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                        os.remove(path); os.rename(tmp, base + "." + fv)
                        with lk: st["ok"] += 1
                        self._log.write(f"  ✓  {os.path.basename(base + '.' + fv)}")
                    else:
                        if os.path.isfile(tmp): os.remove(tmp)
                        with lk: st["er"] += 1
                        self._log.write(f"  ✗  {os.path.basename(path)}")
                except Exception as e:
                    if os.path.isfile(tmp): os.remove(tmp)
                    with lk: st["er"] += 1
                    self._log.write(f"  ✗  {os.path.basename(path)}  {e}")
            elif ext in I_SRC and fi and ext != "." + fi:
                run_ff(IA.get(fi, []), path, base + "_CONV." + fi, base + "." + fi)
            elif ext in A_SRC and fa and ext != "." + fa:
                run_ff(AA.get(fa, []), path, base + "_CONV." + fa, base + "." + fa)
            else:
                with lk: st["sk"] += 1

            with lk:
                done = st["ok"] + st["er"] + st["sk"]
                self._bar.config(value=done)
                self._lbl.config(text=f"{done}/{total}  ✓{st['ok']}  ✗{st['er']}  —{st['sk']}")

        with ThreadPoolExecutor(max(1, (os.cpu_count() or 4) // 2)) as ex:
            for _ in as_completed([ex.submit(proc, f) for f in files]): pass
        self._running = False
        self._log.write(f"\n  Terminé  ✓{st['ok']}  ✗{st['er']}  ignorés:{st['sk']}")
        self._lbl.config(text=f"Terminé — {st['ok']} convertis, {st['er']} erreurs")


# ═══════════════════════════════════════════════════════
#  ONGLET YOUTUBE
# ═══════════════════════════════════════════════════════
class TabYT(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app; self._running = False
        self._build(); self._load()

    def _build(self):
        p = scrollable(self)

        tk.Label(p, text="YouTube Playlist", bg=BG, fg=FG, font=FTTL,
                 anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(p, text="Télécharge toute une playlist dans le format choisi.",
                 bg=BG, fg=FG2, font=FSM, anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        c0 = mk_card(p, "yt-dlp")
        dr = tk.Frame(c0, bg=CARD); dr.pack(fill="x")
        self._dot_yt = Dot(dr, "yt-dlp"); self._dot_yt.pack(side="left", padx=(0, 12))
        Btn(dr, "Vérifier & installer", cmd=self._check_yt).pack(side="right")

        c1 = mk_card(p, "Playlist")
        r1 = mk_row(c1, "Lien URL")
        self._url = tk.StringVar()
        tk.Entry(r1, textvariable=self._url, bg=LINE, fg=FG,
                 insertbackground=FG, relief="flat", font=FMO, bd=0)\
          .pack(side="left", fill="x", expand=True, ipady=5)
        self._url.trace_add("write", lambda *a: self._save())

        r2 = mk_row(c1, "Format")
        self._fmt = tk.StringVar(value=list(YT_FMTS.keys())[0])
        mk_menu(r2, self._fmt, list(YT_FMTS.keys())).pack(side="left", fill="x", expand=True)

        self._out = PathRow(c1, label="Dossier sortie", mode="dir", on_change=self._save)
        self._out.pack(fill="x", pady=(4, 0))

        c2 = mk_card(p, "Progression")
        self._lbl = tk.Label(c2, text="En attente", bg=CARD, fg=FG2, font=FSM, anchor="w")
        self._lbl.pack(fill="x", pady=(0, 4))
        self._bar = ttk.Progressbar(c2, mode="determinate", style="A.Horizontal.TProgressbar")
        self._bar.pack(fill="x", pady=(0, 6))
        self._log = Log(c2, h=8)
        self._log.pack(fill="both", expand=True)

        br = tk.Frame(p, bg=BG)
        br.pack(fill="x", padx=16, pady=(10, 16))
        Btn(br, "▶  Télécharger la playlist", cmd=self._start, primary=True).pack(side="left")
        Btn(br, "Arrêter", cmd=self._stop, danger=True).pack(side="right")

    def _save(self):
        self.app.cfg["yt_out"] = self._out.get()
        cfg_save(self.app.cfg)

    def _load(self):
        self._out.set(self.app.cfg.get("yt_out", ""))

    def _stop(self): self._running = False

    def _check_yt(self):
        self._dot_yt.pending()
        def run():
            try:
                ensure_ytdlp(); self._dot_yt.ok("yt-dlp  ✓")
            except Exception as e:
                self._dot_yt.err(f"yt-dlp  ✗  {e}", "https://pypi.org/project/yt-dlp/")
        threading.Thread(target=run, daemon=True).start()

    def _start(self):
        if self._running: return
        ff = self.app.cfg.get("ffmpeg", "")
        if not ff or not os.path.isfile(ff):
            messagebox.showerror("FFmpeg manquant",
                                 "FFmpeg n'est pas configuré.\nVa dans l'onglet ⚙ Paramètres.")
            return
        url = self._url.get().strip()
        if not url:
            messagebox.showerror("Erreur", "Colle un lien de playlist YouTube.")
            return
        self._save(); self._running = True; self._log.clear()
        threading.Thread(target=self._run, args=(url, ff), daemon=True).start()

    def _run(self, url, ff):
        try:
            yt_dlp = ensure_ytdlp()
        except Exception as e:
            self._log.write(f"  Impossible d'installer yt-dlp : {e}")
            self._running = False; return

        ff_dir = str(pathlib.Path(ff).parent)
        codec, quality, fmt_str = YT_FMTS.get(self._fmt.get(), ("mp3", "192", "bestaudio/best"))

        class QL:
            def debug(s, m): pass
            def info(s, m): pass
            def warning(s, m):
                if "unavailable" in str(m).lower(): self._log.write(f"  skip: {m}")
            def error(s, m):
                if "unavailable" in str(m).lower(): self._log.write(f"  ✗ {m}")

        self._log.write("  Récupération infos playlist...")
        try:
            with yt_dlp.YoutubeDL({"quiet": True, "extract_flat": True,
                                    "no_warnings": True, "logger": QL()}) as y:
                info = y.extract_info(url, download=False)
            title   = clean(info.get("title", "Playlist"))
            entries = [e for e in info.get("entries", []) if e]
        except Exception as e:
            self._log.write(f"  Erreur accès playlist : {e}")
            self._running = False; return

        base_out = self._out.get() or str(pathlib.Path.home() / "Downloads")
        out_dir  = pathlib.Path(base_out) / title
        out_dir.mkdir(parents=True, exist_ok=True)
        total = len(entries)
        self._log.write(f"  {title}  —  {total} vidéos  →  {out_dir}")
        self._bar.config(maximum=max(total, 1), value=0)
        ct = {"n": 0}; lk = Lock()

        pp = ([{"key": "FFmpegExtractAudio",
                "preferredcodec": codec, "preferredquality": quality}]
              if codec in ("mp3","flac","aac","opus","m4a")
              else [{"key": "FFmpegVideoConvertor", "preferedformat": "mp4"}])

        def dl(entry):
            if not self._running: return
            link = entry.get("url") or entry.get("webpage_url")
            if not link:
                with lk: ct["n"] += 1; self._bar.config(value=ct["n"])
                return
            name = clean(entry.get("title", "video"))
            self._log.write(f"  ⬇  {name}")
            opts = {
                "format": fmt_str,
                "outtmpl": str(out_dir / f"{name}.%(ext)s"),
                "postprocessors": pp,
                "quiet": True, "no_warnings": True, "ignoreerrors": True,
                "logger": QL(), "ffmpeg_location": ff_dir,
                "writethumbnail": False, "writesubtitles": False,
            }
            try:
                with yt_dlp.YoutubeDL(opts) as y: y.download([link])
            except Exception as e:
                self._log.write(f"  ✗  {name}  {e}")
            with lk:
                ct["n"] += 1
                self._bar.config(value=ct["n"])
                self._lbl.config(text=f"{ct['n']}/{total}  —  {name[:38]}")

        with ThreadPoolExecutor(max(1, (os.cpu_count() or 4) // 2)) as ex:
            for _ in as_completed([ex.submit(dl, e) for e in entries]): pass
        self._running = False
        self._log.write(f"\n  Terminé  {ct['n']}/{total}  →  {out_dir}")
        self._lbl.config(text=f"Terminé — {ct['n']} fichiers")


# ═══════════════════════════════════════════════════════
#  ONGLET COULEURS
# ═══════════════════════════════════════════════════════
class TabColor(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app; self._running = False
        self._build(); self._load()

    def _build(self):
        p = scrollable(self)

        tk.Label(p, text="Color Changer", bg=BG, fg=FG, font=FTTL,
                 anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(p, text="Modifie saturation, luminosité, contraste et gamma de vos images.",
                 bg=BG, fg=FG2, font=FSM, anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        c1 = mk_card(p, "Source")
        mr = tk.Frame(c1, bg=CARD); mr.pack(fill="x", pady=(0, 6))
        self._mode = tk.StringVar(value="folder")
        tk.Radiobutton(mr, text="Fichier unique", variable=self._mode, value="file",
                       bg=CARD, fg=FG, selectcolor=LINE, activebackground=CARD,
                       command=self._toggle).pack(side="left", padx=(0, 12))
        tk.Radiobutton(mr, text="Dossier entier", variable=self._mode, value="folder",
                       bg=CARD, fg=FG, selectcolor=LINE, activebackground=CARD,
                       command=self._toggle).pack(side="left")
        self._file_row = PathRow(c1, label="Fichier", mode="file",
                                 ftypes=[("Images","*.jpg *.png *.webp *.bmp *.tiff *.gif *.avif"),
                                         ("Tous","*.*")],
                                 on_change=self._save)
        self._dir_row = PathRow(c1, label="Dossier", mode="dir", on_change=self._save)

        c2 = mk_card(p, "Format de sortie")
        r2 = mk_row(c2, "Format")
        self._fmt = tk.StringVar(value="same")
        mk_menu(r2, self._fmt, ["same","jpg","png","webp","avif","tiff","bmp","gif"])\
          .pack(side="left", fill="x", expand=True)
        tk.Label(r2, text="  'same' = conserve le format original",
                 bg=CARD, fg=FG2, font=FSM).pack(side="left")
        self._fmt.trace_add("write", lambda *a: self._save())

        c3 = mk_card(p, "Réglages  (0 = aucun changement)")
        self._sld = {}
        for key, lbl, mn, mx in [("sat","Saturation",-100,100),
                                   ("bri","Luminosité", -100,100),
                                   ("con","Contraste",  -100,100),
                                   ("gam","Gamma",       -50, 50)]:
            r = tk.Frame(c3, bg=CARD); r.pack(fill="x", pady=3)
            tk.Label(r, text=lbl, bg=CARD, fg=FG2, font=FSM, width=11, anchor="w").pack(side="left")
            v = tk.IntVar(value=0)
            vl = tk.Label(r, text=" 0", bg=CARD, fg=ACC2, font=FB, width=5, anchor="e")
            vl.pack(side="right")

            def on_slide(val, vl=vl):
                n = int(float(val))
                vl.config(text=("+" if n > 0 else "") + str(n))
                self._save()

            tk.Scale(r, variable=v, from_=mn, to=mx, orient="h",
                     bg=CARD, fg=FG2, troughcolor=LINE, activebackground=ACC,
                     highlightthickness=0, bd=0, sliderrelief="flat",
                     command=on_slide).pack(side="left", fill="x", expand=True)
            self._sld[key] = v
        Btn(c3, "Réinitialiser", cmd=self._reset).pack(anchor="w", pady=(6, 0))

        c4 = mk_card(p, "Progression")
        self._lbl = tk.Label(c4, text="En attente", bg=CARD, fg=FG2, font=FSM, anchor="w")
        self._lbl.pack(fill="x", pady=(0, 4))
        self._bar = ttk.Progressbar(c4, mode="determinate", style="A.Horizontal.TProgressbar")
        self._bar.pack(fill="x", pady=(0, 6))
        self._log = Log(c4, h=6)
        self._log.pack(fill="both", expand=True)

        br = tk.Frame(p, bg=BG)
        br.pack(fill="x", padx=16, pady=(10, 16))
        Btn(br, "▶  Lancer", cmd=self._start, primary=True).pack(side="left")
        Btn(br, "Arrêter",   cmd=self._stop,  danger=True).pack(side="right")

        self._toggle()

    def _toggle(self):
        m = self._mode.get()
        self._file_row.pack_forget(); self._dir_row.pack_forget()
        (self._file_row if m == "file" else self._dir_row).pack(fill="x")

    def _reset(self):
        for v in self._sld.values(): v.set(0)
        self._save()

    def _save(self):
        self.app.cfg["col_mode"] = self._mode.get()
        self.app.cfg["col_fmt"]  = self._fmt.get()
        for k, v in self._sld.items(): self.app.cfg["col_" + k] = v.get()
        cfg_save(self.app.cfg)

    def _load(self):
        m = self.app.cfg.get("col_mode", "folder")
        self._mode.set(m); self._toggle()
        pth = self.app.cfg.get("col_path", "")
        (self._file_row if m == "file" else self._dir_row).set(pth)
        self._fmt.set(self.app.cfg.get("col_fmt", "same"))
        for k, v in self._sld.items(): v.set(int(self.app.cfg.get("col_" + k, 0)))

    def _stop(self): self._running = False

    def _start(self):
        if self._running: return
        ff = self.app.cfg.get("ffmpeg", "")
        if not ff or not os.path.isfile(ff):
            messagebox.showerror("FFmpeg manquant",
                                 "FFmpeg n'est pas configuré.\nVa dans l'onglet ⚙ Paramètres.")
            return
        mode = self._mode.get()
        if mode == "file":
            src = self._file_row.get()
            if not os.path.isfile(src):
                messagebox.showerror("Erreur", "Fichier introuvable.")
                return
            files = [src]
        else:
            folder = self._dir_row.get()
            if not os.path.isdir(folder):
                messagebox.showerror("Erreur", "Dossier introuvable.")
                return
            files = [os.path.join(r, f) for r, _, fs in os.walk(folder)
                     for f in fs if "_TMP" not in f
                     and os.path.splitext(f)[1].lower() in I_SRC]
        if not files:
            messagebox.showinfo("Info", "Aucune image trouvée.")
            return
        self.app.cfg["col_path"] = (self._file_row if mode == "file" else self._dir_row).get()
        self._save(); self._running = True; self._log.clear()
        sat = self._sld["sat"].get(); bri = self._sld["bri"].get()
        con = self._sld["con"].get(); gam = self._sld["gam"].get()
        out = self._fmt.get(); out = None if out == "same" else out
        threading.Thread(target=self._run,
                         args=(files, ff, sat, bri, con, gam, out), daemon=True).start()

    def _run(self, files, ff, sat, bri, con, gam, out_fmt):
        sf = round(1 + sat / 100, 4); bf = round(bri / 100, 4)
        cf = round(1 + con / 100, 4)
        gf = max(0.1, min(10.0, round(1 + gam / 50, 4))) if gam else 1.0
        eq = f"eq=brightness={bf}:saturation={sf}:contrast={cf}:gamma={gf}"
        total = len(files)
        self._bar.config(maximum=max(total, 1), value=0)
        st = {"ok": 0, "er": 0}; lk = Lock()

        def proc(path):
            if not self._running: return
            base, ext = os.path.splitext(path); ext = ext.lower()
            te  = ("." + out_fmt) if out_fmt else ext
            tmp = base + "_TMP" + te; dst = base + te
            qa  = IMG_Q.get((out_fmt or ext.lstrip(".")).lower(), [])
            try:
                subprocess.run([ff, "-y", "-i", path, "-vf", eq] + qa + [tmp],
                               capture_output=True, check=True,
                               creationflags=subprocess.CREATE_NO_WINDOW)
                if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                    if dst != path: os.remove(path)
                    os.replace(tmp, dst)
                    with lk: st["ok"] += 1
                    self._log.write(f"  ✓  {os.path.basename(dst)}")
                else:
                    if os.path.isfile(tmp): os.remove(tmp)
                    with lk: st["er"] += 1
                    self._log.write(f"  ✗  {os.path.basename(path)}")
            except Exception as e:
                if os.path.isfile(tmp): os.remove(tmp)
                with lk: st["er"] += 1
                self._log.write(f"  ✗  {os.path.basename(path)}  {e}")
            with lk:
                done = st["ok"] + st["er"]
                self._bar.config(value=done)
                self._lbl.config(text=f"{done}/{total}  ✓{st['ok']}  ✗{st['er']}")

        with ThreadPoolExecutor(max(1, (os.cpu_count() or 4) // 2)) as ex:
            for _ in as_completed([ex.submit(proc, f) for f in files]): pass
        self._running = False
        self._log.write(f"\n  Terminé  ✓{st['ok']}  ✗{st['er']}")
        self._lbl.config(text=f"Terminé — {st['ok']} modifiées, {st['er']} erreurs")


# ═══════════════════════════════════════════════════════
#  ONGLET REDIMENSIONNER
# ═══════════════════════════════════════════════════════
class TabResize(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._running = False
        self._build()
        self._load()

    def _build(self):
        p = scrollable(self)

        tk.Label(p, text="Redimensionner", bg=BG, fg=FG, font=FTTL,
                 anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(p, text="Réduit les images en définissant une hauteur maximale. La largeur s'adapte automatiquement.",
                 bg=BG, fg=FG2, font=FSM, anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        # ── Source ──────────────────────────────────────
        c1 = mk_card(p, "Source")
        mr = tk.Frame(c1, bg=CARD); mr.pack(fill="x", pady=(0, 6))
        self._mode = tk.StringVar(value="folder")
        tk.Radiobutton(mr, text="Fichier unique", variable=self._mode, value="file",
                       bg=CARD, fg=FG, selectcolor=LINE, activebackground=CARD,
                       command=self._toggle).pack(side="left", padx=(0, 12))
        tk.Radiobutton(mr, text="Dossier entier", variable=self._mode, value="folder",
                       bg=CARD, fg=FG, selectcolor=LINE, activebackground=CARD,
                       command=self._toggle).pack(side="left")

        self._file_row = PathRow(c1, label="Fichier", mode="file",
                                 ftypes=[("Images","*.jpg *.jpeg *.png *.webp *.bmp *.tiff *.tif *.gif *.avif *.heic"),
                                         ("Tous","*.*")],
                                 on_change=self._save)
        self._dir_row = PathRow(c1, label="Dossier", mode="dir", on_change=self._save)

        # ── Réglages ─────────────────────────────────────
        c2 = mk_card(p, "Réglages")

        # Hauteur max
        rh = mk_row(c2, "Hauteur max")
        self._height_var = tk.IntVar(value=1080)
        self._height_lbl = tk.Label(rh, text="1080 px", bg=CARD, fg=ACC2, font=FB, width=9, anchor="e")
        self._height_lbl.pack(side="right")

        heights = [240, 360, 480, 720, 1080, 1440, 2160, 4320]

        def on_h_slide(val):
            n = int(float(val))
            self._height_lbl.config(text=f"{n} px")
            self._save()

        self._h_scale = tk.Scale(rh, variable=self._height_var, from_=100, to=8000,
                                 orient="h", resolution=10,
                                 bg=CARD, fg=FG2, troughcolor=LINE, activebackground=ACC,
                                 highlightthickness=0, bd=0, sliderrelief="flat",
                                 command=on_h_slide)
        self._h_scale.pack(side="left", fill="x", expand=True)

        # Raccourcis tailles standards
        rq = tk.Frame(c2, bg=CARD); rq.pack(fill="x", pady=(4, 2))
        tk.Label(rq, text="Raccourcis :", bg=CARD, fg=FG2, font=FSM).pack(side="left")
        for h in heights:
            lbl = f"{h}p" if h <= 2160 else f"{h//1080}K"
            def _set(val=h):
                self._height_var.set(val)
                self._height_lbl.config(text=f"{val} px")
                self._save()
            Btn(rq, text=lbl, cmd=_set).pack(side="left", padx=2)

        # Saisie manuelle
        rm = mk_row(c2, "Valeur libre")
        self._custom_var = tk.StringVar()
        e = tk.Entry(rm, textvariable=self._custom_var, bg=LINE, fg=FG,
                     insertbackground=FG, relief="flat", font=FMO, bd=0, width=8)
        e.pack(side="left", ipady=5, padx=(0, 6))
        tk.Label(rm, text="px", bg=CARD, fg=FG2, font=FSM).pack(side="left")
        def _apply_custom():
            try:
                v = int(self._custom_var.get())
                if 10 <= v <= 99999:
                    self._height_var.set(v)
                    self._height_lbl.config(text=f"{v} px")
                    self._save()
            except ValueError:
                pass
        Btn(rm, "Appliquer", cmd=_apply_custom).pack(side="left", padx=(6, 0))

        # Format de sortie
        rf = mk_row(c2, "Format sortie")
        self._fmt = tk.StringVar(value="same")
        mk_menu(rf, self._fmt, ["same","jpg","png","webp","avif","tiff","bmp"])\
          .pack(side="left", fill="x", expand=True)
        tk.Label(rf, text="  'same' = conserve le format original",
                 bg=CARD, fg=FG2, font=FSM).pack(side="left")
        self._fmt.trace_add("write", lambda *a: self._save())

        # Suffixe
        rs = mk_row(c2, "Suffixe")
        self._suffix_var = tk.StringVar(value="_resized")
        es = tk.Entry(rs, textvariable=self._suffix_var, bg=LINE, fg=FG,
                      insertbackground=FG, relief="flat", font=FMO, bd=0, width=16)
        es.pack(side="left", ipady=5)
        tk.Label(rs, text="  (vide = remplace l'original)", bg=CARD, fg=FG2, font=FSM).pack(side="left")
        self._suffix_var.trace_add("write", lambda *a: self._save())

        # ── Progression ───────────────────────────────────
        c3 = mk_card(p, "Progression")
        self._lbl = tk.Label(c3, text="En attente", bg=CARD, fg=FG2, font=FSM, anchor="w")
        self._lbl.pack(fill="x", pady=(0, 4))
        self._bar = ttk.Progressbar(c3, mode="determinate", style="A.Horizontal.TProgressbar")
        self._bar.pack(fill="x", pady=(0, 6))
        self._log = Log(c3, h=7)
        self._log.pack(fill="both", expand=True)

        br = tk.Frame(p, bg=BG)
        br.pack(fill="x", padx=16, pady=(10, 16))
        Btn(br, "▶  Lancer le redimensionnement", cmd=self._start, primary=True).pack(side="left")
        Btn(br, "Arrêter", cmd=self._stop, danger=True).pack(side="right")

        self._toggle()

    def _toggle(self):
        m = self._mode.get()
        self._file_row.pack_forget()
        self._dir_row.pack_forget()
        (self._file_row if m == "file" else self._dir_row).pack(fill="x")

    def _save(self):
        self.app.cfg["rsz_mode"]   = self._mode.get()
        self.app.cfg["rsz_height"] = self._height_var.get()
        self.app.cfg["rsz_fmt"]    = self._fmt.get()
        self.app.cfg["rsz_suffix"] = self._suffix_var.get()
        m = self._mode.get()
        self.app.cfg["rsz_path"] = (self._file_row if m == "file" else self._dir_row).get()
        cfg_save(self.app.cfg)

    def _load(self):
        m = self.app.cfg.get("rsz_mode", "folder")
        self._mode.set(m); self._toggle()
        pth = self.app.cfg.get("rsz_path", "")
        (self._file_row if m == "file" else self._dir_row).set(pth)
        h = int(self.app.cfg.get("rsz_height", 1080))
        self._height_var.set(h)
        self._height_lbl.config(text=f"{h} px")
        self._fmt.set(self.app.cfg.get("rsz_fmt", "same"))
        self._suffix_var.set(self.app.cfg.get("rsz_suffix", "_resized"))

    def _stop(self):
        self._running = False

    def _start(self):
        if self._running:
            return
        ff = self.app.cfg.get("ffmpeg", "")
        if not ff or not os.path.isfile(ff):
            messagebox.showerror("FFmpeg manquant",
                                 "FFmpeg n'est pas configuré.\nVa dans l'onglet ⚙ Paramètres.")
            return
        mode = self._mode.get()
        if mode == "file":
            src = self._file_row.get()
            if not os.path.isfile(src):
                messagebox.showerror("Erreur", "Fichier introuvable.")
                return
            files = [src]
        else:
            folder = self._dir_row.get()
            if not os.path.isdir(folder):
                messagebox.showerror("Erreur", "Dossier introuvable.")
                return
            suffix = self._suffix_var.get()
            files = [
                os.path.join(r, f)
                for r, _, fs in os.walk(folder)
                for f in fs
                if os.path.splitext(f)[1].lower() in I_SRC
                and (not suffix or suffix not in f)
            ]
        if not files:
            messagebox.showinfo("Info", "Aucune image trouvée.")
            return
        self._save()
        self._running = True
        self._log.clear()
        height  = self._height_var.get()
        out_fmt = None if self._fmt.get() == "same" else self._fmt.get()
        suffix  = self._suffix_var.get()
        threading.Thread(target=self._run,
                         args=(files, ff, height, out_fmt, suffix), daemon=True).start()

    def _run(self, files, ff, max_height, out_fmt, suffix):
        total = len(files)
        self._log.write(f"  {total} image(s)  |  Hauteur max : {max_height} px")
        self._bar.config(maximum=max(total, 1), value=0)
        st = {"ok": 0, "er": 0}
        lk = Lock()

        def proc(path):
            if not self._running:
                return
            base, ext = os.path.splitext(path)
            ext = ext.lower()
            te  = ("." + out_fmt) if out_fmt else ext
            dst = base + suffix + te if suffix else base + te
            tmp = base + "_RSZTMP" + te
            qa  = IMG_Q.get((out_fmt or ext.lstrip(".")).lower(), [])
            # scale= garde le ratio, ne redimensionne que si > max_height
            vf  = f"scale='if(gt(ih,{max_height}),trunc(iw*{max_height}/ih/2)*2,iw)':'if(gt(ih,{max_height}),{max_height},ih)'"
            try:
                subprocess.run(
                    [ff, "-y", "-i", path, "-vf", vf] + qa + [tmp],
                    capture_output=True, check=True,
                    creationflags=subprocess.CREATE_NO_WINDOW
                )
                if os.path.isfile(tmp) and os.path.getsize(tmp) > 0:
                    if dst != path and os.path.isfile(path) and not suffix:
                        os.remove(path)
                    os.replace(tmp, dst)
                    with lk:
                        st["ok"] += 1
                    self._log.write(f"  ✓  {os.path.basename(dst)}")
                else:
                    if os.path.isfile(tmp):
                        os.remove(tmp)
                    with lk:
                        st["er"] += 1
                    self._log.write(f"  ✗  {os.path.basename(path)}")
            except Exception as e:
                if os.path.isfile(tmp):
                    os.remove(tmp)
                with lk:
                    st["er"] += 1
                self._log.write(f"  ✗  {os.path.basename(path)}  {e}")
            with lk:
                done = st["ok"] + st["er"]
                self._bar.config(value=done)
                self._lbl.config(text=f"{done}/{total}  ✓{st['ok']}  ✗{st['er']}")

        with ThreadPoolExecutor(max(1, (os.cpu_count() or 4) // 2)) as ex:
            for _ in as_completed([ex.submit(proc, f) for f in files]):
                pass
        self._running = False
        self._log.write(f"\n  Terminé  ✓{st['ok']}  ✗{st['er']}")
        self._lbl.config(text=f"Terminé — {st['ok']} redimensionnées, {st['er']} erreurs")


# ═══════════════════════════════════════════════════════
#  ONGLET PARAMÈTRES  (FFmpeg global)
# ═══════════════════════════════════════════════════════
class TabSettings(tk.Frame):
    def __init__(self, parent, app):
        super().__init__(parent, bg=BG)
        self.app = app
        self._build(); self._load()

    def _build(self):
        p = self  # pas de scroll nécessaire ici

        tk.Label(p, text="Paramètres", bg=BG, fg=FG, font=FTTL,
                 anchor="w").pack(fill="x", padx=16, pady=(14, 2))
        tk.Label(p, text="Ces réglages sont partagés entre tous les outils.",
                 bg=BG, fg=FG2, font=FSM, anchor="w").pack(fill="x", padx=16, pady=(0, 8))

        # FFmpeg
        c1 = mk_card(p, "FFmpeg  —  utilisé par tous les outils")
        self._ff = PathRow(c1, label="ffmpeg.exe", mode="file",
                           ftypes=[("FFmpeg", "ffmpeg.exe ffmpeg"), ("Tous", "*.*")],
                           on_change=self._on_ff)
        self._ff.pack(fill="x")

        dr = tk.Frame(c1, bg=CARD); dr.pack(fill="x", pady=(8, 0))
        self._dot_ff  = Dot(dr, "FFmpeg");   self._dot_ff.pack(side="left", padx=(0, 16))
        self._dot_gpu = Dot(dr, "GPU CUDA"); self._dot_gpu.pack(side="left")
        Btn(dr, "Vérifier", cmd=self._check_ff).pack(side="right")

        note = ("Si ffmpeg.exe n'est pas dans votre PATH, utilisez 📂 pour le localiser.\n"
                "Téléchargement gratuit sur ffmpeg.org")
        tk.Label(c1, text=note, bg=CARD, fg=FG2, font=FSM,
                 justify="left", anchor="w").pack(fill="x", pady=(8, 0))
        Btn(c1, "Télécharger FFmpeg",
            cmd=lambda: __import__("webbrowser").open("https://ffmpeg.org/download.html"))\
          .pack(anchor="w", pady=(6, 0))

        mk_sep(p)

        # yt-dlp
        c2 = mk_card(p, "yt-dlp  —  pour YouTube")
        dr2 = tk.Frame(c2, bg=CARD); dr2.pack(fill="x")
        self._dot_yt = Dot(dr2, "yt-dlp"); self._dot_yt.pack(side="left", padx=(0, 12))
        Btn(dr2, "Installer / Mettre à jour", cmd=self._install_yt).pack(side="right")
        tk.Label(c2, text="S'installe automatiquement via pip. Si ça échoue, clique le bouton.",
                 bg=CARD, fg=FG2, font=FSM, anchor="w").pack(fill="x", pady=(6, 0))

        mk_sep(p)

        # Config path
        c3 = mk_card(p, "Fichier de configuration (sauvegarde automatique)")
        tk.Label(c3, text=CFG_FILE, bg=CARD, fg=FG2, font=FMO,
                 anchor="w", wraplength=560).pack(fill="x")
        Btn(c3, "Ouvrir le dossier",
            cmd=lambda: subprocess.Popen(f'explorer "{os.path.dirname(CFG_FILE)}"'))\
          .pack(anchor="w", pady=(6, 0))

    def _on_ff(self):
        self.app.cfg["ffmpeg"] = self._ff.get()
        cfg_save(self.app.cfg)

    def _load(self):
        ff = self.app.cfg.get("ffmpeg", "")
        if ff: self._ff.set(ff)

    def _check_ff(self):
        self._dot_ff.pending(); self._dot_gpu.pending()
        def run():
            ff = find_ffmpeg(self._ff.get())
            if ff:
                self._ff.set(ff)
                self.app.cfg["ffmpeg"] = ff
                cfg_save(self.app.cfg)
                self._dot_ff.ok(f"FFmpeg  ✓  {os.path.basename(ff)}")
                (self._dot_gpu.ok("GPU CUDA  ✓  NVIDIA détecté")
                 if has_gpu(ff)
                 else self._dot_gpu.warn("GPU CUDA  —  non détecté, mode CPU"))
            else:
                self._dot_ff.err("FFmpeg introuvable", "https://ffmpeg.org/download.html")
                self._dot_gpu.err("GPU CUDA  —  ffmpeg manquant")
        threading.Thread(target=run, daemon=True).start()

    def _install_yt(self):
        self._dot_yt.pending()
        def run():
            try:
                ensure_ytdlp(); self._dot_yt.ok("yt-dlp  ✓  prêt")
            except Exception as e:
                self._dot_yt.err(f"yt-dlp  ✗  {e}", "https://pypi.org/project/yt-dlp/")
        threading.Thread(target=run, daemon=True).start()


# ═══════════════════════════════════════════════════════
#  FENÊTRE PRINCIPALE
# ═══════════════════════════════════════════════════════
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Media Toolkit")
        self.configure(bg=BG)
        self.option_add("*tearOff", False)

        # Centrage et taille adaptée à l'écran
        self.update_idletasks()
        sw = self.winfo_screenwidth()
        sh = self.winfo_screenheight()
        w  = min(840, int(sw * 0.75))
        h  = min(720, int(sh * 0.80))
        x  = (sw - w) // 2
        y  = (sh - h) // 2
        self.geometry(f"{w}x{h}+{x}+{y}")
        self.minsize(600, 520)

        # Style progressbar
        s = ttk.Style(self)
        s.theme_use("clam")
        s.configure("A.Horizontal.TProgressbar",
                    background=ACC, troughcolor=LINE,
                    borderwidth=0, relief="flat", thickness=5)

        self.cfg = cfg_load()
        self._build()

        # Auto-détection FFmpeg silencieuse au démarrage
        def auto_ff():
            ff = find_ffmpeg(self.cfg.get("ffmpeg", ""))
            if ff and not self.cfg.get("ffmpeg"):
                self.cfg["ffmpeg"] = ff
                cfg_save(self.cfg)
        threading.Thread(target=auto_ff, daemon=True).start()

    def _build(self):
        # Barre titre
        top = tk.Frame(self, bg="#111113", height=46)
        top.pack(fill="x"); top.pack_propagate(False)
        tk.Label(top, text="  ◈  Media Toolkit", bg="#111113", fg=FG,
                 font=("Segoe UI", 12, "bold")).pack(side="left")
        tk.Label(top, text="format  ·  youtube  ·  couleurs  ·  resize",
                 bg="#111113", fg=FG2, font=FSM).pack(side="left", padx=10)

        # Barre onglets
        tbar = tk.Frame(self, bg="#111113")
        tbar.pack(fill="x")
        tk.Frame(self, bg=LINE, height=1).pack(fill="x")  # séparateur

        # Zone contenu
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        self._tabs     = {}
        self._tab_btns = {}
        self._active   = None

        for key, lbl, cls in [
            ("format",   "Format Changer", TabFormat),
            ("youtube",  "YouTube",        TabYT),
            ("color",    "Couleurs",       TabColor),
            ("resize",   "Redimensionner", TabResize),
            ("settings", "⚙ Paramètres",  TabSettings),
        ]:
            frame = cls(body, self)
            self._tabs[key] = frame

            btn = tk.Label(tbar, text=lbl, bg="#111113", fg=FG2,
                           font=FSM, padx=16, pady=9, cursor="hand2")
            btn.pack(side="left")
            btn.bind("<Button-1>", lambda e, k=key: self._switch(k))
            btn.bind("<Enter>",    lambda e, b=btn, k=key:
                     b.config(fg=FG) if self._active != k else None)
            btn.bind("<Leave>",    lambda e, b=btn, k=key:
                     b.config(fg=ACC2 if self._active == k else FG2))
            self._tab_btns[key] = btn

        self._switch("format")

    def _switch(self, key):
        if self._active and self._active in self._tabs:
            self._tabs[self._active].pack_forget()
        self._active = key
        self._tabs[key].pack(fill="both", expand=True)
        for k, b in self._tab_btns.items():
            b.config(fg=ACC2 if k == key else FG2,
                     font=FB if k == key else FSM)


if __name__ == "__main__":
    App().mainloop()