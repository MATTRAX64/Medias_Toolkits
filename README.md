# ◈ Media Toolkit

> Interface graphique tout-en-un pour convertir, télécharger, ajuster et redimensionner vos médias — propulsé par FFmpeg.

![Python](https://img.shields.io/badge/Python-3.8%2B-blue?logo=python&logoColor=white)
![FFmpeg](https://img.shields.io/badge/FFmpeg-required-green?logo=ffmpeg&logoColor=white)
![Platform](https://img.shields.io/badge/Platform-Windows-lightgrey?logo=windows)
![License](https://img.shields.io/badge/License-MIT-purple)

[⬇️ Télécharger](https://github.com/MATTRAX64/Medias_Toolkits/releases/latest/download/MediaToolkit.py)

---

## Aperçu

**Media Toolkit** est une application de bureau légère (tkinter, aucune dépendance lourde) qui regroupe en un seul endroit les opérations les plus courantes sur les fichiers multimédia :

| Onglet | Fonctionnalité |
|---|---|
| **Format Changer** | Convertit en lot vidéos, images et audios vers les formats de votre choix |
| **YouTube** | Télécharge des vidéos / playlists YouTube via yt-dlp |
| **Couleurs** | Ajuste saturation, luminosité, contraste et gamma d'images |
| **Redimensionner** | Réduit des images à une hauteur max en préservant le ratio |
| **Paramètres** | Configure FFmpeg une seule fois, partagé par tous les outils |

---

## Prérequis

- **Python 3.8+** (tkinter inclus sur Windows)
- **FFmpeg** — [Télécharger sur ffmpeg.org](https://ffmpeg.org/download.html)
- **yt-dlp** — installé automatiquement au premier lancement de l'onglet YouTube (ou via le bouton dans Paramètres)

> FFmpeg doit être dans votre `PATH` ou localisé manuellement dans l'onglet ⚙ Paramètres.

---

## Installation

```bash
# 1. Cloner le dépôt
git clone https://github.com/votre-utilisateur/media-toolkit.git
cd media-toolkit

# 2. (Optionnel) Créer un environnement virtuel
python -m venv venv
venv\Scripts\activate  # Windows

# 3. Aucune dépendance pip requise au démarrage
#    yt-dlp s'installe automatiquement si nécessaire

# 4. Lancer l'application
python MediaToolkit.py
```

---

## Utilisation

### Format Changer
Sélectionne un dossier source, choisis les formats cibles pour la vidéo, l'image et l'audio, puis lance la conversion. Tous les sous-dossiers sont traités récursivement. Les fichiers déjà convertis (marqués `_CONV`) sont ignorés.

**Formats vidéo supportés :** `mp4`, `mkv`, `mov`, `webm`, `avi`  
**Formats image supportés :** `jpg`, `png`, `webp`, `avif`, `tiff`, `bmp`, `gif`  
**Formats audio supportés :** `mp3`, `flac`, `aac`, `ogg`, `opus`, `wav`, `m4a`

### YouTube
Colle une URL YouTube (vidéo ou playlist), choisis le format de sortie et le dossier de destination. Les téléchargements se font en parallèle.

**Formats disponibles :** MP3 (192/320 kbps), MP4 (meilleur/1080p/720p), FLAC, AAC, Opus

### Couleurs
Ajuste les propriétés colorimétriques d'une image unique ou d'un dossier entier via des sliders (-100 → +100). Le format de sortie peut être conservé ou changé à la volée.

### Redimensionner
Définit une **hauteur maximale** en pixels — la largeur s'adapte automatiquement au ratio. Les images déjà plus petites ne sont pas modifiées.

- Raccourcis rapides : `240p` → `4320p`
- Saisie manuelle libre
- Suffixe personnalisable (vide = remplacement en place)

---

## Accélération GPU

Si une carte NVIDIA compatible CUDA est détectée, la conversion vidéo utilise automatiquement l'encodeur `hevc_nvenc` pour des conversions bien plus rapides. L'onglet Paramètres indique si le GPU est actif.

---

## Configuration

Les réglages sont sauvegardés automatiquement dans `mediatoolkit.json` dans le dossier du script. Ce fichier est recréé s'il est supprimé.

---

## Structure du projet

```
media-toolkit/
├── MediaToolkit.py      # Application principale (script unique)
├── mediatoolkit.json    # Config auto-générée (ignorée par git)
└── README.md
```

---

## Contribuer

Les pull requests sont les bienvenues. Pour les changements majeurs, ouvre d'abord une issue pour discuter de ce que tu souhaites modifier.

1. Fork le projet
2. Crée ta branche (`git checkout -b feature/ma-fonctionnalite`)
3. Commit tes changements (`git commit -m 'feat: ajout de ma fonctionnalité'`)
4. Push sur la branche (`git push origin feature/ma-fonctionnalite`)
5. Ouvre une Pull Request

---

## Licence

Distribué sous licence MIT. Voir `LICENSE` pour plus d'informations.
