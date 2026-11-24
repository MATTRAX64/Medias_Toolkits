# Media Toolkit

Application Windows pour **compresser, convertir et monter rapidement** des vidéos, des images et des fichiers audio avec FFmpeg. L’interface pywebview est intégrée à un seul fichier `MediaToolkit.pyw`.

[Voir les versions sur GitHub](https://github.com/MATTRAX64/Medias_Toolkits/releases)

## Installation

- **Windows et Python 3**. La version actuelle a été testée avec Python 3.13.
- **FFmpeg et FFprobe**. Ajoute-les au `PATH` ou indique le chemin de `ffmpeg.exe` dans **Paramètres**. [Télécharger FFmpeg](https://ffmpeg.org/download.html)

Place `MediaToolkit.pyw` dans le dossier de ton choix et ouvre-le en double-cliquant dessus. L’application tente d’installer les dépendances Python manquantes au premier lancement. Pour les installer manuellement, ouvre PowerShell dans le dossier du fichier et exécute :

```powershell
python -m pip install pywebview pillow pillow-heif
python .\MediaToolkit.pyw
```

## Compresser une vidéo

1. Ajoute un ou plusieurs fichiers, ou sélectionne un dossier.
2. Choisis le **format final**. Le format vidéo par défaut est **MP4**. Tu peux aussi choisir **MKV** ou **WebM**. Pour l’audio seul, choisis **M4A** ou **MP3**.
3. Choisis **720p compact (AV1)**, **1080p qualité (AV1)** ou **720p compatible (H.264)**. Le profil WebM utilise AV1.
4. Garde la taille en **Auto** ou règle une **Taille cible** en Mo par fichier. Le poids obtenu peut varier légèrement selon l’encodage.
5. Clique sur **Tester 5 s** pour comparer la vidéo d’origine et le rendu. **Estimer le temps et le poids** encode quelques extraits avec les réglages choisis.
6. Lance l’export. Le journal indique la taille et le temps d’encodage réels. La compression conserve l’original et enregistre une copie uniquement si elle est plus légère.

### Carte graphique

Pour les profils AV1, tu peux choisir **Intel Quick Sync**, **NVIDIA NVENC**, **CPU (SVT-AV1)** ou laisser **Auto**. Le mode automatique essaie Intel, puis NVIDIA, puis le CPU. Les options matérielles dépendent de la carte, du pilote et des encodeurs disponibles dans FFmpeg. Le profil compatible H.264 utilise le CPU.

## Convertir et ajuster

Convertis en lot des vidéos, images ou fichiers audio. Les formats courants apparaissent en premier et la case **Afficher les formats avancés** donne accès au reste.

Dans le même écran, tu peux limiter la hauteur en conservant les proportions, puis régler la saturation, la luminosité, le contraste et le gamma. **Voir l’aperçu** montre l’image exportée ou deux courts extraits vidéo avant/après.

## Montage rapide

| Catégorie | Outils |
| --- | --- |
| Montage vidéo | Couper, fusionner des fichiers compatibles, appliquer une rotation ou un miroir |
| Images et captures | Créer un GIF, capturer une image, exporter une séquence d’images |
| Audio | Extraire ou retirer le son, régler le volume, normaliser le niveau sonore |

Pour couper une vidéo, renseigne le début et la fin en secondes ou au format `hh:mm:ss`. Tu peux lire les cinq secondes qui suivent le début choisi avant d’exporter.

## Analyse et préférences

**Analyser** affiche les informations du média, notamment sa résolution, sa durée, ses codecs et son débit. Dans **Paramètres**, configure FFmpeg et choisis où enregistrer les copies, avec un suffixe ou dans un sous-dossier.

Les préférences sont sauvegardées dans le registre Windows de l’utilisateur. Le fichier `MediaToolkit.pyw` contient le code et l’interface et se suffit à lui-même côté projet.

## Contribuer

Pour signaler un problème, indique ta version de Windows, de Python et de FFmpeg, ainsi que le format du fichier et les réglages d’export. [Issues GitHub](https://github.com/MATTRAX64/Medias_Toolkits/issues)

## Licence

MIT.
