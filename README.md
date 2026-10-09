# KFluxTV

Lecteur de flux **Xtream Codes** pour Windows : TV en direct, films, séries, guide des programmes et diffusion Chromecast.

> KFluxTV ne fournit **aucun contenu**. Il faut disposer de ses propres identifiants (serveur, utilisateur, mot de passe) auprès d'un fournisseur de son choix.

## Fonctions

- Connexion par comptes Xtream (ou lien M3U complet), plusieurs comptes, connexion automatique au dernier compte
- **Direct**, **Films**, **Séries** avec catégories, recherche, fiche (affiche, résumé, note)
- Bandeau chronologique des programmes (EPG) sous la vidéo pour les chaînes en direct
- **Favoris** (onglet dédié, clic droit ou `Ctrl+D`) pour chaînes, films et séries, rangés en **groupes** (création, renommage, glisser-déposer)
- Masquer des chaînes, films, séries ou catégories entières ; boutons **tout développer / tout réduire**
- **Direct différé** : pause, retour arrière et retour au direct sur les chaînes en direct
- Mode **semi plein écran** (la vidéo remplit la fenêtre) et plein écran
- Choix de la sortie audio
- **Chromecast** : diffusion sur TV, avec conversion automatique (ffmpeg intégré) des flux que la Chromecast ne lit pas (ex. 1080p 50 images/s)
- Options et fenêtre de **mise à jour** intégrée (GitHub Releases)

## Installation

Télécharge depuis la page [Releases](https://github.com/KisakePro/KFluxTV/releases) :

- `KFluxTV-Setup-x.y.exe` : installation par utilisateur (sans droits administrateur), raccourcis, désinstallation
- `KFluxTV-Portable-x.y.exe` : un seul fichier, à lancer directement

Les comptes et réglages sont stockés dans `%APPDATA%\KFluxTV`.

## Compiler

Python 3.12+ et Windows.

```powershell
pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File build.ps1
```

Les fichiers sont produits dans `release/`. Pour une nouvelle version : modifier `VERSION` dans `iptv.py`, compiler, puis publier une release dont le tag est `v<VERSION>` avec les deux fichiers `.exe` (le nom doit contenir `Setup` ou `Portable`).

## Mise à jour automatique

L'application interroge `releases/latest` du dépôt (le dépôt doit être public). La version installée télécharge et lance le setup en mode silencieux ; la version portable se remplace elle-même puis redémarre.
