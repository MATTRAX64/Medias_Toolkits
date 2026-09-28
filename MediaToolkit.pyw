"""
MediaToolkit  —  v3  (interface pywebview)
──────────────────────────────────────────────────────────────────────
Convertir · Couleurs (aperçu) · Redimensionner · Outils vidéo/audio

Installation (une seule fois) :
    pip install pywebview pillow pillow-heif

FFmpeg est nécessaire (auto-détecté, sinon à indiquer dans Paramètres).
"""

import os, sys, re, json, shutil, base64, threading, subprocess, tempfile, time, io
from concurrent.futures import ThreadPoolExecutor
from threading import Lock, Event

# ── dépendances : installées automatiquement si absentes ───────────────
def _pip(*pkgs):
    kw = {}
    if os.name == "nt":
        kw["creationflags"] = subprocess.CREATE_NO_WINDOW
    subprocess.check_call([sys.executable, "-m", "pip", "install", "-q", *pkgs], **kw)

try:
    import webview
except ImportError:
    _pip("pywebview"); import webview

try:
    from PIL import Image
    HAS_PIL = True
except ImportError:
    try:
        _pip("pillow"); from PIL import Image; HAS_PIL = True
    except Exception:
        HAS_PIL = False

if HAS_PIL:
    try:
        import pillow_heif
        pillow_heif.register_heif_opener()
    except ImportError:
        try:
            _pip("pillow-heif"); import pillow_heif
            pillow_heif.register_heif_opener()
        except Exception:
            pass

NOWIN = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
HERE  = os.path.dirname(os.path.abspath(__file__))
CFG_FILE = os.path.join(HERE, "mediatoolkit.json")

# ═══════════════════════════════════════════════════════════════════════
#  CONFIG
# ═══════════════════════════════════════════════════════════════════════
DEFAULTS = {
    "ffmpeg": "", "ffprobe": "",
    "threads": max(1, (os.cpu_count() or 4) // 2),
    "use_gpu": True,
    "overwrite_mode": "replace",      # replace | suffix | subfolder
    "suffix": "_conv",
    "subfolder": "converted",
    "recursive": True,
    "keep_metadata": True,
    "delete_source": True,
    "last_folder": "",
}

def cfg_load():
    c = dict(DEFAULTS)
    if os.path.isfile(CFG_FILE):
        try:
            c.update(json.loads(open(CFG_FILE, encoding="utf-8").read()))
        except Exception:
            pass
    return c

def cfg_save(c):
    try:
        open(CFG_FILE, "w", encoding="utf-8").write(json.dumps(c, indent=2, ensure_ascii=False))
    except Exception:
        pass

# ═══════════════════════════════════════════════════════════════════════
#  CATALOGUE DE FORMATS
#  Chaque cible = extension → (catégorie, arguments ffmpeg par défaut, description)
# ═══════════════════════════════════════════════════════════════════════
# Extensions SOURCE reconnues (ffmpeg les lit presque toutes)
V_SRC = {".m3u8",".m4s",".mp4",".mkv",".mov",".avi",".webm",".flv",".ts",".m2ts",".mts",".wmv",".3gp",".3g2",
         ".ogv",".m4v",".mpg",".mpeg",".vob",".asf",".rm",".rmvb",".f4v",".divx",".xvid",
         ".mxf",".dv",".nut",".y4m",".swf",".mod",".tod",".amv",".gxf",".ivf",".yuv",".h264",
         ".h265",".hevc",".264",".265",".m1v",".m2v",".mp2v",".dat",".bik",".smk",".roq",".fli",".flc"}
I_SRC = {".jpg",".jpeg",".png",".webp",".avif",".tiff",".tif",".bmp",".gif",".heic",".heif",".jfif",
         ".jp2",".j2k",".jxl",".ico",".tga",".ppm",".pgm",".pbm",".pnm",".pam",".psd",".exr",
         ".hdr",".dds",".sgi",".xbm",".xpm",".pcx",".qoi",".apng",".jxr",".wdp",".svg",".cur",
         ".dpx",".fits",".pic",".pict",".ras",".wbmp",".jpe",".xwd"}
A_SRC = {".mp3",".aac",".ogg",".flac",".opus",".wav",".m4a",".wma",".aiff",".aif",".aifc",".ac3",
         ".eac3",".dts",".mka",".amr",".awb",".au",".snd",".caf",".w64",".wv",".tta",".ape",
         ".mpc",".spx",".oga",".mp2",".mp1",".voc",".ra",".mid",".midi",".gsm",".8svx",".tak",
         ".dsf",".dff",".sbc",".ircam",".sf",".mlp",".thd",".adx",".xa",".mmf",".m4b",".m4r",".wve"}
ALL_SRC = V_SRC | I_SRC | A_SRC

def ext_kind(ext):
    ext = ext.lower()
    if ext in V_SRC: return "video"
    if ext in I_SRC: return "image"
    if ext in A_SRC: return "audio"
    return None

# format cible → dict(cat, ff = args de base (sans encodeur vidéo), muxer, label, needs = [encodeurs])
# 'venc' sera choisi dynamiquement selon la disponibilité / GPU / choix qualité.
VIDEO_TARGETS = {
    "mp4":  dict(label="MP4 (H.264/H.265)",       vcodecs=["h264","h265","av1"],       acodecs=["aac","mp3","opus","flac","ac3"]),
    "mkv":  dict(label="MKV (Matroska)",          vcodecs=["h264","h265","vp9","av1","ffv1"], acodecs=["aac","opus","flac","vorbis","ac3","mp3","copy"]),
    "mov":  dict(label="MOV (QuickTime)",         vcodecs=["h264","h265","prores"],    acodecs=["aac","pcm","alac"]),
    "webm": dict(label="WebM (VP9/AV1)",          vcodecs=["vp9","vp8","av1"],         acodecs=["opus","vorbis"]),
    "avi":  dict(label="AVI",                     vcodecs=["h264","mpeg4","mjpeg","huffyuv"], acodecs=["mp3","ac3","pcm"]),
    "flv":  dict(label="FLV (Flash)",             vcodecs=["h264","flv1"],             acodecs=["aac","mp3"]),
    "wmv":  dict(label="WMV (Windows Media)",     vcodecs=["wmv2"],                    acodecs=["wma"]),
    "ogv":  dict(label="OGV (Theora)",            vcodecs=["theora"],                  acodecs=["vorbis","opus","flac"]),
    "3gp":  dict(label="3GP (mobile)",            vcodecs=["h264","mpeg4"],            acodecs=["aac","amr"]),
    "3g2":  dict(label="3G2 (mobile)",            vcodecs=["h264","mpeg4"],            acodecs=["aac","amr"]),
    "ts":   dict(label="MPEG-TS",                 vcodecs=["h264","h265","mpeg2"],     acodecs=["aac","mp3","ac3"]),
    "mpg":  dict(label="MPEG-2 (DVD)",            vcodecs=["mpeg2","mpeg1"],           acodecs=["mp2","ac3","mp3"]),
    "m4v":  dict(label="M4V (Apple)",             vcodecs=["h264","h265"],             acodecs=["aac","ac3"]),
    "mxf":  dict(label="MXF (broadcast)",         vcodecs=["mpeg2","dnxhd","prores"],  acodecs=["pcm"]),
    "gif":  dict(label="GIF animé",               vcodecs=["gif"],                     acodecs=[]),
    "apng": dict(label="APNG animé",              vcodecs=["apng"],                    acodecs=[]),
    "webp": dict(label="WebP animé",              vcodecs=["webp"],                    acodecs=[], anim=True),
    "nut":  dict(label="NUT",                     vcodecs=["h264","ffv1","mpeg4"],     acodecs=["flac","pcm","aac"]),
    "swf":  dict(label="SWF (Flash)",             vcodecs=["flv1"],                    acodecs=["mp3"]),
    "asf":  dict(label="ASF",                     vcodecs=["wmv2"],                    acodecs=["wma"]),
    "f4v":  dict(label="F4V (Flash HD)",          vcodecs=["h264"],                    acodecs=["aac"]),
    "dv":   dict(label="DV (caméscope)",          vcodecs=["dv"],                      acodecs=["pcm"]),
    "y4m":  dict(label="Y4M (brut)",              vcodecs=["raw"],                     acodecs=[]),
}
AUDIO_TARGETS = {
    "mp3":  dict(label="MP3"),           "flac": dict(label="FLAC (sans perte)"),
    "wav":  dict(label="WAV (PCM)"),     "aac":  dict(label="AAC (ADTS)"),
    "m4a":  dict(label="M4A (AAC)"),     "ogg":  dict(label="OGG Vorbis"),
    "opus": dict(label="Opus"),          "aiff": dict(label="AIFF"),
    "wma":  dict(label="WMA"),           "ac3":  dict(label="AC-3 (Dolby)"),
    "eac3": dict(label="E-AC-3"),        "dts":  dict(label="DTS"),
    "mka":  dict(label="MKA (Matroska)"),"amr":  dict(label="AMR (voix)"),
    "au":   dict(label="AU (Sun)"),      "caf":  dict(label="CAF (Apple)"),
    "w64":  dict(label="Wave64"),        "wv":   dict(label="WavPack"),
    "tta":  dict(label="TTA"),           "mp2":  dict(label="MP2"),
    "spx":  dict(label="Speex"),         "oga":  dict(label="OGA (Ogg Audio)"),
    "m4b":  dict(label="M4B (livre audio)"), "m4r": dict(label="M4R (sonnerie iPhone)"),
    "voc":  dict(label="VOC (Creative)"),"ircam":dict(label="IRCAM"),
    "gsm":  dict(label="GSM"),           "mp1":  dict(label="MP1"),
}
IMAGE_TARGETS = {
    "jpg":  dict(label="JPEG"),          "png":  dict(label="PNG"),
    "webp": dict(label="WebP"),          "avif": dict(label="AVIF"),
    "tiff": dict(label="TIFF"),          "bmp":  dict(label="BMP"),
    "gif":  dict(label="GIF"),           "jxl":  dict(label="JPEG XL"),
    "jp2":  dict(label="JPEG 2000"),     "ico":  dict(label="ICO (icône)"),
    "tga":  dict(label="TGA (Targa)"),   "ppm":  dict(label="PPM"),
    "pgm":  dict(label="PGM (gris)"),    "pbm":  dict(label="PBM (N&B)"),
    "pam":  dict(label="PAM"),           "qoi":  dict(label="QOI"),
    "sgi":  dict(label="SGI"),           "xbm":  dict(label="XBM"),
    "dpx":  dict(label="DPX (cinéma)"),  "exr":  dict(label="OpenEXR"),
    "hdr":  dict(label="Radiance HDR"),  "psd":  dict(label="PSD (Photoshop)"),
    "pcx":  dict(label="PCX"),           "apng": dict(label="APNG"),
    "xwd":  dict(label="XWD"),           "wbmp": dict(label="WBMP"),
    "heic": dict(label="HEIC (via Pillow)"),
}

# Encodeurs vidéo : nom logique → liste de (encodeur ffmpeg, args qualité par niveau)
# Le niveau qualité 0..100 est converti en CRF/CQ selon l'encodeur.
def _crf(q, lo, hi):
    """q=100 → meilleure qualité (crf lo) ; q=0 → pire (crf hi)."""
    return str(int(round(hi - (hi - lo) * (q / 100.0))))

def video_codec_args(codec, q, speed, gpu_ok, encoders):
    """Retourne (liste d'args, encodeur utilisé) ou (None, None) si indisponible."""
    lossless = (q >= 100)
    presets_x = ["ultrafast","superfast","veryfast","faster","fast","medium","slow","slower","veryslow"]
    px = presets_x[max(0, min(8, int(speed)))]
    def has(n): return n in encoders

    if codec == "h264":
        if gpu_ok and has("h264_nvenc"):
            return (["-c:v","h264_nvenc","-preset","p5","-rc","vbr","-cq",_crf(q,0,40),"-b:v","0"], "h264_nvenc")
        if has("libx264"):
            return (["-c:v","libx264","-preset",px,"-crf","0" if lossless else _crf(q,10,38),"-pix_fmt","yuv444p" if lossless else "yuv420p"], "libx264")
    if codec == "h265":
        if gpu_ok and has("hevc_nvenc"):
            return (["-c:v","hevc_nvenc","-preset","p5","-rc","vbr","-cq",_crf(q,0,40),"-b:v","0"], "hevc_nvenc")
        if has("libx265"):
            a = ["-c:v","libx265","-preset",px,"-crf",_crf(q,10,38),"-tag:v","hvc1"]
            if lossless: a += ["-x265-params","lossless=1"]
            return (a, "libx265")
    if codec == "av1":
        if gpu_ok and has("av1_nvenc"):
            return (["-c:v","av1_nvenc","-preset","p5","-cq",_crf(q,0,50),"-b:v","0"], "av1_nvenc")
        if has("libsvtav1"):
            return (["-c:v","libsvtav1","-preset",str(max(0,min(13,13-int(speed*1.5)))),"-crf",_crf(q,10,50)], "libsvtav1")
        if has("libaom-av1"):
            return (["-c:v","libaom-av1","-cpu-used",str(max(0,min(8,int(speed)))),"-crf",_crf(q,10,50),"-b:v","0"], "libaom-av1")
    if codec == "vp9" and has("libvpx-vp9"):
        return (["-c:v","libvpx-vp9","-crf",_crf(q,0,50),"-b:v","0","-row-mt","1","-deadline","good","-cpu-used",str(max(0,min(5,int(speed/2))))], "libvpx-vp9")
    if codec == "vp8" and has("libvpx"):
        return (["-c:v","libvpx","-crf",_crf(q,4,40),"-b:v","2M"], "libvpx")
    if codec == "mpeg4" and has("mpeg4"):
        return (["-c:v","mpeg4","-q:v",str(max(1,min(31,int(round(31-(q/100)*30)))))], "mpeg4")
    if codec == "mpeg2" and has("mpeg2video"):
        return (["-c:v","mpeg2video","-q:v",str(max(1,min(31,int(round(31-(q/100)*30)))))], "mpeg2video")
    if codec == "mpeg1" and has("mpeg1video"):
        return (["-c:v","mpeg1video","-q:v",str(max(1,min(31,int(round(31-(q/100)*30)))))], "mpeg1video")
    if codec == "mjpeg" and has("mjpeg"):
        return (["-c:v","mjpeg","-q:v",str(max(1,min(31,int(round(31-(q/100)*30))))),"-pix_fmt","yuvj420p"], "mjpeg")
    if codec == "ffv1" and has("ffv1"):
        return (["-c:v","ffv1","-level","3","-coder","1","-context","1","-g","1"], "ffv1")
    if codec == "huffyuv" and has("huffyuv"):
        return (["-c:v","huffyuv"], "huffyuv")
    if codec == "prores" and has("prores_ks"):
        return (["-c:v","prores_ks","-profile:v","3","-pix_fmt","yuv422p10le"], "prores_ks")
    if codec == "dnxhd" and has("dnxhd"):
        return (["-c:v","dnxhd","-b:v","36M","-pix_fmt","yuv422p"], "dnxhd")
    if codec == "theora" and has("libtheora"):
        return (["-c:v","libtheora","-q:v",str(max(0,min(10,int(round(q/10)))))], "libtheora")
    if codec == "flv1" and has("flv"):
        return (["-c:v","flv1","-q:v","3"], "flv")
    if codec == "wmv2" and has("wmv2"):
        return (["-c:v","wmv2","-q:v",str(max(1,min(31,int(round(31-(q/100)*30)))))], "wmv2")
    if codec == "dv" and has("dvvideo"):
        return (["-c:v","dvvideo","-s","720x576","-pix_fmt","yuv420p"], "dvvideo")
    if codec == "gif":
        return ([], "gif")
    if codec == "apng" and has("apng"):
        return (["-c:v","apng","-plays","0"], "apng")
    if codec == "webp" and has("libwebp_anim"):
        return (["-c:v","libwebp_anim","-loop","0","-q:v",str(int(q))], "libwebp_anim")
    if codec == "raw":
        return (["-c:v","rawvideo"], "rawvideo")
    return (None, None)

def audio_codec_args(codec, br, encoders):
    """br en kbps. codec logique → args."""
    def has(n): return n in encoders
    b = f"{int(br)}k"
    m = {
        "aac":    (["-c:a","aac","-b:a",b], "aac"),
        "mp3":    (["-c:a","libmp3lame","-b:a",b], "libmp3lame"),
        "opus":   (["-c:a","libopus","-b:a",b], "libopus"),
        "vorbis": (["-c:a","libvorbis","-b:a",b], "libvorbis"),
        "flac":   (["-c:a","flac"], "flac"),
        "ac3":    (["-c:a","ac3","-b:a",b], "ac3"),
        "eac3":   (["-c:a","eac3","-b:a",b], "eac3"),
        "mp2":    (["-c:a","mp2","-b:a",b], "mp2"),
        "wma":    (["-c:a","wmav2","-b:a",b], "wmav2"),
        "pcm":    (["-c:a","pcm_s16le"], "pcm_s16le"),
        "alac":   (["-c:a","alac"], "alac"),
        "amr":    (["-c:a","libopencore_amrnb","-ar","8000","-ac","1","-b:a","12.2k"], "libopencore_amrnb"),
        "copy":   (["-c:a","copy"], "copy"),
    }
    if codec in m:
        a, enc = m[codec]
        if enc == "copy" or has(enc):
            return a
    return None

# Cible audio → (codec logique, args extra, muxer forcé éventuel)
AUDIO_MAP = {
    "mp3":  ("mp3",  [], None),
    "flac": ("flac", ["-compression_level","12"], None),
    "wav":  (None, ["-c:a","pcm_s24le"], None),
    "aac":  ("aac",  [], "adts"),
    "m4a":  ("aac",  [], "ipod"),
    "m4b":  ("aac",  [], "ipod"),
    "m4r":  ("aac",  [], "ipod"),
    "ogg":  ("vorbis", [], None),
    "oga":  ("vorbis", [], "ogg"),
    "opus": ("opus", [], None),
    "aiff": (None, ["-c:a","pcm_s16be"], None),
    "wma":  ("wma",  [], "asf"),
    "ac3":  ("ac3",  [], None),
    "eac3": ("eac3", [], None),
    "dts":  (None, ["-c:a","dca","-strict","-2","-b:a","768k"], None),
    "mka":  ("flac", [], "matroska"),
    "amr":  (None, ["-c:a","libopencore_amrnb","-ar","8000","-ac","1","-b:a","12.2k"], None),
    "au":   (None, ["-c:a","pcm_s16be"], None),
    "caf":  (None, ["-c:a","pcm_s16le"], None),
    "w64":  (None, ["-c:a","pcm_s16le"], None),
    "wv":   (None, ["-c:a","wavpack"], None),
    "tta":  (None, ["-c:a","tta"], None),
    "mp2":  ("mp2",  [], None),
    "mp1":  (None, ["-c:a","mp2","-b:a","192k"], "mp2"),
    "spx":  (None, ["-c:a","libspeex","-ar","16000"], None),
    "voc":  (None, ["-c:a","pcm_u8"], None),
    "ircam":(None, ["-c:a","pcm_s16le"], None),
    "gsm":  (None, ["-c:a","libgsm","-ar","8000","-ac","1"], "gsm"),
}

# Cible image → args ffmpeg (q = qualité 0..100)
def image_args(fmt, q, encoders):
    q = int(q)
    jq = str(max(1, min(31, int(round(31 - (q/100)*30)))))
    if fmt == "jpg":  return ["-c:v","mjpeg","-q:v",jq,"-pix_fmt","yuvj420p","-frames:v","1"]
    if fmt == "png":  return ["-c:v","png","-compression_level","9" if q < 100 else "0","-frames:v","1"]
    if fmt == "webp":
        a = ["-c:v","libwebp","-quality",str(q),"-frames:v","1"]
        if q >= 100: a += ["-lossless","1"]
        return a
    if fmt == "avif":
        if "libsvtav1" in encoders: return ["-c:v","libsvtav1","-crf",_crf(q,0,50),"-frames:v","1","-f","avif"]
        return ["-c:v","libaom-av1","-crf",_crf(q,0,50),"-b:v","0","-still-picture","1","-frames:v","1","-f","avif"]
    if fmt == "jxl":  return ["-c:v","libjxl","-distance",str(round((100-q)/12.0,2)),"-frames:v","1"]
    if fmt == "jp2":  return ["-c:v","jpeg2000","-frames:v","1"]
    if fmt == "tiff": return ["-c:v","tiff","-compression_algo","deflate","-frames:v","1"]
    if fmt == "bmp":  return ["-c:v","bmp","-frames:v","1"]
    if fmt == "gif":  return ["-c:v","gif"]
    if fmt == "ico":  return ["-c:v","bmp","-frames:v","1","-f","ico"]
    if fmt == "tga":  return ["-c:v","targa","-frames:v","1"]
    if fmt in ("ppm","pgm","pbm","pam","sgi","xbm","xwd","dpx","exr","hdr","qoi","pcx","psd"):
        codec = {"ppm":"ppm","pgm":"pgm","pbm":"pbm","pam":"pam","sgi":"sgi","xbm":"xbm",
                 "xwd":"xwd","dpx":"dpx","exr":"exr","hdr":"hdr","qoi":"qoi","pcx":"pcx","psd":"psd"}[fmt]
        return ["-c:v",codec,"-frames:v","1"]
    if fmt == "apng": return ["-c:v","apng","-plays","0"]
    if fmt == "wbmp": return ["-c:v","wbmp","-frames:v","1"]
    return []

# ═══════════════════════════════════════════════════════════════════════
#  DÉTECTION FFMPEG
# ═══════════════════════════════════════════════════════════════════════
def find_tool(name, hint=""):
    if hint and os.path.isfile(hint):
        return hint
    f = shutil.which(name)
    if f: return f
    for n in (name + ".exe", name, os.path.join("SmartCompress", name + ".exe")):
        p = os.path.join(HERE, n)
        if os.path.isfile(p): return p
    return ""

def run_quiet(cmd, timeout=30):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout,
                          creationflags=NOWIN, errors="replace")

_enc_cache = {}
def list_encoders(ff):
    if ff in _enc_cache: return _enc_cache[ff]
    out = set()
    try:
        r = run_quiet([ff, "-hide_banner", "-encoders"], 20)
        for line in r.stdout.splitlines():
            parts = line.split()
            if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
                out.add(parts[1])
    except Exception:
        pass
    _enc_cache[ff] = out
    return out

def gpu_available(ff):
    enc = list_encoders(ff)
    if not any(x in enc for x in ("h264_nvenc", "hevc_nvenc")):
        return False
    # Test réel : l'encodeur listé n'implique pas qu'une carte NVIDIA soit présente
    try:
        r = subprocess.run([ff,"-hide_banner","-f","lavfi","-i","color=black:s=256x256:d=0.1",
                            "-c:v","h264_nvenc","-f","null","-"],
                           capture_output=True, timeout=15, creationflags=NOWIN)
        return r.returncode == 0
    except Exception:
        return False

# ═══════════════════════════════════════════════════════════════════════
#  FILTRES (couleur + redimensionnement)
# ═══════════════════════════════════════════════════════════════════════
def color_filter(p):
    """
    p : dict avec sat, bri, con, gam (-100..100 pour sat/bri/con ; -100..100 pour gam)
        + hue (-180..180), temp (-100..100), expo (-100..100), vign (0..100),
          sharp (-100..100), blur (0..100), grain (0..100), gray, sepia, invert, denoise
    """
    fl = []
    g = lambda k, d=0: float(p.get(k, d) or 0)
    sat, bri, con, gam = g("sat"), g("bri"), g("con"), g("gam")
    hue, temp, expo = g("hue"), g("temp"), g("expo")

    if expo:
        fl.append(f"exposure=exposure={expo/50:.3f}")
    if bri or con or sat or gam:
        gf = max(0.1, min(10.0, 1 + gam / 50)) if gam else 1.0
        fl.append(f"eq=brightness={bri/100:.4f}:saturation={max(0,1+sat/100):.4f}:contrast={max(0.0,1+con/100):.4f}:gamma={gf:.4f}")
    if hue:
        fl.append(f"hue=h={hue:.1f}")
    if temp:
        # chaud (+) = plus de rouge / moins de bleu
        t = temp / 100 * 0.25
        fl.append(f"colorbalance=rs={t:.3f}:gs={t*0.15:.3f}:bs={-t:.3f}:rm={t:.3f}:bm={-t:.3f}")
    if p.get("sepia"):
        fl.append("colorchannelmixer=.393:.769:.189:0:.349:.686:.168:0:.272:.534:.131")
    if p.get("gray"):
        fl.append("hue=s=0")
    if p.get("invert"):
        fl.append("negate")
    dn = g("denoise")
    if dn > 0:
        fl.append(f"hqdn3d={dn/10:.2f}:{dn/10:.2f}:{dn/6:.2f}:{dn/6:.2f}")
    sh = g("sharp")
    if sh > 0:
        fl.append(f"unsharp=5:5:{sh/50:.2f}:5:5:0")
    elif sh < 0:
        fl.append(f"gblur=sigma={abs(sh)/20:.2f}")
    bl = g("blur")
    if bl > 0:
        fl.append(f"gblur=sigma={bl/5:.2f}")
    vg = g("vign")
    if vg > 0:
        fl.append(f"vignette=angle={vg/100*1.2:.3f}")
    gr = g("grain")
    if gr > 0:
        fl.append(f"noise=alls={int(gr/2)}:allf=t")
    return ",".join(fl)

def resize_filter(p, src_w=None, src_h=None):
    """
    Modes :
      height   → hauteur cible (largeur auto)
      width    → largeur cible (hauteur auto)
      fit      → boîte W×H, garde le ratio (tient dedans)
      fill     → boîte W×H, garde le ratio, recadre pour remplir
      exact    → W×H exact, ignore le ratio
      percent  → % de la taille d'origine
      longest  → plus grand côté
      shortest → plus petit côté
      megapix  → nombre de mégapixels cible
    'allow' : both | down | up   → autorise réduire, agrandir ou les deux
    """
    mode = p.get("mode", "height")
    if mode == "off": return ""
    allow = p.get("allow", "both")
    algo  = p.get("algo", "lanczos")
    val   = float(p.get("value", 1080) or 1080)
    W     = int(float(p.get("w", 0) or 0))
    H     = int(float(p.get("h", 0) or 0))
    pct   = float(p.get("percent", 100) or 100)
    even  = bool(p.get("even", False))
    ev = 2 if even else 1
    flags = f":flags={algo}" if algo else ""

    def q(expr): return expr.replace(",", "\\,")

    # Expressions "n'agrandit pas" / "n'agrandit que"
    if mode == "percent":
        f = pct / 100.0
        w = f"trunc(iw*{f}/{ev})*{ev}" if ev > 1 else f"round(iw*{f})"
        h = f"trunc(ih*{f}/{ev})*{ev}" if ev > 1 else f"round(ih*{f})"
        w = f"max(1,{w})"; h = f"max(1,{h})"
        if allow == "down" and pct > 100: return ""
        if allow == "up" and pct < 100: return ""
        return f"scale=w='{q(w)}':h='{q(h)}'{flags}"

    if mode == "height":
        t = int(val)
        cond = {"down": f"gt(ih,{t})", "up": f"lt(ih,{t})", "both": "1"}[allow]
        h = f"if({cond},{t},ih)"
        w = f"if({cond},max(1,trunc(iw*{t}/ih/{ev})*{ev}),iw)" if ev > 1 else f"if({cond},max(1,round(iw*{t}/ih)),iw)"
        return f"scale=w='{q(w)}':h='{q(h)}'{flags}"

    if mode == "width":
        t = int(val)
        cond = {"down": f"gt(iw,{t})", "up": f"lt(iw,{t})", "both": "1"}[allow]
        w = f"if({cond},{t},iw)"
        h = f"if({cond},max(1,trunc(ih*{t}/iw/{ev})*{ev}),ih)" if ev > 1 else f"if({cond},max(1,round(ih*{t}/iw)),ih)"
        return f"scale=w='{q(w)}':h='{q(h)}'{flags}"

    if mode in ("longest", "shortest"):
        t = int(val)
        side = "max(iw,ih)" if mode == "longest" else "min(iw,ih)"
        cond = {"down": f"gt({side},{t})", "up": f"lt({side},{t})", "both": "1"}[allow]
        ratio = f"({t}/{side})"
        w = f"if({cond},max(1,round(iw*{ratio}/{ev})*{ev}),iw)"
        h = f"if({cond},max(1,round(ih*{ratio}/{ev})*{ev}),ih)"
        return f"scale=w='{q(w)}':h='{q(h)}'{flags}"

    if mode == "megapix":
        mp = max(0.0001, val) * 1_000_000
        r = f"sqrt({mp}/(iw*ih))"
        cond = {"down": f"lt({r},1)", "up": f"gt({r},1)", "both": "1"}[allow]
        w = f"if({cond},max(1,round(iw*{r}/{ev})*{ev}),iw)"
        h = f"if({cond},max(1,round(ih*{r}/{ev})*{ev}),ih)"
        return f"scale=w='{q(w)}':h='{q(h)}'{flags}"

    if mode == "exact" and W > 0 and H > 0:
        return f"scale={W}:{H}{flags}"

    if mode in ("fit", "fill") and W > 0 and H > 0:
        if mode == "fit":
            if allow == "down":
                return f"scale=w='{q(f'min(iw,{W})')}':h='{q(f'min(ih,{H})')}':force_original_aspect_ratio=decrease{flags}"
            return f"scale={W}:{H}:force_original_aspect_ratio=decrease{flags}"
        # fill : agrandit/réduit pour couvrir la boîte puis recadre
        return f"scale={W}:{H}:force_original_aspect_ratio=increase{flags},crop={W}:{H}"

    return ""

# ═══════════════════════════════════════════════════════════════════════
#  UTILITAIRES DIVERS
# ═══════════════════════════════════════════════════════════════════════
def human(n):
    for u in ("o", "Ko", "Mo", "Go", "To"):
        if n < 1024: return f"{n:.0f} {u}" if u == "o" else f"{n:.1f} {u}"
        n /= 1024
    return f"{n:.1f} Po"

def scan(folder, recursive=True, kinds=None, exclude_markers=("_CONVTMP",)):
    out = []
    if os.path.isfile(folder):
        e = os.path.splitext(folder)[1].lower()
        k = ext_kind(e)
        return [folder] if k and (not kinds or k in kinds) else []
    if not os.path.isdir(folder): return out
    it = os.walk(folder) if recursive else [(folder, [], os.listdir(folder))]
    for r, _, fs in it:
        for f in fs:
            if any(m in f for m in exclude_markers): continue
            e = os.path.splitext(f)[1].lower()
            k = ext_kind(e)
            if k and (not kinds or k in kinds):
                out.append(os.path.join(r, f))
    return sorted(out)

def probe(ffprobe, path):
    try:
        r = run_quiet([ffprobe, "-v", "error", "-print_format", "json", "-show_format",
                       "-show_streams", path], 20)
        return json.loads(r.stdout or "{}")
    except Exception:
        return {}

# ═══════════════════════════════════════════════════════════════════════
#  API EXPOSÉE À L'INTERFACE
# ═══════════════════════════════════════════════════════════════════════
class Api:
    def __init__(self):
        self.cfg = cfg_load()
        self._window = None
        self._stop = Event()
        self._running = False
        self._lock = Lock()
        self._procs = set()
        self._auto_detect()

    # ── helpers internes ──
    def _auto_detect(self):
        ff = find_tool("ffmpeg", self.cfg.get("ffmpeg", ""))
        fp = find_tool("ffprobe", self.cfg.get("ffprobe", ""))
        if ff: self.cfg["ffmpeg"] = ff
        if fp: self.cfg["ffprobe"] = fp
        if ff and not fp:
            cand = os.path.join(os.path.dirname(ff), "ffprobe.exe" if os.name == "nt" else "ffprobe")
            if os.path.isfile(cand): self.cfg["ffprobe"] = cand
        cfg_save(self.cfg)

    def _emit(self, event, data=None):
        """Envoie un événement au JS (thread-safe)."""
        if not self._window: return
        try:
            payload = json.dumps({"event": event, "data": data}, ensure_ascii=False)
            self._window.evaluate_js(f"window.onPy && window.onPy({payload})")
        except Exception:
            pass

    def _log(self, msg, kind="info"):
        self._emit("log", {"msg": msg, "kind": kind})

    def _need_ff(self):
        ff = self.cfg.get("ffmpeg", "")
        if not ff or not os.path.isfile(ff):
            return None
        return ff

    # ── init / config ──
    def init(self):
        ff = self.cfg.get("ffmpeg", "")
        info = {
            "cfg": self.cfg, "has_ffmpeg": bool(ff and os.path.isfile(ff)),
            "has_pil": HAS_PIL,
            "targets": {
                "video": {k: v["label"] for k, v in VIDEO_TARGETS.items()},
                "audio": {k: v["label"] for k, v in AUDIO_TARGETS.items()},
                "image": {k: v["label"] for k, v in IMAGE_TARGETS.items()},
            },
            "video_codecs": {k: v["vcodecs"] for k, v in VIDEO_TARGETS.items()},
            "audio_codecs": {k: v["acodecs"] for k, v in VIDEO_TARGETS.items()},
            "src_counts": {"video": len(V_SRC), "image": len(I_SRC), "audio": len(A_SRC)},
        }
        return info

    def set_cfg(self, key, value):
        self.cfg[key] = value
        cfg_save(self.cfg)
        return True

    def check_ffmpeg(self, path=""):
        ff = find_tool("ffmpeg", path or self.cfg.get("ffmpeg", ""))
        if not ff:
            return {"ok": False}
        self.cfg["ffmpeg"] = ff
        fp = find_tool("ffprobe", "") or os.path.join(os.path.dirname(ff), "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(fp): self.cfg["ffprobe"] = fp
        cfg_save(self.cfg)
        enc = list_encoders(ff)
        try:
            ver = run_quiet([ff, "-version"], 8).stdout.splitlines()[0]
        except Exception:
            ver = "?"
        key_enc = ["libx264","libx265","libsvtav1","libaom-av1","libvpx-vp9","libmp3lame","libopus",
                   "libvorbis","flac","libwebp","libjxl","prores_ks","ffv1","h264_nvenc","hevc_nvenc",
                   "av1_nvenc","h264_qsv","h264_amf"]
        return {"ok": True, "path": ff, "version": ver, "gpu": gpu_available(ff),
                "n_encoders": len(enc), "encoders": {k: (k in enc) for k in key_enc}}

    # ── boîtes de dialogue ──
    def pick_folder(self):
        r = self._window.create_file_dialog(webview.FOLDER_DIALOG)
        return r[0] if r else ""

    def pick_files(self, kind="all"):
        exts = {"image": I_SRC, "video": V_SRC, "audio": A_SRC}.get(kind, ALL_SRC)
        ft = ("Médias (" + ";".join("*" + e for e in sorted(exts)[:12]) + "...)",
              ";".join("*" + e for e in sorted(exts)))
        try:
            r = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True,
                                                file_types=(f"Médias ({';'.join('*'+e for e in sorted(exts))})", "Tous (*.*)"))
        except Exception:
            r = self._window.create_file_dialog(webview.OPEN_DIALOG, allow_multiple=True)
        return list(r) if r else []

    def pick_file(self, kind="all"):
        r = self.pick_files(kind)
        return r[0] if r else ""

    def pick_exe(self):
        try:
            r = self._window.create_file_dialog(webview.OPEN_DIALOG,
                                                file_types=("Exécutable (*.exe)", "Tous (*.*)"))
        except Exception:
            r = self._window.create_file_dialog(webview.OPEN_DIALOG)
        return r[0] if r else ""

    def open_folder(self, path):
        try:
            p = path if os.path.isdir(path) else os.path.dirname(path)
            if os.name == "nt": os.startfile(p)
            elif sys.platform == "darwin": subprocess.Popen(["open", p])
            else: subprocess.Popen(["xdg-open", p])
        except Exception:
            pass
        return True

    def open_url(self, url):
        import webbrowser; webbrowser.open(url); return True

    # ── scan ──
    def scan_path(self, path, kinds=None, recursive=True):
        files = scan(path, recursive, set(kinds) if kinds else None)
        total = 0; by = {"video": 0, "image": 0, "audio": 0}
        items = []
        for f in files:
            k = ext_kind(os.path.splitext(f)[1]); by[k] += 1
            try: sz = os.path.getsize(f)
            except Exception: sz = 0
            total += sz
            if len(items) < 500:
                items.append({"path": f, "name": os.path.basename(f), "kind": k, "size": sz})
        return {"count": len(files), "by_kind": by, "size": human(total), "items": items}

    def scan_paths(self, paths, kinds=None, recursive=True):
        seen, files = set(), []
        for p in paths:
            for f in scan(p, recursive, set(kinds) if kinds else None):
                if f not in seen: seen.add(f); files.append(f)
        by = {"video": 0, "image": 0, "audio": 0}; total = 0; items = []
        for f in files:
            k = ext_kind(os.path.splitext(f)[1]); by[k] += 1
            try: sz = os.path.getsize(f)
            except Exception: sz = 0
            total += sz
            if len(items) < 500:
                items.append({"path": f, "name": os.path.basename(f), "kind": k, "size": sz})
        return {"count": len(files), "by_kind": by, "size": human(total), "items": items}

    def media_info(self, path):
        fp = self.cfg.get("ffprobe", "")
        if not fp or not os.path.isfile(fp):
            return {"ok": False}
        d = probe(fp, path)
        out = {"ok": True, "size": human(os.path.getsize(path)) if os.path.isfile(path) else "?"}
        fm = d.get("format", {})
        out["duration"] = float(fm.get("duration", 0) or 0)
        out["bitrate"] = int(float(fm.get("bit_rate", 0) or 0) / 1000)
        for s in d.get("streams", []):
            if s.get("codec_type") == "video" and "w" not in out:
                out.update(w=s.get("width"), h=s.get("height"), vcodec=s.get("codec_name"),
                           fps=s.get("r_frame_rate"), pix=s.get("pix_fmt"))
            if s.get("codec_type") == "audio" and "acodec" not in out:
                out.update(acodec=s.get("codec_name"), sr=s.get("sample_rate"), ch=s.get("channels"))
        return out

    # ── APERÇU COULEURS / REDIMENSIONNEMENT ──
    def preview(self, path, color, resize, maxdim=900, at=0.0):
        """
        Génère un aperçu AVANT/APRÈS (PNG base64) d'un fichier image ou d'un
        instant d'une vidéo, en appliquant EXACTEMENT les mêmes filtres ffmpeg
        que la conversion finale → l'aperçu est fidèle.
        """
        ff = self._need_ff()
        if not ff: return {"ok": False, "err": "FFmpeg non configuré"}
        if not os.path.isfile(path): return {"ok": False, "err": "Fichier introuvable"}
        kind = ext_kind(os.path.splitext(path)[1])

        tmpdir = tempfile.mkdtemp(prefix="mtk_")
        try:
            src = path
            # Formats que ffmpeg lit mal (HEIC, SVG, PSD...) → conversion via Pillow
            ext = os.path.splitext(path)[1].lower()
            if kind == "image" and ext in (".heic", ".heif", ".svg", ".psd", ".ico", ".cur", ".dds") and HAS_PIL:
                try:
                    im = Image.open(path); im.load()
                    src = os.path.join(tmpdir, "src.png")
                    im.convert("RGBA").save(src)
                except Exception:
                    pass

            info = self.media_info(path) if kind != "audio" else {}
            ow, oh = info.get("w"), info.get("h")
            if (not ow) and HAS_PIL and kind == "image":
                try:
                    with Image.open(src) as im: ow, oh = im.size
                except Exception: pass

            def render(vf, name):
                outp = os.path.join(tmpdir, name)
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]
                if kind == "video" and at: cmd += ["-ss", str(at)]
                cmd += ["-i", src]
                if kind == "video": cmd += ["-frames:v", "1"]
                else: cmd += ["-frames:v", "1"]
                # Réduit l'aperçu à maxdim pour rester fluide, APRÈS les filtres utilisateur
                chain = [x for x in (vf,) if x]
                chain.append(f"scale='if(gt(iw,ih),min(iw,{maxdim}),-2)':'if(gt(iw,ih),-2,min(ih,{maxdim}))':flags=fast_bilinear")
                cmd += ["-vf", ",".join(chain), outp]
                r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NOWIN)
                if r.returncode != 0 or not os.path.isfile(outp):
                    return None, r.stderr.decode("utf-8", "replace")[-300:]
                return outp, ""

            vf_after = ",".join(x for x in (resize_filter(resize or {"mode": "off"}) if resize else "",
                                            color_filter(color or {})) if x)
            res = {"ok": True, "orig_w": ow, "orig_h": oh}
            b, err = render("", "before.png")
            if not b: return {"ok": False, "err": err or "Rendu impossible"}
            a, err2 = render(vf_after, "after.png")
            if not a: return {"ok": False, "err": err2 or "Filtre invalide"}
            res["before"] = "data:image/png;base64," + base64.b64encode(open(b, "rb").read()).decode()
            res["after"]  = "data:image/png;base64," + base64.b64encode(open(a, "rb").read()).decode()

            # dimensions finales (sans réduction d'aperçu)
            if ow and oh:
                fw, fh = self._final_size(ow, oh, resize)
                res["final_w"], res["final_h"] = fw, fh
            return res
        except Exception as e:
            return {"ok": False, "err": str(e)}
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _final_size(self, w, h, r):
        """Calcule la taille finale en Python (reflète la logique de resize_filter)."""
        if not r or r.get("mode", "off") == "off": return w, h
        mode = r.get("mode"); allow = r.get("allow", "both")
        val = float(r.get("value", 0) or 0); W = int(float(r.get("w", 0) or 0)); H = int(float(r.get("h", 0) or 0))
        pct = float(r.get("percent", 100) or 100); even = bool(r.get("even"))
        def ok(scale):  # respecte 'allow'
            return (allow == "both") or (allow == "down" and scale < 1) or (allow == "up" and scale > 1)
        def fin(nw, nh):
            nw, nh = max(1, int(round(nw))), max(1, int(round(nh)))
            if even: nw -= nw % 2; nh -= nh % 2; nw, nh = max(2, nw), max(2, nh)
            return nw, nh
        if mode == "percent":
            s = pct / 100
            return fin(w*s, h*s) if ok(s) else (w, h)
        if mode == "height" and val:
            s = val / h; return fin(w*s, val) if ok(s) else (w, h)
        if mode == "width" and val:
            s = val / w; return fin(val, h*s) if ok(s) else (w, h)
        if mode == "longest" and val:
            s = val / max(w, h); return fin(w*s, h*s) if ok(s) else (w, h)
        if mode == "shortest" and val:
            s = val / min(w, h); return fin(w*s, h*s) if ok(s) else (w, h)
        if mode == "megapix" and val:
            s = ((val*1e6)/(w*h)) ** 0.5; return fin(w*s, h*s) if ok(s) else (w, h)
        if mode == "exact" and W and H: return fin(W, H)
        if mode in ("fit", "fill") and W and H:
            s = min(W/w, H/h) if mode == "fit" else max(W/w, H/h)
            if mode == "fit": return fin(w*s, h*s) if ok(s) else (w, h)
            return fin(W, H)
        return w, h

    # ── contrôle du traitement ──
    def stop(self):
        self._stop.set()
        with self._lock:
            for p in list(self._procs):
                try: p.terminate()
                except Exception: pass
        return True

    def is_running(self): return self._running

    def _run_ff(self, cmd, timeout=None):
        """Exécute ffmpeg avec possibilité d'annulation."""
        p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=NOWIN)
        with self._lock: self._procs.add(p)
        try:
            _, err = p.communicate(timeout=timeout)
            return p.returncode, err.decode("utf-8", "replace")
        except subprocess.TimeoutExpired:
            p.kill(); p.communicate(); return -9, "timeout"
        finally:
            with self._lock: self._procs.discard(p)

    def _dest(self, path, new_ext, opts, tag=""):
        """Détermine le chemin de sortie selon le mode choisi."""
        base, _ = os.path.splitext(path)
        mode = opts.get("overwrite_mode", "replace")
        ne = ("." + new_ext.lstrip(".")) if new_ext else os.path.splitext(path)[1]
        if mode == "suffix":
            return base + (opts.get("suffix", "_conv") or "_conv") + ne
        if mode == "subfolder":
            d = os.path.join(os.path.dirname(path), opts.get("subfolder", "converted") or "converted")
            os.makedirs(d, exist_ok=True)
            return os.path.join(d, os.path.basename(base) + ne)
        return base + ne

    def _finish_file(self, src, tmp, dst, opts):
        """Remplace / déplace le fichier temporaire vers la destination finale."""
        if not (os.path.isfile(tmp) and os.path.getsize(tmp) > 0):
            if os.path.isfile(tmp): os.remove(tmp)
            return False
        same = os.path.normcase(os.path.abspath(dst)) == os.path.normcase(os.path.abspath(src))
        if opts.get("overwrite_mode", "replace") == "replace":
            if not same and opts.get("delete_source", True) and os.path.isfile(src):
                try: os.remove(src)
                except Exception: pass
        os.replace(tmp, dst)
        return True

    # ── TRAITEMENT PRINCIPAL (un seul moteur pour tout) ──
    def process(self, job):
        """
        job = {
          files: [chemins],  # fichiers explicites
          source: chemin (fichier ou dossier) — utilisé si files vide,
          recursive: bool,
          mode: 'convert' | 'color' | 'resize' | 'combo' | 'tools',
          targets: {video, image, audio, ...},
          quality: 0..100, speed: 0..8, vcodec, acodec, abitrate,
          color: {...}, resize: {...},
          apply_to: ['image','video'],
          opts: {overwrite_mode, suffix, subfolder, delete_source, keep_metadata, threads, use_gpu},
          tools: {...}
        }
        """
        ff = self._need_ff()
        if not ff:
            return {"ok": False, "err": "FFmpeg non configuré (onglet Paramètres)"}
        with self._lock:
            if self._running: return {"ok": False, "err": "Un traitement est déjà en cours"}
            self._stop.clear(); self._running = True
        threading.Thread(target=self._process_thread, args=(job, ff), daemon=True).start()
        return {"ok": True}

    def _process_thread(self, job, ff):
        try:
            opts = dict(self.cfg); opts.update(job.get("opts", {}))
            self.cfg.update({k: v for k, v in job.get("opts", {}).items() if k in DEFAULTS})
            cfg_save(self.cfg)

            kinds = set(job.get("apply_to") or ["image", "video", "audio"])
            files = job.get("files") or []
            if not files:
                files = scan(job.get("source", ""), job.get("recursive", True), kinds)
            else:
                files = [f for f in files if ext_kind(os.path.splitext(f)[1]) in kinds]

            total = len(files)
            if not total:
                self._emit("done", {"ok": 0, "err": 0, "skip": 0, "total": 0, "msg": "Aucun fichier compatible"})
                return

            enc = list_encoders(ff)
            gpu = bool(opts.get("use_gpu", True)) and gpu_available(ff)
            self._log(f"{total} fichier(s)  •  GPU : {'oui' if gpu else 'non (CPU)'}  •  "
                      f"mode : {job.get('mode')}", "info")
            self._emit("progress", {"done": 0, "total": total, "ok": 0, "err": 0, "skip": 0})

            st = {"ok": 0, "err": 0, "skip": 0, "saved": 0}
            lk = Lock()

            def bump(kind):
                with lk:
                    st[kind] += 1
                    d = st["ok"] + st["err"] + st["skip"]
                    self._emit("progress", {"done": d, "total": total, "ok": st["ok"],
                                            "err": st["err"], "skip": st["skip"]})

            def work(path):
                if self._stop.is_set():
                    return
                try:
                    r = self._one(path, job, opts, ff, enc, gpu)
                except Exception as e:
                    r = ("err", f"{os.path.basename(path)} — {e}")
                kind, msg = r
                if kind == "ok":
                    self._log("✓  " + msg, "ok")
                elif kind == "skip":
                    self._log("—  " + msg, "dim")
                else:
                    self._log("✗  " + msg, "err")
                bump(kind)

            nthreads = max(1, int(opts.get("threads", 2)))
            with ThreadPoolExecutor(nthreads) as ex:
                list(ex.map(work, files))

            stopped = self._stop.is_set()
            self._emit("done", {"ok": st["ok"], "err": st["err"], "skip": st["skip"], "total": total,
                                "stopped": stopped})
        except Exception as e:
            self._log(f"Erreur fatale : {e}", "err")
            self._emit("done", {"ok": 0, "err": 1, "skip": 0, "total": 0, "msg": str(e)})
        finally:
            self._running = False

    # Traite UN fichier → ('ok'|'err'|'skip', message)
    def _one(self, path, job, opts, ff, enc, gpu):
        ext  = os.path.splitext(path)[1].lower()
        kind = ext_kind(ext)
        mode = job.get("mode", "convert")
        name = os.path.basename(path)
        q    = int(job.get("quality", 90))
        speed = int(job.get("speed", 4))

        color  = job.get("color") or {}
        resize = job.get("resize") or {}
        cf = color_filter(color) if mode in ("color", "combo") else ""
        rf = resize_filter(resize) if mode in ("resize", "combo") else ""
        vfilters = ",".join(x for x in (rf, cf) if x)

        tg = job.get("targets", {})
        target = tg.get(kind) or ""       # '' ou 'same' → garde le format
        if target == "same": target = ""

        # ─ 'convert' : ignorer si déjà au format cible
        if mode == "convert":
            if not target or ext == "." + target:
                return ("skip", f"{name}  (déjà en {ext.lstrip('.')})")

        if mode in ("color", "resize") and kind == "audio":
            return ("skip", f"{name}  (audio ignoré)")
        if mode in ("color", "resize", "combo") and kind == "audio" and not target:
            return ("skip", f"{name}  (audio ignoré)")

        new_ext = target or ext.lstrip(".")
        dst = self._dest(path, new_ext, opts)
        tmp = os.path.join(os.path.dirname(dst), os.path.splitext(os.path.basename(dst))[0] + "_CONVTMP." + new_ext.lstrip("."))
        if not opts.get("keep_metadata", True):
            meta = ["-map_metadata", "-1"]
        else:
            meta = ["-map_metadata", "0"]

        # ─ Cas spécial : HEIC / SVG / PSD en sortie ou en entrée via Pillow
        if kind == "image" and HAS_PIL and (target == "heic" or ext in (".heic", ".heif", ".svg", ".psd", ".cur", ".dds", ".jxr", ".wdp", ".fits", ".pict")):
            return self._one_pillow(path, dst, tmp, target or ext.lstrip("."), q, job, opts, color, resize)

        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]

        if kind == "video":
            vcodec = job.get("vcodec") or (VIDEO_TARGETS.get(new_ext, {}).get("vcodecs") or ["h264"])[0]
            acodec = job.get("acodec") or "aac"
            hw = ["-hwaccel", "cuda"] if gpu and vcodec in ("h264", "h265", "av1") else []
            cmd += hw + ["-i", path]
            if new_ext in ("gif",):
                pal = (vfilters + "," if vfilters else "") + "fps=15,scale='min(720,iw)':-2:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
                cmd += ["-filter_complex", pal, "-an", "-loop", "0"]
            elif new_ext in ("apng", "webp"):
                va, _ = video_codec_args(VIDEO_TARGETS[new_ext]["vcodecs"][0], q, speed, gpu, enc)
                if va is None: return ("err", f"{name}  (encodeur {new_ext} absent)")
                cmd += (["-vf", vfilters] if vfilters else []) + ["-an"] + va
            else:
                if mode == "convert" or mode in ("color", "resize", "combo"):
                    if mode in ("color", "resize", "combo") and not target:
                        # Garde le conteneur d'origine mais ré-encode
                        vcodec = job.get("vcodec") or "h264"
                        if ext in (".webm",): vcodec = "vp9"
                    va, used = video_codec_args(vcodec, q, speed, gpu, enc)
                    if va is None:
                        return ("err", f"{name}  (encodeur '{vcodec}' introuvable dans ce FFmpeg)")
                    if vfilters: cmd += ["-vf", vfilters]
                    cmd += va
                    if new_ext in VIDEO_TARGETS and VIDEO_TARGETS[new_ext]["acodecs"] or True:
                        ac = audio_codec_args(acodec, job.get("abitrate", 192), enc)
                        # WebM impose Opus/Vorbis
                        if new_ext == "webm" and acodec not in ("opus", "vorbis"):
                            ac = audio_codec_args("opus", job.get("abitrate", 192), enc)
                        if ac is None:
                            ac = audio_codec_args("aac", 192, enc) or ["-an"]
                        cmd += ac
                    if new_ext in ("mp4", "mov", "m4v", "3gp", "3g2", "f4v"):
                        cmd += ["-movflags", "+faststart"]
            cmd += meta + [tmp]

        elif kind == "image":
            ia = image_args(new_ext, q, enc)
            if new_ext in ("gif", "apng"): pass
            if not ia and new_ext not in IMAGE_TARGETS:
                return ("err", f"{name}  (format image '{new_ext}' non géré)")
            # GIF / APNG / WebP animés en entrée : ne garde tout que si cible animée
            cmd += ["-i", path]
            vf = vfilters
            if new_ext == "ico":
                vf = (vf + "," if vf else "") + "scale='min(256,iw)':'min(256,ih)':force_original_aspect_ratio=decrease,pad=256:256:(ow-iw)/2:(oh-ih)/2:color=0x00000000,format=bgra"
            elif new_ext in ("jpg", "bmp", "ppm", "pgm", "pbm", "pcx", "wbmp", "xbm"):
                vf = (vf + "," if vf else "") + "format=" + {"jpg": "yuvj420p", "pgm": "gray", "pbm": "monob", "wbmp": "monob", "xbm": "monob"}.get(new_ext, "rgb24")
            if new_ext in ("jpg", "webp", "png", "avif", "tiff", "tga", "jxl", "jp2") and ext == ".png":
                pass
            if vf: cmd += ["-vf", vf]
            cmd += ia + meta + [tmp]

        else:  # audio
            if not target: return ("skip", f"{name}  (pas de format audio choisi)")
            spec = AUDIO_MAP.get(new_ext)
            if not spec: return ("err", f"{name}  (format audio '{new_ext}' non géré)")
            codec, extra, muxer = spec
            br = job.get("abitrate", 320)
            if codec:
                aa = audio_codec_args(codec, br, enc)
                if aa is None: return ("err", f"{name}  (encodeur audio '{codec}' introuvable)")
            else:
                aa = []
            cmd += ["-i", path, "-vn"] + aa + extra
            # tags / pochette
            cmd += meta
            if muxer: cmd += ["-f", muxer]
            cmd += [tmp]

        rc, err = self._run_ff(cmd)
        if self._stop.is_set():
            if os.path.isfile(tmp): os.remove(tmp)
            return ("skip", f"{name}  (annulé)")
        if rc != 0:
            if os.path.isfile(tmp):
                try: os.remove(tmp)
                except Exception: pass
            lines = [l for l in err.strip().splitlines() if l.strip()]
            return ("err", f"{name}  —  {lines[-1][:160] if lines else 'erreur ffmpeg'}")

        before = os.path.getsize(path) if os.path.isfile(path) else 0
        if self._finish_file(path, tmp, dst, opts):
            after = os.path.getsize(dst)
            delta = ""
            if before and after:
                pc = (after - before) / before * 100
                delta = f"  ({human(before)} → {human(after)}, {pc:+.0f} %)"
            return ("ok", f"{os.path.basename(dst)}{delta}")
        return ("err", f"{name}  (sortie vide)")

    def _one_pillow(self, path, dst, tmp, target, q, job, opts, color, resize):
        """Repli Pillow pour HEIC / SVG / PSD, etc. (formats que ffmpeg gère mal)."""
        name = os.path.basename(path)
        try:
            im = Image.open(path); im.load()
            mode = job.get("mode", "convert")
            if mode in ("resize", "combo") and resize and resize.get("mode") != "off":
                w, h = im.size
                nw, nh = self._final_size(w, h, resize)
                if (nw, nh) != (w, h):
                    if resize.get("mode") == "fill":
                        s = max(nw / w, nh / h)
                        im = im.resize((max(1, round(w*s)), max(1, round(h*s))), Image.LANCZOS)
                        l = (im.width - nw)//2; t = (im.height - nh)//2
                        im = im.crop((l, t, l+nw, t+nh))
                    else:
                        im = im.resize((nw, nh), Image.LANCZOS)
            if mode in ("color", "combo") and color:
                from PIL import ImageEnhance
                im = im.convert("RGBA" if "A" in im.getbands() else "RGB")
                if color.get("bri"): im = ImageEnhance.Brightness(im).enhance(max(0, 1 + color["bri"]/100))
                if color.get("con"): im = ImageEnhance.Contrast(im).enhance(max(0, 1 + color["con"]/100))
                if color.get("sat"): im = ImageEnhance.Color(im).enhance(max(0, 1 + color["sat"]/100))
            fmt = {"jpg": "JPEG", "jpeg": "JPEG", "png": "PNG", "webp": "WEBP", "tiff": "TIFF", "bmp": "BMP",
                   "gif": "GIF", "heic": "HEIF", "avif": "AVIF", "ico": "ICO", "jp2": "JPEG2000"}.get(target, target.upper())
            if fmt == "JPEG" and im.mode in ("RGBA", "P", "LA"):
                bgc = Image.new("RGB", im.size, (255, 255, 255))
                bgc.paste(im, mask=im.split()[-1] if "A" in im.getbands() else None); im = bgc
            im.save(tmp, format=fmt, quality=q)
            before = os.path.getsize(path)
            if self._finish_file(path, tmp, dst, opts):
                return ("ok", f"{os.path.basename(dst)}  ({human(before)} → {human(os.path.getsize(dst))})")
            return ("err", f"{name}  (sortie vide)")
        except Exception as e:
            if os.path.isfile(tmp):
                try: os.remove(tmp)
                except Exception: pass
            return ("err", f"{name}  —  {e}")

    # ── OUTILS VIDÉO / AUDIO (extraire, couper, fusionner, etc.) ──
    def run_tool(self, job):
        """
        job = {tool, files, out_dir, params}
        tools : extract_audio | trim | speed | gif | thumbnail | strip_audio | mute
                normalize | volume | rotate | flip | merge_audio | frames | compress_target
        """
        ff = self._need_ff()
        if not ff: return {"ok": False, "err": "FFmpeg non configuré"}
        with self._lock:
            if self._running: return {"ok": False, "err": "Un traitement est déjà en cours"}
            self._stop.clear(); self._running = True
        threading.Thread(target=self._tool_thread, args=(job, ff), daemon=True).start()
        return {"ok": True}

    def _tool_thread(self, job, ff):
        try:
            tool = job["tool"]; p = job.get("params", {}); files = job.get("files", [])
            enc = list_encoders(ff)
            opts = dict(self.cfg); opts.update(job.get("opts", {}))
            total = len(files) if tool != "merge" else 1
            self._emit("progress", {"done": 0, "total": total, "ok": 0, "err": 0, "skip": 0})
            st = {"ok": 0, "err": 0}
            lk = Lock()

            def done_one(ok, msg):
                with lk:
                    st["ok" if ok else "err"] += 1
                    self._log(("✓  " if ok else "✗  ") + msg, "ok" if ok else "err")
                    self._emit("progress", {"done": st["ok"]+st["err"], "total": total,
                                            "ok": st["ok"], "err": st["err"], "skip": 0})

            if tool == "merge":
                self._tool_merge(ff, files, p, opts, enc, done_one)
            else:
                def work(path):
                    if self._stop.is_set(): return
                    try:
                        ok, msg = self._tool_one(ff, tool, path, p, opts, enc)
                    except Exception as e:
                        ok, msg = False, f"{os.path.basename(path)} — {e}"
                    done_one(ok, msg)
                with ThreadPoolExecutor(max(1, int(opts.get("threads", 2)))) as ex:
                    list(ex.map(work, files))
            self._emit("done", {"ok": st["ok"], "err": st["err"], "skip": 0, "total": total,
                                "stopped": self._stop.is_set()})
        except Exception as e:
            self._log(f"Erreur : {e}", "err")
            self._emit("done", {"ok": 0, "err": 1, "skip": 0, "total": 0})
        finally:
            self._running = False

    def _tool_out(self, path, ext, opts, suffix):
        base = os.path.splitext(os.path.basename(path))[0]
        d = os.path.dirname(path)
        mode = opts.get("overwrite_mode", "replace")
        if mode == "subfolder":
            d = os.path.join(d, opts.get("subfolder", "converted") or "converted")
            os.makedirs(d, exist_ok=True)
        # Les outils ne remplacent JAMAIS l'original : ils ajoutent toujours un suffixe
        return os.path.join(d, f"{base}{suffix}.{ext.lstrip('.')}")

    def _smart_command(self, ff, path, out, p, start=None, duration=None):
        vb, ab = int(p.get("video_kbps", 1000)), int(p.get("audio_kbps", 96))
        height = int(p.get("height", 720))
        if not 50 <= vb <= 100000 or not 16 <= ab <= 320:
            raise ValueError("Débits autorisés : vidéo 50–100000, audio 16–320 kbit/s")
        if height not in (0, 360, 480, 720, 1080): raise ValueError("Résolution invalide")
        codec, speed = p.get("codec", "h264"), p.get("preset", "medium")
        if codec not in ("h264", "h265"): raise ValueError("Codec invalide")
        if speed not in ("fast", "medium", "slow"): raise ValueError("Vitesse invalide")
        cmd = [ff, "-hide_banner", "-loglevel", "error", "-y"]
        if start is not None: cmd += ["-ss", str(start)]
        cmd += ["-i", path]
        if duration is not None: cmd += ["-t", str(duration)]
        if ext_kind(os.path.splitext(path)[1]) == "audio":
            cmd += ["-map", "0:a:0", "-vn"]
        else:
            cmd += ["-map", "0:v:0", "-map", "0:a:0?", "-c:v",
                    "libx265" if codec == "h265" else "libx264", "-preset", speed,
                    "-b:v", f"{vb}k", "-maxrate", f"{vb * 2}k", "-bufsize", f"{vb * 2}k", "-pix_fmt", "yuv420p"]
            vf = f"scale=-2:trunc(min(ih\\,{height})/2)*2" if height else "scale=trunc(iw/2)*2:trunc(ih/2)*2"
            cmd += ["-vf", vf]
            if codec == "h265": cmd += ["-tag:v", "hvc1"]
        return cmd + ["-c:a", "aac", "-b:a", f"{ab}k", "-ac", "2", "-map_metadata", "0", "-movflags", "+faststart", out]

    def _smart_export(self, ff, tool, path, p, opts):
        kind = ext_kind(os.path.splitext(path)[1])
        if kind not in ("video", "audio"): return False, "Sélectionne une vidéo ou un fichier audio"
        if tool == "unusual_mp4" and os.path.splitext(path)[1].lower() not in {".ts", ".m3u8", ".m4s", ".mts", ".m2ts"}:
            return True, os.path.basename(path) + " — format non concerné, ignoré"
        ext = "m4a" if kind == "audio" else "mp4"
        dst = self._tool_out(path, ext, opts, "_compressed" if tool == "smart_compress" else "_mp4")
        base, n = os.path.splitext(dst)[0], 1
        while True:
            try:
                with open(dst, "xb"): pass
                break
            except FileExistsError:
                dst = f"{base}_{n}.{ext}"; n += 1
        tmp, saved = None, False
        try:
            fd, tmp = tempfile.mkstemp(prefix="_CONVTMP", suffix="." + ext, dir=os.path.dirname(dst)); os.close(fd)
            if self._stop.is_set(): return False, "Annulé"
            self._log("Traitement : " + os.path.basename(path))
            if tool == "unusual_mp4":
                cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", path, "-map", "0:v:0", "-map", "0:a:0?", "-c", "copy", "-movflags", "+faststart", tmp]
                rc, err = self._run_ff(cmd)
                if rc and not self._stop.is_set():
                    self._log("Copie incompatible : ré-encodage H.264/AAC", "warn")
                    cmd = [ff, "-hide_banner", "-loglevel", "error", "-y", "-i", path,
                           "-map", "0:v:0", "-map", "0:a:0?", "-c:v", "libx264", "-preset", "fast", "-crf", "22",
                           "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-movflags", "+faststart", tmp]
                    rc, err = self._run_ff(cmd)
            else: rc, err = self._run_ff(self._smart_command(ff, path, tmp, p))
            if self._stop.is_set(): return False, "Annulé : " + os.path.basename(path)
            if rc or not os.path.getsize(tmp):
                return False, os.path.basename(path) + " — " + err[-1200:] + (" — Un segment M4S isolé peut nécessiter sa playlist et son fichier d’initialisation." if path.lower().endswith(".m4s") else "")
            before, after = os.path.getsize(path), os.path.getsize(tmp)
            if tool == "smart_compress" and p.get("only_smaller", True) and after >= before:
                return True, os.path.basename(path) + " — aucun gain, original conservé sans nouvelle copie"
            os.replace(tmp, dst); saved = True
            return True, f"{os.path.basename(dst)} — {human(before)} → {human(after)} (gain {100 * (1-after/max(1,before)):.1f} %)"
        finally:
            if tmp and os.path.exists(tmp): os.remove(tmp)
            if not saved and os.path.exists(dst): os.remove(dst)

    def smart_preview(self, path, params):
        with self._lock:
            if self._running: return {"ok": False, "err": "Un traitement est déjà en cours"}
            self._running = True
            self._stop.clear()
        try:
            ff = self._need_ff()
            if not ff: raise ValueError("FFmpeg non configuré")
            if ext_kind(os.path.splitext(path)[1]) not in ("audio", "video"): raise ValueError("Sélectionne une vidéo ou un fichier audio")
            at = float(params.get("at", 0))
            if not 0 <= at <= 864000: raise ValueError("Position d’aperçu invalide")
            audio_only = ext_kind(os.path.splitext(path)[1]) == "audio"
            with tempfile.TemporaryDirectory(prefix="mediatoolkit-preview-") as td:
                out = os.path.join(td, "preview.m4a" if audio_only else "preview.mp4")
                rc, err = self._run_ff(self._smart_command(ff, path, out, params, at, 5), timeout=180)
                if self._stop.is_set(): raise ValueError("Aperçu annulé")
                if rc: raise ValueError(err[-1200:])
                info = probe(self.cfg.get("ffprobe", ""), out)
                dur = float(info.get("format", {}).get("duration", 0) or 0)
                if dur <= 0: raise ValueError("Extrait vide ou ffprobe absent : choisis une position dans le média")
                size = os.path.getsize(out)
                if size > 64 * 1024 * 1024: raise ValueError("Aperçu trop volumineux : réduis le débit")
                mime = "audio/mp4" if audio_only else "video/mp4"
                with open(out, "rb") as f: data = base64.b64encode(f.read()).decode("ascii")
                result = {"ok": True, "url": f"data:{mime};base64,{data}", "size": human(size), "duration": dur}
                if not audio_only:
                    for key, source, seek in (("before", path, at), ("after", out, 0)):
                        jpg = os.path.join(td, key + ".jpg")
                        rc, err = self._run_ff([ff, "-loglevel", "error", "-y", "-ss", str(seek), "-i", source, "-frames:v", "1", "-vf", "scale=800:-2", jpg], timeout=30)
                        if rc == 0 and os.path.isfile(jpg):
                            with open(jpg, "rb") as f: result[key] = "data:image/jpeg;base64," + base64.b64encode(f.read()).decode("ascii")
                return result
        except Exception as e: return {"ok": False, "err": str(e)}
        finally: self._running = False

    def _tool_one(self, ff, tool, path, p, opts, enc):
        if tool in ("smart_compress", "unusual_mp4"):
            return self._smart_export(ff, tool, path, p, opts)
        name = os.path.basename(path)
        ext = os.path.splitext(path)[1].lower()
        kind = ext_kind(ext)
        g = lambda k, d=None: p.get(k, d)

        if tool == "extract_audio":
            fmt = g("format", "mp3")
            spec = AUDIO_MAP.get(fmt)
            if not spec: return False, f"{name} (format {fmt} inconnu)"
            codec, extra, muxer = spec
            aa = audio_codec_args(codec, g("bitrate", 320), enc) if codec else []
            if aa is None: return False, f"{name} (encodeur absent)"
            out = self._tool_out(path, fmt, opts, "_audio")
            cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-vn"] + aa + extra
            if muxer: cmd += ["-f", muxer]
            cmd += [out]

        elif tool == "trim":
            ss, to = str(g("start", "0")), str(g("end", ""))
            out = self._tool_out(path, ext.lstrip("."), opts, "_trim")
            cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]
            if ss and ss != "0": cmd += ["-ss", ss]
            cmd += ["-i", path]
            if to: cmd += ["-to", str(float(to) - (float(ss) if ss.replace('.','',1).isdigit() else 0)) if False else to]
            cmd += (["-c", "copy"] if g("lossless", True) else []) + ["-map_metadata", "0", out]

        elif tool == "speed":
            f = float(g("factor", 1.5))
            out = self._tool_out(path, ext.lstrip("."), opts, f"_x{f:g}")
            # atempo accepte 0.5–2.0 : on chaîne pour les valeurs extrêmes
            at, r = [], f
            while r > 2.0: at.append("atempo=2.0"); r /= 2.0
            while r < 0.5: at.append("atempo=0.5"); r /= 0.5
            at.append(f"atempo={r:.4f}")
            if kind == "video":
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path,
                       "-filter_complex", f"[0:v]setpts=PTS/{f}[v];[0:a]{','.join(at)}[a]",
                       "-map", "[v]", "-map", "[a]", out]
            else:
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-af", ",".join(at), out]

        elif tool == "gif":
            w = int(g("width", 480)); fps = int(g("fps", 15))
            out = self._tool_out(path, "gif", opts, "")
            ss = str(g("start", "0")); dur = str(g("duration", "5"))
            cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", ss, "-t", dur, "-i", path,
                   "-filter_complex",
                   f"fps={fps},scale={w}:-2:flags=lanczos,split[a][b];[a]palettegen=stats_mode=diff[p];[b][p]paletteuse=dither=bayer:bayer_scale=5",
                   "-loop", "0", out]

        elif tool == "thumbnail":
            t = str(g("time", "1"))
            fmt = g("format", "jpg")
            out = self._tool_out(path, fmt, opts, "_thumb")
            ia = image_args(fmt, 92, enc)
            cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", t, "-i", path,
                   "-frames:v", "1"] + ia + [out]

        elif tool == "frames":
            fps = g("fps", "1")
            fmt = g("format", "png")
            d = os.path.join(os.path.dirname(path), os.path.splitext(name)[0] + "_frames")
            os.makedirs(d, exist_ok=True)
            out = os.path.join(d, "frame_%05d." + fmt)
            cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-vf", f"fps={fps}"] + image_args(fmt, 95, enc)[:-2 if "-frames:v" in image_args(fmt, 95, enc) else None] + [out]

        elif tool in ("strip_audio", "mute"):
            out = self._tool_out(path, ext.lstrip("."), opts, "_mute")
            cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-c", "copy", "-an", out]

        elif tool == "volume":
            db = float(g("db", 6))
            out = self._tool_out(path, ext.lstrip("."), opts, f"_vol{db:+g}dB")
            if kind == "video":
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-c:v", "copy", "-af", f"volume={db}dB", out]
            else:
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-af", f"volume={db}dB", out]

        elif tool == "normalize":
            out = self._tool_out(path, ext.lstrip("."), opts, "_norm")
            lufs = g("lufs", -16)
            af = f"loudnorm=I={lufs}:TP=-1.5:LRA=11"
            if kind == "video":
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-c:v", "copy", "-af", af, out]
            else:
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-af", af, out]

        elif tool == "rotate":
            a = g("angle", "90")
            vf = {"90": "transpose=1", "180": "transpose=1,transpose=1", "270": "transpose=2",
                  "hflip": "hflip", "vflip": "vflip"}.get(str(a), "transpose=1")
            out = self._tool_out(path, ext.lstrip("."), opts, "_rot")
            if kind == "image":
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-vf", vf, out]
            else:
                va, _ = video_codec_args("h264", 85, 4, False, enc)
                cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", path, "-vf", vf] + (va or []) + ["-c:a", "copy", out]

        elif tool == "compress_target":
            mb = float(g("size_mb", 25))
            fp = self.cfg.get("ffprobe", "")
            d = probe(fp, path) if fp else {}
            dur = float(d.get("format", {}).get("duration", 0) or 0)
            if dur <= 0: return False, f"{name} (durée inconnue)"
            abr = 128
            vbr = max(50, int((mb * 8192) / dur - abr))     # kbit/s
            out = self._tool_out(path, "mp4", opts, f"_{int(mb)}MB")
            base_cmd = [ff, "-y", "-hide_banner", "-loglevel", "error"]
            tmpdir = tempfile.mkdtemp(prefix="mtk_")
            passlog = os.path.join(tmpdir, "pl")
            c1 = base_cmd + ["-i", path, "-c:v", "libx264", "-b:v", f"{vbr}k", "-pass", "1", "-passlogfile", passlog,
                             "-an", "-f", "mp4", os.devnull]
            rc, err = self._run_ff(c1)
            if rc != 0: shutil.rmtree(tmpdir, True); return False, f"{name} — passe 1 : {err[-120:]}"
            cmd = base_cmd + ["-i", path, "-c:v", "libx264", "-b:v", f"{vbr}k", "-pass", "2", "-passlogfile", passlog,
                              "-c:a", "aac", "-b:a", f"{abr}k", "-movflags", "+faststart", out]
            rc, err = self._run_ff(cmd)
            shutil.rmtree(tmpdir, True)
            if rc != 0: return False, f"{name} — {err[-120:]}"
            return True, f"{os.path.basename(out)}  ({human(os.path.getsize(out))})"

        else:
            return False, f"outil inconnu : {tool}"

        rc, err = self._run_ff(cmd)
        if rc != 0:
            lines = [l for l in err.strip().splitlines() if l.strip()]
            return False, f"{name} — {lines[-1][:150] if lines else 'erreur'}"
        try:
            return True, f"{os.path.basename(out)}  ({human(os.path.getsize(out))})"
        except Exception:
            return True, os.path.basename(out)

    def _tool_merge(self, ff, files, p, opts, enc, done_one):
        if len(files) < 2:
            done_one(False, "Fusion : sélectionne au moins 2 fichiers"); return
        first = files[0]
        ext = os.path.splitext(first)[1].lstrip(".").lower()
        out = self._tool_out(first, ext, opts, "_merged")
        lst = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        for f in files:
            lst.write("file '" + f.replace("'", "'\\''") + "'\n")
        lst.close()
        cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", lst.name,
               "-c", "copy", out]
        rc, err = self._run_ff(cmd)
        os.unlink(lst.name)
        if rc != 0:
            done_one(False, f"Fusion impossible (les fichiers doivent avoir les mêmes codecs) — {err[-120:]}")
        else:
            done_one(True, f"{os.path.basename(out)}  ({human(os.path.getsize(out))})")


# ═══════════════════════════════════════════════════════════════════════
#  INTERFACE  (HTML / CSS / JS embarqué)
# ═══════════════════════════════════════════════════════════════════════
HTML = r"""<!DOCTYPE html>
<html lang="fr"><head><meta charset="utf-8"><title>Media Toolkit</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root{
  --bg:#0e0f13;--bg2:#14161c;--card:#1a1d25;--card2:#20242e;--line:#2a2f3b;--line2:#353b4a;
  --fg:#eef0f6;--fg2:#9aa1b2;--fg3:#646b7c;
  --acc:#7c6cff;--acc2:#9a8dff;--accbg:rgba(124,108,255,.14);
  --ok:#3ddc84;--warn:#ffc857;--err:#ff5d6c;--info:#5cb8ff;
  --r:12px;--r2:8px;
}
*{box-sizing:border-box;margin:0;padding:0}
html,body{height:100%;background:var(--bg);color:var(--fg);
  font:14px/1.45 "Segoe UI",system-ui,-apple-system,Roboto,sans-serif;overflow:hidden;user-select:none}
input,select,textarea,.selectable{user-select:text}
button{font:inherit;color:inherit;cursor:pointer;border:0;background:none}
::-webkit-scrollbar{width:9px;height:9px}
::-webkit-scrollbar-thumb{background:var(--line2);border-radius:9px;border:2px solid var(--bg)}
::-webkit-scrollbar-thumb:hover{background:var(--fg3)}
::-webkit-scrollbar-track{background:transparent}

/* ── layout ── */
#app{display:flex;height:100vh}
#side{width:216px;background:var(--bg2);border-right:1px solid var(--line);display:flex;flex-direction:column;flex-shrink:0}
.brand{padding:20px 18px 14px;display:flex;align-items:center;gap:10px}
.logo{width:32px;height:32px;border-radius:9px;background:linear-gradient(135deg,var(--acc),#c66cff);
  display:grid;place-items:center;font-weight:800;font-size:16px;box-shadow:0 4px 14px rgba(124,108,255,.4)}
.brand b{font-size:15px;letter-spacing:.2px}.brand small{display:block;color:var(--fg3);font-size:11px;margin-top:-2px}
nav{padding:6px 10px;display:flex;flex-direction:column;gap:2px;flex:1;overflow:auto}
nav .grp{font-size:10.5px;text-transform:uppercase;letter-spacing:1.2px;color:var(--fg3);padding:14px 10px 6px}
nav button{display:flex;align-items:center;gap:11px;padding:9px 12px;border-radius:9px;text-align:left;color:var(--fg2);
  transition:.12s;font-weight:500}
nav button:hover{background:var(--card);color:var(--fg)}
nav button.on{background:var(--accbg);color:var(--acc2)}
nav button .ic{width:18px;text-align:center;font-size:15px}
.side-foot{padding:12px 14px;border-top:1px solid var(--line);font-size:11.5px;color:var(--fg3);display:flex;flex-direction:column;gap:6px}
.pill{display:inline-flex;align-items:center;gap:6px}
.dot{width:8px;height:8px;border-radius:50%;background:var(--fg3);display:inline-block}
.dot.ok{background:var(--ok);box-shadow:0 0 8px var(--ok)}.dot.err{background:var(--err)}.dot.warn{background:var(--warn)}

main{flex:1;overflow:auto;min-width:0}
.page{display:none;padding:26px 30px 40px;max-width:1240px;margin:0 auto}
.page.on{display:block;animation:fade .18s}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
h1{font-size:23px;font-weight:700;letter-spacing:-.3px}
.sub{color:var(--fg2);margin:4px 0 22px}
h3{font-size:11.5px;text-transform:uppercase;letter-spacing:1.1px;color:var(--fg3);font-weight:700;margin-bottom:12px}

/* ── cartes / champs ── */
.card{background:var(--card);border:1px solid var(--line);border-radius:var(--r);padding:16px 18px;margin-bottom:16px}
.grid{display:grid;gap:16px}.g2{grid-template-columns:1fr 1fr}.g3{grid-template-columns:repeat(3,1fr)}
.split{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,1.15fr);gap:18px;align-items:start}
@media(max-width:1000px){.split,.g2,.g3{grid-template-columns:1fr}}
.row{display:flex;align-items:center;gap:10px;margin:8px 0;flex-wrap:wrap}
.row>label{width:118px;color:var(--fg2);font-size:13px;flex-shrink:0}
.row.tight{margin:5px 0}
.grow{flex:1;min-width:0}
input[type=text],input[type=number],select,textarea{
  background:var(--bg2);border:1px solid var(--line);border-radius:var(--r2);color:var(--fg);
  padding:8px 11px;font:inherit;outline:none;transition:.12s;min-width:0}
input:focus,select:focus,textarea:focus{border-color:var(--acc);box-shadow:0 0 0 3px var(--accbg)}
select{cursor:pointer;padding-right:26px}
select option{background:var(--card);color:var(--fg)}
input[type=number]{width:96px}
.mono{font-family:Consolas,"Cascadia Mono",monospace;font-size:12.5px}
.btn{padding:8px 15px;border-radius:var(--r2);background:var(--card2);border:1px solid var(--line2);color:var(--fg);
  font-weight:500;transition:.12s;display:inline-flex;align-items:center;gap:7px;white-space:nowrap}
.btn:hover{background:var(--line);border-color:var(--fg3)}
.btn.pri{background:linear-gradient(135deg,var(--acc),#9a6cff);border-color:transparent;color:#fff;font-weight:600;
  box-shadow:0 4px 16px rgba(124,108,255,.35)}
.btn.pri:hover{filter:brightness(1.1);transform:translateY(-1px)}
.btn.dan{background:rgba(255,93,108,.12);border-color:rgba(255,93,108,.4);color:var(--err)}
.btn.dan:hover{background:rgba(255,93,108,.22)}
.btn.sm{padding:5px 10px;font-size:12.5px}
.btn:disabled{opacity:.45;pointer-events:none}
.chips{display:flex;flex-wrap:wrap;gap:6px}
.chip{padding:4px 11px;border-radius:99px;background:var(--bg2);border:1px solid var(--line);color:var(--fg2);font-size:12.5px;transition:.1s}
.chip:hover{border-color:var(--acc);color:var(--fg)}
.chip.on{background:var(--accbg);border-color:var(--acc);color:var(--acc2)}
.hint{color:var(--fg3);font-size:12px}
.tag{display:inline-block;padding:1px 8px;border-radius:99px;font-size:11px;font-weight:600;background:var(--card2);color:var(--fg2)}
.tag.v{background:rgba(92,184,255,.14);color:var(--info)}.tag.i{background:rgba(61,220,132,.14);color:var(--ok)}.tag.a{background:rgba(255,200,87,.14);color:var(--warn)}

/* ── switch ── */
.sw{position:relative;width:38px;height:22px;flex-shrink:0}
.sw input{display:none}.sw span{position:absolute;inset:0;background:var(--line2);border-radius:99px;transition:.15s;cursor:pointer}
.sw span:before{content:"";position:absolute;left:3px;top:3px;width:16px;height:16px;border-radius:50%;background:#fff;transition:.15s}
.sw input:checked+span{background:var(--acc)}.sw input:checked+span:before{transform:translateX(16px)}
.swrow{display:flex;align-items:center;gap:10px;margin:8px 0;color:var(--fg2)}
.swrow b{color:var(--fg);font-weight:500}

/* ── sliders ── */
.sl{display:grid;grid-template-columns:96px 1fr 56px 24px;align-items:center;gap:10px;margin:7px 0}
.sl label{color:var(--fg2);font-size:13px}
.sl output{text-align:right;font-variant-numeric:tabular-nums;color:var(--acc2);font-weight:600;font-size:13px}
.sl .rs{color:var(--fg3);cursor:pointer;font-size:14px;text-align:center}.sl .rs:hover{color:var(--fg)}
input[type=range]{-webkit-appearance:none;appearance:none;height:5px;border-radius:9px;background:var(--line2);outline:none;width:100%;cursor:pointer}
input[type=range]::-webkit-slider-thumb{-webkit-appearance:none;width:16px;height:16px;border-radius:50%;background:#fff;border:3px solid var(--acc);cursor:pointer;box-shadow:0 2px 6px rgba(0,0,0,.4)}
input[type=range]:hover::-webkit-slider-thumb{transform:scale(1.15)}

/* ── zone dépôt ── */
.drop{border:2px dashed var(--line2);border-radius:var(--r);padding:20px;text-align:center;color:var(--fg2);transition:.15s;background:var(--bg2)}
.drop.hot{border-color:var(--acc);background:var(--accbg);color:var(--fg)}
.drop b{color:var(--fg)}
.srcbar{display:flex;gap:8px;flex-wrap:wrap;margin-top:12px}
.flist{max-height:170px;overflow:auto;margin-top:12px;border:1px solid var(--line);border-radius:var(--r2);background:var(--bg2)}
.fitem{display:flex;align-items:center;gap:9px;padding:6px 10px;border-bottom:1px solid var(--line);font-size:12.5px;cursor:pointer}
.fitem:last-child{border:0}.fitem:hover{background:var(--card)}.fitem.sel{background:var(--accbg)}
.fitem .n{flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}.fitem .s{color:var(--fg3);font-size:11.5px}
.stat{display:flex;gap:14px;margin-top:10px;font-size:12.5px;color:var(--fg2);flex-wrap:wrap}.stat b{color:var(--fg)}

/* ── aperçu avant/après ── */
.pv{position:sticky;top:0}
.stage{position:relative;background:
  conic-gradient(#20242e 25%,#191c24 0 50%,#20242e 0 75%,#191c24 0) 0 0/20px 20px;
  border:1px solid var(--line);border-radius:var(--r);overflow:hidden;min-height:260px;aspect-ratio:16/10;
  display:grid;place-items:center}
.stage img{position:absolute;inset:0;width:100%;height:100%;object-fit:contain;pointer-events:none}
#pvAfterWrap{position:absolute;inset:0;overflow:hidden}
.divider{position:absolute;top:0;bottom:0;width:2px;background:#fff;box-shadow:0 0 12px rgba(0,0,0,.6);cursor:ew-resize;z-index:5}
.divider:after{content:"⇆";position:absolute;top:50%;left:50%;transform:translate(-50%,-50%);width:32px;height:32px;
  border-radius:50%;background:#fff;color:#111;display:grid;place-items:center;font-size:14px;font-weight:700;box-shadow:0 2px 10px rgba(0,0,0,.5)}
.lab{position:absolute;top:10px;padding:3px 10px;border-radius:99px;background:rgba(0,0,0,.6);font-size:11.5px;font-weight:600;z-index:4;backdrop-filter:blur(6px)}
.lab.l{left:10px}.lab.r{right:10px;background:rgba(124,108,255,.85)}
.stage .empty{color:var(--fg3);text-align:center;padding:20px;z-index:1}
.stage .spin{position:absolute;top:10px;left:50%;transform:translateX(-50%);z-index:6;background:rgba(0,0,0,.7);padding:4px 12px;border-radius:99px;font-size:11.5px;display:none}
.vmodes{display:flex;gap:6px;margin-bottom:10px}
.dims{display:flex;gap:12px;align-items:center;justify-content:center;margin-top:10px;font-size:13px;flex-wrap:wrap}
.dims .a{color:var(--fg2)}.dims .b{color:var(--acc2);font-weight:700}.dims .arrow{color:var(--fg3)}
.dims .up{color:var(--ok)}.dims .down{color:var(--warn)}

/* ── progression / log ── */
.prog{height:8px;background:var(--line);border-radius:99px;overflow:hidden;margin:10px 0}
.prog i{display:block;height:100%;width:0;background:linear-gradient(90deg,var(--acc),#c66cff);border-radius:99px;transition:width .2s}
.plabel{display:flex;justify-content:space-between;font-size:12.5px;color:var(--fg2)}
.log{background:#0a0b0e;border:1px solid var(--line);border-radius:var(--r2);padding:10px 12px;height:210px;overflow:auto;
  font:12px/1.55 Consolas,"Cascadia Mono",monospace;user-select:text}
.log div.ok{color:var(--ok)}.log div.err{color:var(--err)}.log div.dim{color:var(--fg3)}.log div.info{color:var(--info)}
.actions{display:flex;gap:10px;align-items:center;margin-top:14px;flex-wrap:wrap}
.actions .sp{flex:1}
.callout{background:var(--accbg);border:1px solid rgba(124,108,255,.35);color:var(--acc2);padding:10px 14px;border-radius:var(--r2);font-size:13px;margin-bottom:14px}
.callout.warn{background:rgba(255,200,87,.1);border-color:rgba(255,200,87,.35);color:var(--warn)}
.callout.err{background:rgba(255,93,108,.1);border-color:rgba(255,93,108,.35);color:var(--err)}
details{margin:8px 0}summary{cursor:pointer;color:var(--fg2);font-size:13px;padding:6px 0;font-weight:500}
summary:hover{color:var(--fg)}
.tabs{display:flex;gap:4px;border-bottom:1px solid var(--line);margin-bottom:14px}
.tabs button{padding:8px 14px;color:var(--fg2);border-bottom:2px solid transparent;font-weight:500}
.tabs button.on{color:var(--acc2);border-color:var(--acc)}
.kv{display:grid;grid-template-columns:auto 1fr;gap:4px 14px;font-size:12.5px}.kv span:nth-child(odd){color:var(--fg3)}
.tools{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.tool{background:var(--bg2);border:1px solid var(--line);border-radius:var(--r);padding:14px;text-align:left;transition:.12s}
.tool:hover{border-color:var(--acc);transform:translateY(-2px)}.tool.on{border-color:var(--acc);background:var(--accbg)}
.tool .ic{font-size:22px}.tool b{display:block;margin-top:6px;font-size:13.5px}.tool small{color:var(--fg3);font-size:11.5px}
.modal{position:fixed;inset:0;background:rgba(0,0,0,.65);display:none;place-items:center;z-index:99;backdrop-filter:blur(3px)}
.modal.on{display:grid}.modal .box{background:var(--card);border:1px solid var(--line2);border-radius:14px;padding:22px;max-width:520px;width:90%}
</style></head>
<body>
<div id="app">
<aside id="side">
  <div class="brand"><div class="logo">M</div><div><b>Media Toolkit</b><small>v3 · pywebview</small></div></div>
  <nav>
    <div class="grp">Traiter</div>
    <button data-p="convert" class="on"><span class="ic">⇄</span>Convertir</button>
    <button data-p="compress"><span class="ic">📦</span>SmartCompress</button>
    <button data-p="color"><span class="ic">◐</span>Couleurs</button>
    <button data-p="resize"><span class="ic">⤢</span>Redimensionner</button>
    <button data-p="combo"><span class="ic">✦</span>Tout-en-un</button>
    <div class="grp">Avancé</div>
    <button data-p="tools"><span class="ic">🛠</span>Outils</button>
    <button data-p="info"><span class="ic">ⓘ</span>Analyser</button>
    <div class="grp">Système</div>
    <button data-p="settings"><span class="ic">⚙</span>Paramètres</button>
  </nav>
  <div class="side-foot">
    <span class="pill"><i class="dot" id="dFF"></i><span id="tFF">FFmpeg…</span></span>
    <span class="pill"><i class="dot" id="dGPU"></i><span id="tGPU">GPU…</span></span>
  </div>
</aside>

<main>
<section class="page" id="p-compress">
<h1>SmartCompress</h1><p class="hint">Réduis le poids des vidéos et fichiers audio pour les sauvegarder. Chaque export garde l’original.</p>
<div data-src></div><div class="card"><h3>Compression</h3>
<div class="row"><label>Codec vidéo</label><select id="scCodec"><option value="h264">H.264 — compatible</option><option value="h265">H.265 — efficace, plus lent</option></select></div>
<div class="row"><label>Débit vidéo (kbit/s)</label><input id="scVideo" type="number" min="50" max="100000" value="1000"></div>
<div class="row"><label>Débit audio (kbit/s)</label><input id="scAudio" type="number" min="16" max="320" value="96"></div>
<div class="row"><label>Hauteur maximale</label><select id="scHeight"><option value="0">Résolution originale</option><option>360</option><option>480</option><option selected>720</option><option>1080</option></select></div>
<div class="row"><label>Encodage</label><select id="scPreset"><option value="fast">Rapide</option><option value="medium" selected>Équilibré</option><option value="slow">Lent — meilleure efficacité</option></select></div>
<label><input type="checkbox" id="scSmaller" checked> Sauvegarder uniquement si le résultat est plus petit</label>
<p class="hint">Un débit plus bas réduit le poids et la qualité. Audio AAC stéréo ; première piste vidéo et première piste audio. La taille finale dépend du contenu.</p>
<div id="scEstimate" class="hint"></div></div>
<div class="card"><h3>Aperçu réel avant de sauvegarder</h3>
<div class="row"><label>Début (secondes)</label><input id="scAt" type="number" min="0" value="0"><button class="btn" id="scPreview">Tester 5 secondes</button><button class="btn" id="scCancel" disabled>Annuler l’aperçu</button></div>
<p id="scStatus" class="hint">Sélectionne un fichier dans la liste, puis teste tes réglages.</p>
<video id="scPlayer" controls style="display:none;width:100%;max-height:340px"></video>
<div style="display:flex;gap:12px"><figure style="margin:0;width:50%"><figcaption>Original</figcaption><img id="scBefore" style="width:100%"></figure><figure style="margin:0;width:50%"><figcaption>Compressé</figcaption><img id="scAfter" style="width:100%"></figure></div>
<p class="hint">La lecture H.265 dépend des codecs Windows. Les images avant/après montrent aussi le résultat. Les exports portent le suffixe _compressed, dans le dossier source ou le sous-dossier choisi dans Paramètres.</p></div>
<div data-run></div></section>
<!-- ══════════ SOURCE PARTAGÉE ══════════ -->
<template id="srcTpl">
  <div class="card">
    <h3>Source</h3>
    <div class="drop"><b>Glisse des fichiers ou dossiers ici</b><div class="hint">ou utilise les boutons ci-dessous</div></div>
    <div class="srcbar">
      <button class="btn sm" data-a="files">📄 Fichiers…</button>
      <button class="btn sm" data-a="folder">📁 Dossier…</button>
      <button class="btn sm" data-a="clear">✕ Vider</button>
      <label class="swrow" style="margin:0 0 0 auto"><span class="sw"><input type="checkbox" data-a="rec" checked><span></span></span><b>Sous-dossiers</b></label>
    </div>
    <div class="stat" data-r="stat"></div>
    <div class="flist" data-r="list" style="display:none"></div>
  </div>
</template>

<template id="runTpl">
  <div class="card">
    <h3>Progression</h3>
    <div class="plabel"><span data-r="plab">En attente</span><span data-r="pct">0 %</span></div>
    <div class="prog"><i data-r="bar"></i></div>
    <div class="log" data-r="log"></div>
    <div class="actions">
      <button class="btn pri" data-a="go">▶ Lancer</button>
      <button class="btn dan" data-a="stop">■ Arrêter</button>
      <span class="sp"></span>
      <button class="btn sm" data-a="clrlog">Effacer le journal</button>
    </div>
  </div>
</template>

<!-- ══════════ CONVERTIR ══════════ -->
<section class="page on" id="p-convert">
  <h1>Convertir</h1><div class="sub">Plus de 100 formats vidéo, image et audio. Choisis un format cible par type de média.</div>
  <div class="split"><div>
    <div data-src></div>
    <div class="card"><h3>Formats cibles</h3>
      <div class="row"><label>Vidéo →</label><select id="cvV" class="grow"></select></div>
      <div class="row"><label>Image →</label><select id="cvI" class="grow"></select></div>
      <div class="row"><label>Audio →</label><select id="cvA" class="grow"></select></div>
      <div class="hint">« Ne pas convertir » laisse ce type de média intact.</div>
    </div>
    <div class="card"><h3>Qualité</h3>
      <div class="sl"><label>Qualité</label><input type="range" id="cvQ" min="0" max="100" value="90"><output id="cvQo">90</output><span></span></div>
      <div class="hint" id="cvQh"></div>
      <details><summary>Options vidéo / audio avancées</summary>
        <div class="row"><label>Codec vidéo</label><select id="cvVC" class="grow"></select></div>
        <div class="row"><label>Codec audio</label><select id="cvAC" class="grow"></select></div>
        <div class="row"><label>Débit audio</label><select id="cvAB"><option>96</option><option>128</option><option>192</option><option selected>256</option><option>320</option><option>512</option></select><span class="hint">kbit/s</span></div>
        <div class="sl"><label>Vitesse encodage</label><input type="range" id="cvSP" min="0" max="8" value="4"><output id="cvSPo">4</output><span></span></div>
        <div class="hint">0 = très rapide (gros fichier) · 8 = très lent (fichier compact)</div>
      </details>
    </div>
    <div data-run></div>
  </div>
  <div><div class="card"><h3>Ce qui sera fait</h3><div id="cvSummary" class="selectable"></div></div>
    <div class="card"><h3>Formats reconnus en entrée</h3><div class="hint" id="cvSrcInfo"></div></div></div></div>
</section>

<!-- ══════════ COULEURS ══════════ -->
<section class="page" id="p-color">
  <h1>Couleurs</h1><div class="sub">Règle avec un aperçu en direct, puis applique à tout un dossier en un clic.</div>
  <div class="split"><div>
    <div data-src></div>
    <div class="card"><h3>Réglages</h3>
      <div id="colSliders"></div>
      <div class="chips" style="margin:12px 0 6px" id="colPresets"></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="colGray"><span></span></span><b>Noir & blanc</b>
        <span class="sw" style="margin-left:16px"><input type="checkbox" id="colSepia"><span></span></span><b>Sépia</b>
        <span class="sw" style="margin-left:16px"><input type="checkbox" id="colInv"><span></span></span><b>Négatif</b></div>
      <button class="btn sm" id="colReset">↺ Tout réinitialiser</button>
    </div>
    <div class="card"><h3>Sortie</h3>
      <div class="row"><label>Format image</label><select id="colFmt" class="grow"></select></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="colVid"><span></span></span><b>Appliquer aussi aux vidéos</b> <span class="hint">(ré-encodage)</span></div>
    </div>
    <div data-run></div>
  </div>
  <div class="pv">
    <div class="card"><h3>Aperçu avant / après</h3>
      <div class="vmodes"><button class="chip on" data-vm="split">Séparé</button><button class="chip" data-vm="after">Après</button><button class="chip" data-vm="before">Avant</button></div>
      <div class="stage" id="colStage"><div class="empty">Sélectionne un fichier dans la liste<br>pour voir l'aperçu</div><div class="spin">Calcul…</div></div>
      <div class="dims" id="colDims"></div>
      <div class="hint" style="text-align:center;margin-top:8px">L'aperçu utilise les <b>mêmes filtres FFmpeg</b> que le rendu final.</div>
    </div>
  </div></div>
</section>

<!-- ══════════ REDIMENSIONNER ══════════ -->
<section class="page" id="p-resize">
  <h1>Redimensionner</h1><div class="sub">Agrandis ou réduis, de minuscule à géant. Aperçu de la taille finale en direct.</div>
  <div class="split"><div>
    <div data-src></div>
    <div class="card"><h3>Mode</h3>
      <div class="chips" id="rzModes"></div>
      <div id="rzInputs" style="margin-top:14px"></div>
      <div class="row"><label>Autoriser</label><div class="chips" id="rzAllow"></div></div>
      <div class="hint" id="rzAllowH"></div>
    </div>
    <div class="card"><h3>Qualité de mise à l'échelle</h3>
      <div class="row"><label>Algorithme</label><select id="rzAlgo" class="grow">
        <option value="lanczos">Lanczos — le plus net (recommandé)</option>
        <option value="bicubic">Bicubique — équilibré</option>
        <option value="bilinear">Bilinéaire — doux</option>
        <option value="neighbor">Plus proche voisin — pixel art</option>
        <option value="spline">Spline — très lisse</option>
        <option value="area">Area — idéal pour réduire</option></select></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="rzEven"><span></span></span><b>Dimensions paires</b> <span class="hint">(requis pour certaines vidéos)</span></div>
      <div class="row"><label>Format sortie</label><select id="rzFmt" class="grow"></select></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="rzVid"><span></span></span><b>Appliquer aussi aux vidéos</b></div>
    </div>
    <div data-run></div>
  </div>
  <div class="pv">
    <div class="card"><h3>Aperçu</h3>
      <div class="vmodes"><button class="chip on" data-vm="split">Séparé</button><button class="chip" data-vm="after">Après</button><button class="chip" data-vm="before">Avant</button></div>
      <div class="stage" id="rzStage"><div class="empty">Sélectionne un fichier dans la liste<br>pour voir l'aperçu</div><div class="spin">Calcul…</div></div>
      <div class="dims" id="rzDims"></div>
      <div class="card" style="margin:14px 0 0;background:var(--bg2)"><h3>Présélections rapides</h3><div class="chips" id="rzPresets"></div></div>
    </div>
  </div></div>
</section>

<!-- ══════════ TOUT-EN-UN ══════════ -->
<section class="page" id="p-combo">
  <h1>Tout-en-un</h1><div class="sub">Convertir + redimensionner + couleurs en une seule passe (une seule ré-encodage = pas de perte cumulée).</div>
  <div class="split"><div>
    <div data-src></div>
    <div class="card"><h3>Étapes à inclure</h3>
      <div class="swrow"><span class="sw"><input type="checkbox" id="cbRz" checked><span></span></span><b>Redimensionner</b> <span class="hint">(réglages de l'onglet Redimensionner)</span></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="cbCol" checked><span></span></span><b>Couleurs</b> <span class="hint">(réglages de l'onglet Couleurs)</span></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="cbCv" checked><span></span></span><b>Convertir</b> <span class="hint">(formats de l'onglet Convertir)</span></div>
      <div class="callout" style="margin-top:12px">Règle chaque étape dans son onglet, puis reviens ici pour tout lancer d'un coup.</div>
    </div>
    <div data-run></div>
  </div>
  <div><div class="card"><h3>Récapitulatif</h3><div id="cbSummary" class="selectable"></div></div></div></div>
</section>

<!-- ══════════ OUTILS ══════════ -->
<section class="page" id="p-tools">
  <h1>Outils</h1><div class="sub">Couper, extraire, accélérer, compresser à une taille précise…</div>
  <div class="split"><div>
    <div data-src></div>
    <div class="card"><h3>Choisis un outil</h3><div class="tools" id="toolGrid"></div></div>
  </div>
  <div>
    <div class="card"><h3 id="toolTitle">Paramètres</h3><div id="toolParams" class="hint">Choisis un outil à gauche.</div></div>
    <div data-run></div>
  </div></div>
</section>

<!-- ══════════ ANALYSER ══════════ -->
<section class="page" id="p-info">
  <h1>Analyser</h1><div class="sub">Détails techniques d'un fichier : codec, résolution, débit, durée…</div>
  <div class="card"><div class="srcbar" style="margin:0 0 14px"><button class="btn" id="infoPick">📄 Choisir un fichier…</button></div>
    <div id="infoOut" class="kv selectable"><span>—</span><span>Aucun fichier</span></div></div>
</section>

<!-- ══════════ PARAMÈTRES ══════════ -->
<section class="page" id="p-settings">
  <h1>Paramètres</h1><div class="sub">Réglages partagés par tous les outils. Sauvegarde automatique.</div>
  <div class="grid g2"><div>
    <div class="card"><h3>FFmpeg</h3>
      <div class="row"><input type="text" id="stFF" class="grow mono" placeholder="Chemin de ffmpeg.exe (auto-détecté si dans le PATH)"><button class="btn sm" id="stFFpick">📂</button><button class="btn sm" id="stFFcheck">Vérifier</button></div>
      <div id="stFFout" class="kv selectable" style="margin-top:12px"></div>
      <div class="actions" style="margin-top:12px"><button class="btn sm" id="stDl">Télécharger FFmpeg</button></div>
    </div>
    <div class="card"><h3>Performance</h3>
      <div class="sl"><label>Tâches parallèles</label><input type="range" id="stThr" min="1" max="32" value="2"><output id="stThro">2</output><span></span></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="stGpu" checked><span></span></span><b>Utiliser le GPU NVIDIA</b> <span class="hint">(si détecté)</span></div>
    </div>
  </div><div>
    <div class="card"><h3>Fichiers de sortie</h3>
      <div class="row"><label>Que faire ?</label><select id="stOw" class="grow">
        <option value="replace">Remplacer l'original</option>
        <option value="suffix">Garder l'original + ajouter un suffixe</option>
        <option value="subfolder">Écrire dans un sous-dossier</option></select></div>
      <div class="row" id="stSufRow"><label>Suffixe</label><input type="text" id="stSuf" class="mono" value="_conv"></div>
      <div class="row" id="stSubRow"><label>Sous-dossier</label><input type="text" id="stSub" class="mono" value="converted"></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="stDel" checked><span></span></span><b>Supprimer l'original après conversion</b> <span class="hint">(mode « Remplacer »)</span></div>
      <div class="swrow"><span class="sw"><input type="checkbox" id="stMeta" checked><span></span></span><b>Conserver les métadonnées</b> <span class="hint">(tags, EXIF, dates)</span></div>
      <div class="callout warn">⚠ En mode « Remplacer », les originaux sont supprimés. Teste d'abord sur une copie.</div>
    </div>
    <div class="card"><h3>À propos</h3><div class="hint">Config : <span class="mono selectable" id="stCfg"></span></div></div>
  </div></div>
</section>
</main></div>

<div class="modal" id="modal"><div class="box"><h3 id="mTitle"></h3><div id="mBody" style="margin:10px 0 18px;color:var(--fg2)"></div>
<div class="actions" style="justify-content:flex-end"><button class="btn" id="mNo">Annuler</button><button class="btn pri" id="mYes">Continuer</button></div></div></div>

<script>
/* ═════════ utilitaires ═════════ */
const $=(s,r=document)=>r.querySelector(s), $$=(s,r=document)=>[...r.querySelectorAll(s)];
let API=null, INFO=null, CFG={};
const wait=()=>new Promise(r=>{ if(window.pywebview&&window.pywebview.api) r(); else window.addEventListener('pywebviewready',r) });
const debounce=(f,ms)=>{let t;return(...a)=>{clearTimeout(t);t=setTimeout(()=>f(...a),ms)}};
const human=n=>{for(const u of['o','Ko','Mo','Go','To']){if(n<1024)return(u==='o'?n.toFixed(0):n.toFixed(1))+' '+u;n/=1024}return n.toFixed(1)+' Po'};
function opt(sel,pairs,val){sel.innerHTML='';for(const[v,l]of pairs){const o=document.createElement('option');o.value=v;o.textContent=l;sel.appendChild(o)}if(val!==undefined)sel.value=val}
function confirmBox(t,b){return new Promise(res=>{$('#mTitle').textContent=t;$('#mBody').innerHTML=b;$('#modal').classList.add('on');
  $('#mYes').onclick=()=>{$('#modal').classList.remove('on');res(true)};$('#mNo').onclick=()=>{$('#modal').classList.remove('on');res(false)}})}

/* ═════════ navigation ═════════ */
$$('nav button').forEach(b=>b.onclick=()=>{$$('nav button').forEach(x=>x.classList.toggle('on',x===b));
  $$('.page').forEach(p=>p.classList.toggle('on',p.id==='p-'+b.dataset.p));
  if(b.dataset.p==='combo')updateCombo(); if(b.dataset.p==='convert')updateConvSummary();});

/* ═════════ événements Python → JS ═════════ */
let currentRun=null;
window.onPy=m=>{
  if(!currentRun)return; const d=m.data, R=currentRun;
  if(m.event==='log'){const l=$('[data-r=log]',R.root);const e=document.createElement('div');e.className=d.kind;e.textContent=d.msg;l.appendChild(e);l.scrollTop=l.scrollHeight}
  if(m.event==='progress'){const p=d.total?Math.round(d.done/d.total*100):0;$('[data-r=bar]',R.root).style.width=p+'%';$('[data-r=pct]',R.root).textContent=p+' %';
    $('[data-r=plab]',R.root).textContent=`${d.done}/${d.total}  ·  ✓ ${d.ok}  ✗ ${d.err}  — ${d.skip}`}
  if(m.event==='done'){R.busy(false);const lab=$('[data-r=plab]',R.root);
    lab.textContent=(d.stopped?'Arrêté — ':'Terminé — ')+`${d.ok} réussi(s), ${d.err} erreur(s), ${d.skip} ignoré(s)`;
    if(d.total)$('[data-r=bar]',R.root).style.width='100%'; currentRun=null}
};

/* ═════════ composant SOURCE (une instance par page) ═════════ */
class Source{
  constructor(root,onChange,kinds){this.root=root;this.paths=[];this.items=[];this.sel=null;this.onChange=onChange;this.kinds=kinds||null;
    root.appendChild($('#srcTpl').content.cloneNode(true));
    const drop=$('.drop',root); this.$=(s)=>$(s,root);
    ['dragover','dragenter'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.add('hot')}));
    ['dragleave','drop'].forEach(e=>drop.addEventListener(e,ev=>{ev.preventDefault();drop.classList.remove('hot')}));
    drop.addEventListener('drop',ev=>{const fs=[...ev.dataTransfer.files].map(f=>f.pywebviewFullPath||f.path).filter(Boolean);
      if(fs.length)this.add(fs); else this.setStat('<span style="color:var(--warn)">Le glisser-déposer n\'a pas fourni de chemin — utilise les boutons.</span>')});
    root.addEventListener('click',async e=>{const a=e.target.closest('[data-a]');if(!a)return;
      if(a.dataset.a==='files'){const f=await API.pick_files(this.kinds&&this.kinds.length===1?this.kinds[0]:'all');if(f&&f.length)this.add(f)}
      if(a.dataset.a==='folder'){const f=await API.pick_folder();if(f)this.add([f])}
      if(a.dataset.a==='clear'){this.paths=[];this.items=[];this.sel=null;this.total=0;this.render();this.onChange&&this.onChange(this)}});
    $('[data-a=rec]',root).onchange=()=>this.refresh();
  }
  get recursive(){return $('[data-a=rec]',this.root).checked}
  async add(p){this.paths=[...new Set([...this.paths,...p])];CFG.last_folder=p[0];await this.refresh()}
  async refresh(){this.setStat('Analyse…');const r=await API.scan_paths(this.paths,this.kinds,this.recursive);
    this.items=r.items;this.total=r.count;this.by=r.by_kind;this.size=r.size;
    if(this.sel&&!this.items.find(i=>i.path===this.sel))this.sel=null;
    if(!this.sel&&this.items.length)this.sel=(this.items.find(i=>i.kind==='image')||this.items[0]).path;
    this.render();this.onChange&&this.onChange(this)}
  setStat(h){$('[data-r=stat]',this.root).innerHTML=h}
  render(){const st=$('[data-r=stat]',this.root),ls=$('[data-r=list]',this.root);
    if(!this.total){st.innerHTML='<span>Aucun fichier sélectionné</span>';ls.style.display='none';return}
    st.innerHTML=`<span><b>${this.total}</b> fichier(s)</span><span class="tag v">${this.by.video} vidéo</span><span class="tag i">${this.by.image} image</span><span class="tag a">${this.by.audio} audio</span><span>${this.size}</span>`;
    ls.style.display='block';ls.innerHTML='';
    for(const it of this.items){const d=document.createElement('div');d.className='fitem'+(it.path===this.sel?' sel':'');
      d.innerHTML=`<span class="tag ${it.kind[0]}">${it.kind[0].toUpperCase()}</span><span class="n" title="${it.path}">${it.name}</span><span class="s">${human(it.size)}</span>`;
      d.onclick=()=>{this.sel=it.path;this.render();this.onChange&&this.onChange(this,true)};ls.appendChild(d)}
    if(this.total>this.items.length){const m=document.createElement('div');m.className='fitem';m.style.cursor='default';m.innerHTML=`<span class="n hint">… et ${this.total-this.items.length} autres (tous seront traités)</span>`;ls.appendChild(m)}}
  job(){return{source:this.paths.length===1?this.paths[0]:'',files:this.paths.length===1&&this.items.length&&this.total<=this.items.length&&!this.paths[0].match(/[\\/]$/)&&this._isFile?[this.paths[0]]:[],paths:this.paths,recursive:this.recursive}}
}

/* ═════════ composant RUN (barre + log + lancer/arrêter) ═════════ */
class Run{
  constructor(root,start){this.root=root;root.appendChild($('#runTpl').content.cloneNode(true));this.start=start;
    $('[data-a=go]',root).onclick=async()=>{ if(currentRun&&currentRun!==this)return; $('[data-r=log]',root).innerHTML='';$('[data-r=bar]',root).style.width='0';
      currentRun=this;this.busy(true);const ok=await this.start();if(!ok){this.busy(false);currentRun=null}};
    $('[data-a=stop]',root).onclick=()=>API.stop();
    $('[data-a=clrlog]',root).onclick=()=>$('[data-r=log]',root).innerHTML='';
    this.busy(false)}
  busy(b){$('[data-a=go]',this.root).disabled=b;$('[data-a=stop]',this.root).disabled=!b}
  log(m,k='info'){const l=$('[data-r=log]',this.root);const e=document.createElement('div');e.className=k;e.textContent=m;l.appendChild(e)}
}

/* résout la liste de fichiers réelle envoyée à Python */
async function resolveFiles(src){const r=await API.scan_paths(src.paths,src.kinds,src.recursive);return r.count}

/* ═════════ données de formats ═════════ */
const ORDER_V=['mp4','mkv','mov','webm','avi','flv','wmv','ogv','3gp','3g2','ts','mpg','m4v','mxf','gif','apng','webp','nut','swf','asf','f4v','dv','y4m'];
const ORDER_I=['jpg','png','webp','avif','tiff','bmp','gif','jxl','jp2','ico','tga','ppm','pgm','pbm','pam','qoi','sgi','xbm','dpx','exr','hdr','psd','pcx','apng','xwd','wbmp','heic'];
const ORDER_A=['mp3','flac','wav','aac','m4a','ogg','opus','aiff','wma','ac3','eac3','dts','mka','amr','au','caf','w64','wv','tta','mp2','spx','oga','m4b','m4r','voc','ircam','gsm','mp1'];
const CODEC_LBL={h264:'H.264 (AVC)',h265:'H.265 (HEVC)',av1:'AV1',vp9:'VP9',vp8:'VP8',mpeg4:'MPEG-4',mpeg2:'MPEG-2',mpeg1:'MPEG-1',mjpeg:'Motion JPEG',ffv1:'FFV1 (sans perte)',huffyuv:'HuffYUV (sans perte)',prores:'Apple ProRes',dnxhd:'DNxHD',theora:'Theora',flv1:'Flash Video',wmv2:'WMV 8',dv:'DV',gif:'GIF',apng:'APNG',webp:'WebP',raw:'Brut',aac:'AAC',mp3:'MP3',opus:'Opus',vorbis:'Vorbis',flac:'FLAC',ac3:'AC-3',pcm:'PCM',alac:'ALAC (Apple sans perte)',amr:'AMR',wma:'WMA',mp2:'MP2',copy:'Copier sans ré-encoder'};

/* ═════════ PAGE : CONVERTIR ═════════ */
let cvSrc,cvRun;
function initConvert(){
  cvSrc=new Source($('#p-convert [data-src]'),updateConvSummary);
  cvRun=new Run($('#p-convert [data-run]'),()=>startJob('convert',cvSrc));
  const t=INFO.targets;
  opt($('#cvV'),[['','— Ne pas convertir —'],...ORDER_V.filter(k=>t.video[k]).map(k=>[k,k.toUpperCase()+'  ·  '+t.video[k]])],CFG.cv_v??'mp4');
  opt($('#cvI'),[['','— Ne pas convertir —'],...ORDER_I.filter(k=>t.image[k]).map(k=>[k,k.toUpperCase()+'  ·  '+t.image[k]])],CFG.cv_i??'webp');
  opt($('#cvA'),[['','— Ne pas convertir —'],...ORDER_A.filter(k=>t.audio[k]).map(k=>[k,k.toUpperCase()+'  ·  '+t.audio[k]])],CFG.cv_a??'mp3');
  ['cvV','cvI','cvA'].forEach(id=>$('#'+id).onchange=()=>{CFG['cv_'+id.slice(2).toLowerCase()]=$('#'+id).value;API.set_cfg('cv_'+id.slice(2).toLowerCase(),$('#'+id).value);syncCodecs();updateConvSummary();updateCombo()});
  $('#cvQ').oninput=()=>{$('#cvQo').textContent=$('#cvQ').value;qHint();updateConvSummary()};
  $('#cvSP').oninput=()=>$('#cvSPo').textContent=$('#cvSP').value;
  syncCodecs();qHint();
  const total=INFO.src_counts;$('#cvSrcInfo').textContent=`${total.video} extensions vidéo, ${total.image} extensions image, ${total.audio} extensions audio reconnues en lecture. La conversion vers HEIC / SVG / PSD passe par Pillow si FFmpeg ne sait pas les lire.`;
  updateConvSummary();
}
function syncCodecs(){const v=$('#cvV').value,vc=INFO.video_codecs[v]||['h264'],ac=INFO.audio_codecs[v]||['aac'];
  opt($('#cvVC'),vc.map(k=>[k,CODEC_LBL[k]||k]));opt($('#cvAC'),(ac.length?ac:['aac']).map(k=>[k,CODEC_LBL[k]||k]))}
function qHint(){const q=+$('#cvQ').value;$('#cvQh').textContent=q>=100?'Sans perte (fichiers volumineux)':q>=90?'Excellente — quasi indiscernable de l\'original':q>=75?'Très bonne':q>=55?'Bonne — bon compromis taille/qualité':q>=30?'Moyenne — fichier léger':'Basse — très compact'}
function updateConvSummary(){const t=[];const V=$('#cvV').value,I=$('#cvI').value,A=$('#cvA').value;
  if(V)t.push(`🎞 Vidéos → <b>${V.toUpperCase()}</b> (${CODEC_LBL[$('#cvVC').value]||''})`);if(I)t.push(`🖼 Images → <b>${I.toUpperCase()}</b>`);if(A)t.push(`🎵 Audios → <b>${A.toUpperCase()}</b>`);
  const o=CFG.overwrite_mode;t.push(`<br>💾 ${o==='replace'?'<span style="color:var(--warn)">Remplace les originaux</span>':o==='suffix'?'Garde l\'original, ajoute le suffixe <b>'+(CFG.suffix||'')+'</b>':'Écrit dans <b>'+(CFG.subfolder||'')+'/</b>'}`);
  if(cvSrc&&cvSrc.total)t.push(`📦 <b>${cvSrc.total}</b> fichier(s) seront examinés`);
  $('#cvSummary').innerHTML=t.length?t.join('<br>'):'Choisis au moins un format cible.'}

/* ═════════ PAGE : COULEURS ═════════ */
const COL=[['sat','Saturation',-100,100],['bri','Luminosité',-100,100],['con','Contraste',-100,100],['gam','Gamma',-100,100],['expo','Exposition',-100,100],
  ['hue','Teinte',-180,180],['temp','Température',-100,100],['sharp','Netteté',-100,100],['denoise','Réd. bruit',0,100],['blur','Flou',0,100],['vign','Vignette',0,100],['grain','Grain',0,100]];
const COLP={'Original':{},'Vif':{sat:35,con:12},'Pastel':{sat:-25,bri:12,con:-12},'Chaud':{temp:40,sat:10},'Froid':{temp:-40,sat:5},
  'Cinéma':{con:22,sat:-12,temp:-15,vign:30},'Vintage':{sat:-30,temp:30,grain:35,vign:35,con:8},'Dramatique':{con:45,sat:-20,vign:45,bri:-8},'Doux':{con:-15,bri:8,blur:6,sat:-8},'Net':{sharp:60,con:10}};
let colV={},colSrc,colRun;
function colParams(){return{...colV,gray:$('#colGray').checked,sepia:$('#colSepia').checked,invert:$('#colInv').checked}}
function makeSliders(host,defs,store,onc){host.innerHTML='';
  for(const[k,l,mn,mx]of defs){store[k]=store[k]||0;const d=document.createElement('div');d.className='sl';
    d.innerHTML=`<label>${l}</label><input type="range" min="${mn}" max="${mx}" value="${store[k]}" data-k="${k}"><output>${store[k]}</output><span class="rs" title="Remettre à 0">↺</span>`;
    const r=$('input',d),o=$('output',d);r.oninput=()=>{store[k]=+r.value;o.textContent=(r.value>0&&mn<0?'+':'')+r.value;onc()};
    $('.rs',d).onclick=()=>{r.value=0;r.oninput()};host.appendChild(d)}}
function initColor(){
  colSrc=new Source($('#p-color [data-src]'),(s,sel)=>{updColPv()},null);
  colRun=new Run($('#p-color [data-run]'),()=>startJob('color',colSrc));
  makeSliders($('#colSliders'),COL,colV,()=>{updColPv();updateCombo()});
  const ch=$('#colPresets');for(const[n,v]of Object.entries(COLP)){const b=document.createElement('button');b.className='chip';b.textContent=n;
    b.onclick=()=>{$$('#colPresets .chip').forEach(x=>x.classList.toggle('on',x===b));for(const[k]of COL)colV[k]=v[k]||0;
      makeSliders($('#colSliders'),COL,colV,()=>{updColPv();updateCombo()});updColPv();updateCombo()};ch.appendChild(b)}
  ['colGray','colSepia','colInv'].forEach(i=>$('#'+i).onchange=()=>{updColPv();updateCombo()});
  $('#colReset').onclick=()=>{for(const[k]of COL)colV[k]=0;['colGray','colSepia','colInv'].forEach(i=>$('#'+i).checked=false);$$('#colPresets .chip').forEach(x=>x.classList.remove('on'));
    makeSliders($('#colSliders'),COL,colV,()=>{updColPv();updateCombo()});updColPv();updateCombo()};
  opt($('#colFmt'),[['','Même format que l\'original'],...ORDER_I.filter(k=>INFO.targets.image[k]).map(k=>[k,k.toUpperCase()])],'');
  bindStage('col');
}

/* ═════════ aperçu générique (couleurs + resize) ═════════ */
const PV={col:{mode:'split',pos:.5,data:null},rz:{mode:'split',pos:.5,data:null}};
function bindStage(pfx){const st=$('#'+pfx+'Stage');const page=st.closest('.page');
  $$('.vmodes .chip',page).forEach(b=>b.onclick=()=>{$$('.vmodes .chip',page).forEach(x=>x.classList.toggle('on',x===b));PV[pfx].mode=b.dataset.vm;drawStage(pfx)});
  let drag=false;const mv=e=>{if(!drag)return;const r=st.getBoundingClientRect();PV[pfx].pos=Math.max(0,Math.min(1,(e.clientX-r.left)/r.width));drawStage(pfx)};
  st.addEventListener('mousedown',e=>{if(e.target.classList.contains('divider')||PV[pfx].mode==='split'){drag=true;mv(e)}});
  window.addEventListener('mouseup',()=>drag=false);window.addEventListener('mousemove',mv)}
function drawStage(pfx){const st=$('#'+pfx+'Stage'),P=PV[pfx],d=P.data;if(!d)return;
  st.querySelectorAll('img,.divider,.lab,#pvAfterWrap,.empty').forEach(e=>e.remove());
  const mk=(src)=>{const i=document.createElement('img');i.src=src;return i};
  if(P.mode==='before'){st.append(mk(d.before));st.insertAdjacentHTML('beforeend','<span class="lab l">Avant</span>')}
  else if(P.mode==='after'){st.append(mk(d.after));st.insertAdjacentHTML('beforeend','<span class="lab r">Après</span>')}
  else{st.append(mk(d.before));const w=document.createElement('div');w.id='pvAfterWrap';w.style.clipPath=`inset(0 0 0 ${P.pos*100}%)`;w.append(mk(d.after));st.append(w);
    const dv=document.createElement('div');dv.className='divider';dv.style.left=(P.pos*100)+'%';st.append(dv);
    st.insertAdjacentHTML('beforeend','<span class="lab l">Avant</span><span class="lab r">Après</span>')}}
let pvSeq=0;
async function runPreview(pfx,src,color,resize,dimsEl){
  const st=$('#'+pfx+'Stage'),sp=$('.spin',st);
  if(!src||!src.sel){if(!PV[pfx].data)st.innerHTML='<div class="empty">Sélectionne un fichier dans la liste<br>pour voir l\'aperçu</div><div class="spin">Calcul…</div>';return}
  const my=++pvSeq;sp.style.display='block';
  const r=await API.preview(src.sel,color,resize,900,0);
  if(my!==pvSeq)return;sp.style.display='none';
  if(!r.ok){st.querySelectorAll('img,.divider,.lab,#pvAfterWrap,.empty').forEach(e=>e.remove());st.insertAdjacentHTML('afterbegin',`<div class="empty" style="color:var(--err)">Aperçu impossible<br><span class="hint">${(r.err||'').slice(0,160)}</span></div>`);return}
  PV[pfx].data=r;drawStage(pfx);
  if(dimsEl&&r.orig_w){const dw=r.final_w,dh=r.final_h,same=(dw===r.orig_w&&dh===r.orig_h);const up=dw*dh>r.orig_w*r.orig_h;
    dimsEl.innerHTML=`<span class="a">${r.orig_w}×${r.orig_h}</span><span class="arrow">→</span><span class="b">${dw}×${dh}</span>`+(same?'':`<span class="${up?'up':'down'}">${up?'▲ agrandi ×':'▼ réduit ×'}${(dw/r.orig_w).toFixed(2)}</span>`)+`<span class="hint">${((dw*dh)/1e6).toFixed(2)} Mpx</span>`}}
const updColPv=debounce(()=>runPreview('col',colSrc,colParams(),null,$('#colDims')),220);
const updRzPv=debounce(()=>runPreview('rz',rzSrc,{},rzParams(),$('#rzDims')),220);

/* ═════════ PAGE : REDIMENSIONNER ═════════ */
const RZM=[['height','Hauteur','Fixe la hauteur, la largeur suit'],['width','Largeur','Fixe la largeur, la hauteur suit'],['longest','Plus grand côté','Le côté le plus long = valeur'],['shortest','Plus petit côté','Le côté le plus court = valeur'],
  ['percent','Pourcentage','% de la taille d\'origine'],['fit','Tenir dans une boîte','Garde le ratio, tient dans L×H'],['fill','Remplir une boîte','Garde le ratio, recadre'],['exact','Taille exacte','L×H exact (déforme)'],['megapix','Mégapixels','Cible un nombre de Mpx'],['off','Aucun','Ne pas redimensionner']];
const RZP=[['16×16',16,16],['32×32',32,32],['64×64',64,64],['128',128,128],['256',256,256],['512',512,512],['480p',null,480],['720p',null,720],['1080p',null,1080],['1440p',null,1440],['4K',null,2160],['8K',null,4320],['16K',null,8640]];
let rz={mode:'height',value:1080,w:1920,h:1080,percent:100,allow:'both',algo:'lanczos',even:false},rzSrc,rzRun;
function rzParams(){return{...rz,algo:$('#rzAlgo').value,even:$('#rzEven').checked}}
function numIn(id,val,min,max,unit,step){return `<input type="number" id="${id}" min="${min}" max="${max}" step="${step||1}" value="${val}"> <span class="hint">${unit}</span>`}
function drawRzInputs(){const h=$('#rzInputs'),m=rz.mode;let s='';
  const desc=RZM.find(x=>x[0]===m)[2];
  const slider=(min,max,val,unit,log)=>`<div class="sl" style="grid-template-columns:1fr 130px 24px"><input type="range" id="rzSl" min="${min}" max="${max}" value="${log?Math.log2(val)*100:val}" step="1"><span>${numIn('rzVal',val,1,99999,unit)}</span><span></span></div>`;
  if(['height','width','longest','shortest'].includes(m))s=`<div class="row"><label>Valeur</label><div class="grow" style="display:flex;gap:10px;align-items:center"><input type="range" id="rzSl" min="0" max="1500" value="${Math.log2(Math.max(1,rz.value))*100}" style="flex:1"><span style="white-space:nowrap">${numIn('rzVal',rz.value,1,65536,'px')}</span></div></div><div class="hint">Échelle logarithmique : de 1 px à 65 536 px. Tape une valeur exacte à droite.</div>`;
  else if(m==='percent')s=`<div class="row"><label>Échelle</label><div class="grow" style="display:flex;gap:10px;align-items:center"><input type="range" id="rzSl" min="0" max="1500" value="${Math.log2(Math.max(.1,rz.percent)*10)*100/Math.log2(10000)*1.5*Math.log2(10000)/1.5/1}" style="flex:1"><span style="white-space:nowrap">${numIn('rzVal',rz.percent,.1,10000,'%',0.1)}</span></div></div><div class="chips" style="margin-top:8px">${[10,25,50,75,100,150,200,400,800].map(v=>`<button class="chip" data-pc="${v}">${v} %</button>`).join('')}</div>`;
  else if(['fit','fill','exact'].includes(m))s=`<div class="row"><label>Largeur</label>${numIn('rzW',rz.w,1,65536,'px')}<label style="width:auto">Hauteur</label>${numIn('rzH',rz.h,1,65536,'px')}<button class="btn sm" id="rzSwap">⇄</button></div>`;
  else if(m==='megapix')s=`<div class="row"><label>Mégapixels</label>${numIn('rzVal',rz.value>500?2:rz.value,.001,2000,'Mpx',0.1)}</div><div class="chips" style="margin-top:6px">${[.1,.5,1,2,4,8,12,24,50,100].map(v=>`<button class="chip" data-mp="${v}">${v} Mpx</button>`).join('')}</div>`;
  else s='<div class="hint">Aucun redimensionnement.</div>';
  h.innerHTML=`<div class="hint" style="margin-bottom:10px">${desc}</div>`+s;
  const sl=$('#rzSl'),vi=$('#rzVal');
  if(sl&&vi&&m!=='percent'){sl.oninput=()=>{const v=Math.max(1,Math.round(2**(sl.value/100)));vi.value=v;rz.value=v;chg()}}
  if(sl&&m==='percent'){sl.value=Math.log2(Math.max(.1,rz.percent)*10)/Math.log2(100000)*1500;sl.oninput=()=>{const v=Math.round((2**(sl.value/1500*Math.log2(100000))/10)*10)/10;vi.value=v;rz.percent=v;chg()}}
  if(vi)vi.oninput=()=>{const v=parseFloat(vi.value);if(!(v>0))return;if(m==='percent'){rz.percent=v;sl.value=Math.log2(v*10)/Math.log2(100000)*1500}else if(m==='megapix'){rz.value=v}else{rz.value=v;if(sl)sl.value=Math.log2(v)*100}chg()};
  $$('[data-pc]',h).forEach(b=>b.onclick=()=>{rz.percent=+b.dataset.pc;drawRzInputs();chg()});
  $$('[data-mp]',h).forEach(b=>b.onclick=()=>{rz.value=+b.dataset.mp;drawRzInputs();chg()});
  if($('#rzW')){$('#rzW').oninput=e=>{rz.w=+e.target.value;chg()};$('#rzH').oninput=e=>{rz.h=+e.target.value;chg()};$('#rzSwap').onclick=()=>{[rz.w,rz.h]=[rz.h,rz.w];drawRzInputs();chg()}}
  drawAllow()}
function drawAllow(){const a=$('#rzAllow');a.innerHTML='';for(const[v,l]of[['both','Réduire et agrandir'],['down','Réduire seulement'],['up','Agrandir seulement']]){
  const b=document.createElement('button');b.className='chip'+(rz.allow===v?' on':'');b.textContent=l;b.onclick=()=>{rz.allow=v;drawAllow();chg()};a.appendChild(b)}
  $('#rzAllowH').textContent={both:'Toutes les images sont ramenées à la taille demandée (plus petites → agrandies, plus grandes → réduites).',down:'Les images déjà plus petites que la cible ne sont pas touchées.',up:'Les images déjà plus grandes que la cible ne sont pas touchées.'}[rz.allow]}
function chg(){API.set_cfg('rz',rz);updRzPv();updateCombo()}
function initResize(){
  rzSrc=new Source($('#p-resize [data-src]'),()=>updRzPv(),null);
  rzRun=new Run($('#p-resize [data-run]'),()=>startJob('resize',rzSrc));
  if(CFG.rz)Object.assign(rz,CFG.rz);
  const ms=$('#rzModes');for(const[k,l]of RZM){const b=document.createElement('button');b.className='chip'+(rz.mode===k?' on':'');b.textContent=l;
    b.onclick=()=>{rz.mode=k;$$('#rzModes .chip').forEach(x=>x.classList.toggle('on',x===b));drawRzInputs();chg()};ms.appendChild(b)}
  const ps=$('#rzPresets');for(const[l,w,h]of RZP){const b=document.createElement('button');b.className='chip';b.textContent=l;
    b.onclick=()=>{if(w){rz.mode='exact';rz.w=w;rz.h=h;rz.allow='both'}else{rz.mode='height';rz.value=h;rz.allow='both'}
      $$('#rzModes .chip').forEach((x,i)=>x.classList.toggle('on',RZM[i][0]===rz.mode));drawRzInputs();chg()};ps.appendChild(b)}
  opt($('#rzFmt'),[['','Même format que l\'original'],...ORDER_I.filter(k=>INFO.targets.image[k]).map(k=>[k,k.toUpperCase()])],'');
  $('#rzAlgo').onchange=chg;$('#rzEven').onchange=chg;$('#rzAlgo').value=rz.algo||'lanczos';
  drawRzInputs();bindStage('rz')}

/* ═════════ PAGE : TOUT-EN-UN ═════════ */
let cbSrc,cbRun;
function initCombo(){cbSrc=new Source($('#p-combo [data-src]'),updateCombo,null);cbRun=new Run($('#p-combo [data-run]'),()=>startJob('combo',cbSrc));
  ['cbRz','cbCol','cbCv'].forEach(i=>$('#'+i).onchange=updateCombo)}
function updateCombo(){const t=[];
  if($('#cbRz').checked){const r=rzParams();t.push(r.mode==='off'?'⤢ Redimensionnement : <i>désactivé dans l\'onglet Redimensionner</i>':`⤢ Redimensionnement : <b>${RZM.find(x=>x[0]===r.mode)[1]}</b> ${['height','width','longest','shortest'].includes(r.mode)?r.value+' px':r.mode==='percent'?r.percent+' %':r.mode==='megapix'?r.value+' Mpx':r.w+'×'+r.h} (${r.allow==='both'?'↕':r.allow==='down'?'réduire':'agrandir'})`)}
  if($('#cbCol').checked){const c=colParams(),act=Object.entries(c).filter(([k,v])=>v).map(([k])=>k);t.push('◐ Couleurs : '+(act.length?'<b>'+act.join(', ')+'</b>':'<i>aucun réglage</i>'))}
  if($('#cbCv').checked)t.push(`⇄ Formats : vidéo <b>${$('#cvV').value||'—'}</b> · image <b>${$('#cvI').value||'—'}</b> · audio <b>${$('#cvA').value||'—'}</b>`);
  $('#cbSummary').innerHTML=t.join('<br><br>')||'Aucune étape sélectionnée.'}

/* ═════════ lancement d'un traitement ═════════ */
async function startJob(mode,src){
  if(!src.total){cmsg(src,'Aucun fichier sélectionné.');return false}
  const q=+$('#cvQ').value, common={source:'',files:[],recursive:src.recursive,quality:q,speed:+$('#cvSP').value,
    vcodec:$('#cvVC').value,acodec:$('#cvAC').value,abitrate:+$('#cvAB').value,opts:{}};
  // liste explicite des chemins (fichiers + dossiers)
  const all=await API.scan_paths(src.paths,src.kinds,src.recursive);
  // on envoie les chemins d'origine : Python re-scanne (rapide) — évite de plafonner à 500 éléments
  common.files=null;common.paths=src.paths;
  let job={...common,mode};
  const cv={video:$('#cvV').value,image:$('#cvI').value,audio:$('#cvA').value};
  if(mode==='convert'){job.targets=cv;job.apply_to=['video','image','audio'];
    if(!cv.video&&!cv.image&&!cv.audio){cmsg(src,'Choisis au moins un format cible.');return false}}
  if(mode==='color'){job.color=colParams();job.targets={image:$('#colFmt').value};job.apply_to=$('#colVid').checked?['image','video']:['image'];
    if(!Object.values(job.color).some(v=>v)){cmsg(src,'Aucun réglage de couleur — bouge un curseur d\'abord.');return false}}
  if(mode==='resize'){job.resize=rzParams();job.targets={image:$('#rzFmt').value};job.apply_to=$('#rzVid').checked?['image','video']:['image'];
    if(job.resize.mode==='off'){cmsg(src,'Mode « Aucun » : rien à faire.');return false}}
  if(mode==='combo'){job.apply_to=['image','video','audio'];
    job.color=$('#cbCol').checked?colParams():{};job.resize=$('#cbRz').checked?rzParams():{mode:'off'};job.targets=$('#cbCv').checked?cv:{};
    if(!$('#cbCv').checked)job.targets={};}
  if(CFG.overwrite_mode==='replace'){
    const ok=await confirmBox('Remplacer les originaux ?',`<b>${src.total}</b> fichier(s) vont être modifiés <b>et les originaux supprimés</b>.<br><br>Change ce comportement dans <i>Paramètres → Fichiers de sortie</i> pour conserver tes fichiers.`);
    if(!ok)return false}
  const r=await API.process_paths(job);
  if(!r.ok){cmsg(src,r.err);return false}return true}
function cmsg(src,m){const root=src.root.closest('.page');const l=$('[data-r=log]',root);if(l){l.innerHTML=`<div class="err">${m}</div>`}}

/* ═════════ PAGE : OUTILS ═════════ */
const TOOLS=[
 {id:'unusual_mp4',ic:'⇄',n:'Formats inhabituels → MP4',d:'TS, M3U8, M4S, MTS, M2TS : copie rapide puis H.264 si nécessaire',p:[]},
 {id:'extract_audio',ic:'🎵',n:'Extraire l\'audio',d:'Vidéo → fichier audio',p:[['sel','format','Format',['mp3','flac','wav','aac','ogg','opus','m4a'],'mp3'],['sel','bitrate','Débit (kbit/s)',[128,192,256,320],320]]},
 {id:'trim',ic:'✂',n:'Couper',d:'Garder un extrait',p:[['txt','start','Début (s ou hh:mm:ss)','0'],['txt','end','Fin (s ou hh:mm:ss)',''],['chk','lossless','Sans ré-encodage (instantané)',true]]},
 {id:'speed',ic:'⏩',n:'Vitesse',d:'Accélérer / ralentir',p:[['num','factor','Facteur (0.25 = ÷4, 2 = ×2)',1.5]]},
 {id:'gif',ic:'🎞',n:'Vidéo → GIF',d:'GIF de qualité',p:[['num','width','Largeur (px)',480],['num','fps','Images/seconde',15],['txt','start','Début (s)','0'],['num','duration','Durée (s)',5]]},
 {id:'thumbnail',ic:'📸',n:'Capture d\'image',d:'Une image de la vidéo',p:[['txt','time','À la seconde','1'],['sel','format','Format',['jpg','png','webp'],'jpg']]},
 {id:'frames',ic:'🎬',n:'Toutes les images',d:'Vidéo → séquence',p:[['txt','fps','Images par seconde','1'],['sel','format','Format',['png','jpg','webp'],'png']]},
 {id:'mute',ic:'🔇',n:'Retirer le son',d:'Vidéo sans audio',p:[]},
 {id:'volume',ic:'🔊',n:'Volume',d:'Monter / baisser en dB',p:[['num','db','Gain (dB)',6]]},
 {id:'normalize',ic:'📊',n:'Normaliser',d:'Volume constant (EBU R128)',p:[['num','lufs','Cible LUFS',-16]]},
 {id:'rotate',ic:'⟳',n:'Rotation / miroir',d:'Vidéo ou image',p:[['sel','angle','Action',['90','180','270','hflip','vflip'],'90']]},
 {id:'compress_target',ic:'📦',n:'Taille cible',d:'Compresser à X Mo (2 passes)',p:[['num','size_mb','Taille voulue (Mo)',25]]},
 {id:'merge',ic:'🔗',n:'Fusionner',d:'Concaténer (mêmes codecs)',p:[]},
];
let toolSrc,toolRun,curTool=null,toolP={};
function initTools(){toolSrc=new Source($('#p-tools [data-src]'),()=>{},null);toolRun=new Run($('#p-tools [data-run]'),startTool);
  const g=$('#toolGrid');for(const t of TOOLS){const b=document.createElement('button');b.className='tool';b.dataset.id=t.id;b.innerHTML=`<span class="ic">${t.ic}</span><b>${t.n}</b><small>${t.d}</small>`;
    b.onclick=()=>{curTool=t;toolP={};$$('.tool').forEach(x=>x.classList.toggle('on',x===b));drawToolParams(t)};g.appendChild(b)}}
function drawToolParams(t){$('#toolTitle').textContent=t.n;const h=$('#toolParams');h.className='';h.innerHTML='';
  if(!t.p.length)h.innerHTML='<div class="hint">Aucun paramètre.'+(t.id==='merge'?' Les fichiers seront assemblés dans l\'ordre de la liste.':'')+'</div>';
  if(t.id==='unusual_mp4')h.textContent='Détection des fichiers TS, M3U8, M4S, MTS et M2TS. Pour une playlist, conserve les segments et le fichier d’initialisation à leur emplacement. Un M4S isolé peut être illisible. Sortie _mp4 ; original conservé.';
  for(const[ty,k,l,a,b]of t.p){const r=document.createElement('div');r.className='row';r.innerHTML=`<label style="width:170px">${l}</label>`;let el;
    if(ty==='sel'){el=document.createElement('select');opt(el,a.map(x=>[x,x]),String(b));toolP[k]=b;el.onchange=()=>toolP[k]=el.value}
    else if(ty==='chk'){el=document.createElement('span');el.className='sw';el.innerHTML=`<input type="checkbox" ${a?'checked':''}><span></span>`;toolP[k]=a;$('input',el).onchange=e=>toolP[k]=e.target.checked}
    else{el=document.createElement('input');el.type=ty==='num'?'number':'text';el.value=a;el.step='any';toolP[k]=ty==='num'?+a:a;el.oninput=()=>toolP[k]=ty==='num'?+el.value:el.value}
    r.appendChild(el);h.appendChild(r)}}
async function startTool(){
  if(!curTool){cmsg(toolSrc,'Choisis un outil.');return false}
  const r=await API.scan_paths(toolSrc.paths,['video','audio','image'],toolSrc.recursive);if(!r.count){cmsg(toolSrc,'Aucun fichier.');return false}
  const fl=[];{const rr=await API.list_files(toolSrc.paths,['video','audio','image'],toolSrc.recursive);fl.push(...rr)}
  const files=curTool.id==='unusual_mp4'?fl.filter(f=>/\.(ts|m3u8|m4s|mts|m2ts)$/i.test(f)):fl;
  if(!files.length){toolRun.log('Aucun fichier au format concerné.','warn');return false}
  const res=await API.run_tool({tool:curTool.id,files,params:toolP,opts:curTool.id==='unusual_mp4'?{threads:1}:{}});if(!res.ok){cmsg(toolSrc,res.err);return false}return true}

/* ═════════ PAGE : ANALYSER ═════════ */
$('#infoPick').onclick=async()=>{const f=await API.pick_file('all');if(!f)return;const i=await API.media_info(f);const o=$('#infoOut');
  if(!i.ok){o.innerHTML='<span>Erreur</span><span>ffprobe introuvable (doit être à côté de ffmpeg)</span>';return}
  const rows=[['Fichier',f.split(/[\\/]/).pop()],['Taille',i.size]];
  if(i.w)rows.push(['Résolution',`${i.w} × ${i.h}  (${(i.w*i.h/1e6).toFixed(2)} Mpx)`],['Codec vidéo',i.vcodec],['Images/s',i.fps],['Pixel format',i.pix]);
  if(i.acodec)rows.push(['Codec audio',i.acodec],['Fréquence',(i.sr||'?')+' Hz'],['Canaux',i.ch]);
  if(i.duration)rows.push(['Durée',new Date(i.duration*1000).toISOString().substr(11,8)]);if(i.bitrate)rows.push(['Débit total',i.bitrate+' kbit/s']);
  o.innerHTML=rows.map(([a,b])=>`<span>${a}</span><span>${b}</span>`).join('')};

/* ═════════ PAGE : PARAMÈTRES ═════════ */
function initSettings(){
  $('#stFF').value=CFG.ffmpeg||'';$('#stCfg').textContent='mediatoolkit.json';
  const S=(id,key,ev='onchange',get=e=>e.target.value)=>{const el=$('#'+id);el[ev]=e=>{CFG[key]=get(e);API.set_cfg(key,CFG[key]);updateConvSummary();syncOw()}};
  $('#stThr').value=CFG.threads;$('#stThro').textContent=CFG.threads;$('#stThr').oninput=e=>{$('#stThro').textContent=e.target.value;CFG.threads=+e.target.value;API.set_cfg('threads',+e.target.value)};
  $('#stGpu').checked=!!CFG.use_gpu;S('stGpu','use_gpu','onchange',e=>e.target.checked);
  $('#stOw').value=CFG.overwrite_mode;S('stOw','overwrite_mode');
  $('#stSuf').value=CFG.suffix;S('stSuf','suffix','oninput');$('#stSub').value=CFG.subfolder;S('stSub','subfolder','oninput');
  $('#stDel').checked=!!CFG.delete_source;S('stDel','delete_source','onchange',e=>e.target.checked);
  $('#stMeta').checked=!!CFG.keep_metadata;S('stMeta','keep_metadata','onchange',e=>e.target.checked);
  $('#stFF').onchange=async e=>{await API.set_cfg('ffmpeg',e.target.value);checkFF()};
  $('#stFFpick').onclick=async()=>{const p=await API.pick_exe();if(p){$('#stFF').value=p;await API.set_cfg('ffmpeg',p);checkFF()}};
  $('#stFFcheck').onclick=()=>checkFF($('#stFF').value);
  $('#stDl').onclick=()=>API.open_url('https://www.gyan.dev/ffmpeg/builds/');
  syncOw()}
function syncOw(){const m=CFG.overwrite_mode;$('#stSufRow').style.display=m==='suffix'?'flex':'none';$('#stSubRow').style.display=m==='subfolder'?'flex':'none'}
async function checkFF(path){const r=await API.check_ffmpeg(path||'');const o=$('#stFFout');
  $('#dFF').className='dot '+(r.ok?'ok':'err');$('#tFF').textContent=r.ok?'FFmpeg prêt':'FFmpeg manquant';
  $('#dGPU').className='dot '+(r.ok&&r.gpu?'ok':'warn');$('#tGPU').textContent=r.ok&&r.gpu?'GPU NVIDIA actif':'Mode CPU';
  if(!r.ok){o.innerHTML='<span>État</span><span style="color:var(--err)">FFmpeg introuvable — indique son chemin ci-dessus</span>';return}
  $('#stFF').value=r.path;const good=Object.entries(r.encoders).filter(([k,v])=>v).map(([k])=>k),bad=Object.entries(r.encoders).filter(([k,v])=>!v).map(([k])=>k);
  o.innerHTML=`<span>Version</span><span>${r.version.replace('ffmpeg version ','')}</span><span>Encodeurs</span><span>${r.n_encoders} disponibles</span><span>GPU</span><span>${r.gpu?'✓ NVENC utilisable':'— non détecté'}</span>
   <span>Présents</span><span style="color:var(--ok)">${good.join(', ')||'—'}</span><span>Absents</span><span style="color:var(--fg3)">${bad.join(', ')||'aucun'}</span>`}


let scSrc,scRun,scRevision=0;
function scParams(){return {video_kbps:+$('#scVideo').value,audio_kbps:+$('#scAudio').value,height:+$('#scHeight').value,codec:$('#scCodec').value,preset:$('#scPreset').value,only_smaller:$('#scSmaller').checked,at:+$('#scAt').value}}
async function scChanged(){
 const rev=++scRevision; $('#scPlayer').pause(); $('#scPlayer').removeAttribute('src'); $('#scPlayer').load(); $('#scPlayer').style.display='none';
 $('#scBefore').removeAttribute('src');$('#scAfter').removeAttribute('src');$('#scStatus').textContent='Aperçu à générer avec les réglages actuels.';$('#scEstimate').textContent='';
 if(!scSrc.sel)return; const i=await API.media_info(scSrc.sel);if(rev!==scRevision)return;
 const p=scParams();if(i.duration)$('#scEstimate').textContent='Estimation pour le fichier sélectionné : '+human(i.duration*1000*((i.w?p.video_kbps:0)+(i.acodec?p.audio_kbps:0))/8)+' · source : '+i.size+' · '+Math.round(i.duration)+' s (estimation, hors conteneur).';
}
function initSmart(){
 scSrc=new Source($('#p-compress [data-src]'),scChanged,['video','audio']);const saved=CFG.smart_compress||{};
 for(const [id,key]of [['scVideo','video_kbps'],['scAudio','audio_kbps'],['scHeight','height'],['scCodec','codec'],['scPreset','preset']]){
  if(saved[key]!==undefined)$('#'+id).value=saved[key];$('#'+id).onchange=()=>{scChanged();API.set_cfg('smart_compress',scParams())};
 }
 $('#scAt').onchange=scChanged;
 if(saved.only_smaller!==undefined)$('#scSmaller').checked=saved.only_smaller;
 $('#scSmaller').onchange=()=>API.set_cfg('smart_compress',scParams());
 scRun=new Run($('#p-compress [data-run]'),async()=>{
  const files=await API.list_files(scSrc.paths,['video','audio'],scSrc.recursive);
  if(!files.length){scRun.log('Sélectionne un média.','err');return false}
  const r=await API.run_tool({tool:'smart_compress',files,params:scParams(),opts:{threads:1}});
  if(!r.ok)scRun.log(r.err,'err');return r.ok;
 });
 $('#scCancel').onclick=()=>API.stop();
 $('#scPlayer').onerror=()=>{if($('#scPlayer').hasAttribute('src'))$('#scStatus').textContent+=' Lecture indisponible : utilise les images avant/après ou H.264.'};
 $('#scPreview').onclick=async()=>{
  if(!scSrc.sel){$('#scStatus').textContent='Sélectionne un fichier.';return}
  if(currentRun||await API.is_running()){$('#scStatus').textContent='Attends la fin du traitement en cours.';return}
  const revision=scRevision;$('#scPreview').disabled=true;$('#scCancel').disabled=false;$('#scStatus').textContent='Compression réelle de 5 secondes en cours…';
  try{const r=await API.smart_preview(scSrc.sel,scParams());if(revision!==scRevision)return;
   if(!r.ok){$('#scStatus').textContent=r.err;return}
   $('#scPlayer').src=r.url;$('#scPlayer').style.display='block';if(r.before)$('#scBefore').src=r.before;if(r.after)$('#scAfter').src=r.after;
   $('#scStatus').textContent='Extrait compressé : '+r.duration.toFixed(1)+' s · '+r.size;
  }catch(e){$('#scStatus').textContent=String(e)}finally{$('#scPreview').disabled=false;$('#scCancel').disabled=true}
 };
}

/* ═════════ démarrage ═════════ */
(async()=>{await wait();API=window.pywebview.api;INFO=await API.init();CFG=INFO.cfg;
  initSmart();initConvert();initColor();initResize();initCombo();initTools();initSettings();checkFF();})();
</script></body></html>
"""

# ═══════════════════════════════════════════════════════════════════════
#  Méthodes ajoutées à Api (chemins multiples depuis l'interface)
# ═══════════════════════════════════════════════════════════════════════
def _api_process_paths(self, job):
    """Version de process() qui accepte une liste de chemins (fichiers ET dossiers)."""
    paths = job.pop("paths", None) or []
    kinds = set(job.get("apply_to") or ["image", "video", "audio"])
    files, seen = [], set()
    for p in paths:
        for f in scan(p, job.get("recursive", True), kinds):
            if f not in seen: seen.add(f); files.append(f)
    job["files"] = files
    return self.process(job)

def _api_list_files(self, paths, kinds=None, recursive=True):
    out, seen = [], set()
    for p in paths:
        for f in scan(p, recursive, set(kinds) if kinds else None):
            if f not in seen: seen.add(f); out.append(f)
    return out

Api.process_paths = _api_process_paths
Api.list_files = _api_list_files


def main():
    api = Api()
    w, h = 1320, 860
    win = webview.create_window("Media Toolkit", html=HTML, js_api=api,
                                width=w, height=h, min_size=(980, 640),
                                background_color="#0e0f13")
    api._window = win
    webview.start(debug=False)


if __name__ == "__main__":
    main()
