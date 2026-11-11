# Discord Presence Studio

Petite appli Windows pour personnaliser ton statut Discord (Rich Presence, affiché « Joue à … »).
Elle utilise uniquement l'API officielle Rich Presence (Client ID d'une application Discord) : **aucun token, aucun selfbot**.

---

## 1. Créer ton application sur Discord (obligatoire, 5 min)

Le nom de ton application sera le nom affiché après « Joue à ».

1. Va sur <https://discord.com/developers/applications> et connecte-toi.
2. Clique sur **New Application** (en haut à droite), donne un nom (ex. « Mon Projet »), accepte les conditions, clique sur **Create**.
3. Sur la page **General Information**, copie l'**Application ID**. C'est ton **Client ID** (un nombre de 17 à 20 chiffres). Garde-le, tu le colleras dans l'appli.
4. (Optionnel, pour avoir des images) Dans le menu de gauche : **Rich Presence → Art Assets**.
   - Clique sur **Add Image(s)** et choisis tes images (512×512 conseillé, 1024×1024 max).
   - Le **nom du fichier** (sans extension) devient la **clé** de l'image, ex. `logo.png` → `logo`.
   - Clique sur **Save Changes**. Les images peuvent mettre quelques minutes à être disponibles.

---

## 2. Régler Discord sur ton PC

1. **Lance Discord desktop** (l'application installée, pas le site web : le site ne fonctionne pas).
2. Ouvre **Paramètres utilisateur** (roue dentée en bas à gauche) → **Confidentialité des activités** (ou « Activity Privacy »).
3. Active **Partager mon activité détectée avec les autres** (« Afficher l'activité en cours comme statut »).
4. Ne mets pas ton statut en **Invisible** : l'activité n'est pas affichée dans ce cas.

---

## 3. Installer et lancer l'appli

### Option A : lancer avec Python

1. Installe **Python 3.12** depuis <https://www.python.org/downloads/>. Pendant l'installation, **coche « Add python.exe to PATH »**.
2. Télécharge ce projet (bouton vert **Code → Download ZIP** sur GitHub) et décompresse-le.
3. Ouvre un terminal dans le dossier (clic droit dans le dossier → « Ouvrir dans le Terminal ») et lance :

```bash
pip install -r requirements.txt
python main.py
```

### Option B : créer un .exe (sans avoir besoin de Python ensuite)

Après l'étape `pip install -r requirements.txt` ci-dessus :

```bash
pyinstaller --onefile --noconsole --name DiscordPresenceStudio --collect-all customtkinter --hidden-import pystray._win32 main.py
```

Ton exécutable est créé dans `dist\DiscordPresenceStudio.exe`. Tu peux le déplacer où tu veux et double-cliquer dessus.

---

## 4. Utiliser l'appli

1. Onglet **Présence** : colle ton **Client ID**, puis remplis `Details` (ligne 1) et `State` (ligne 2). 2 caractères minimum, 128 maximum.
2. Onglet **Images** : saisis la **clé** de tes images (ex. `logo`) et le texte affiché au survol.
3. Onglet **Boutons** : jusqu'à 2 boutons (libellé 32 caractères max + URL en `http://` ou `https://`).
4. Onglet **Timer & groupe** :
   - Timer : aucun, temps écoulé depuis le lancement, temps écoulé custom (HH:MM) ou compte à rebours (HH:MM).
   - Groupe : « x / y » (ex. 2 / 4).
5. Clique sur **Connecter**. Le voyant en bas indique l'état :
   - 🟢 **Connecté**
   - 🟠 **Discord introuvable** : lance Discord, l'appli réessaie toute seule (délai croissant).
   - 🔴 **Erreur** : le message explique la cause (ex. Client ID refusé).
6. Quand tu modifies un champ, la mise à jour est envoyée automatiquement après une courte pause. Discord limite à **1 mise à jour toutes les 15 secondes** : l'appli attend si besoin. Le bouton **Mettre à jour** force l'envoi (et reconnecte si tu as changé le Client ID).
7. **Déconnecter** efface ton statut.

### Profils

En haut : tape un nom dans la liste puis **Enregistrer** pour sauvegarder tous les champs. Choisis un profil dans la liste pour le charger. Le dernier profil utilisé est rechargé au lancement.

### Onglet Paramètres

- **Minimiser dans la barre système** : fermer ou réduire la fenêtre la cache près de l'horloge. Clic droit sur l'icône → Ouvrir / Quitter.
- **Démarrer avec Windows** : lance l'appli réduite à l'ouverture de session et se connecte seule si le profil est valide.

---

## 5. Problèmes fréquents

| Symptôme | Solution |
|---|---|
| « Discord introuvable » | Lance Discord **desktop** (pas le navigateur), ferme-le puis rouvre-le si ça persiste. |
| Rien ne s'affiche sur mon profil | Vérifie l'étape 2 (partage d'activité, statut pas Invisible). Regarde bien ton profil, pas seulement la liste des membres. |
| Mes boutons n'apparaissent pas | Normal : tu ne vois pas tes propres boutons, les autres si. |
| L'image n'apparaît pas | Vérifie que la clé est exacte (sans extension) et attends quelques minutes après l'upload. |
| Le nom après « Joue à » est faux | Il vient du nom de l'application dans le Developer Portal, pas de l'appli. Modifie-le là-bas. |
| Une mise à jour met du temps | Limite Discord : 15 s entre deux mises à jour. |

Fichiers de l'appli : `%APPDATA%\DiscordPresenceStudio\` (profils dans `profiles.json`, logs rotatifs dans `app.log`). Colle `%APPDATA%` dans la barre d'adresse de l'Explorateur pour y accéder.
