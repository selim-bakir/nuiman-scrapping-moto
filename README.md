# nuiman-scrapping-moto

Surveillance quotidienne de la disponibilité des casques et accessoires moto par
taille, sur différents sites e-commerce. Génère un rapport journalier complet
(disponible + indisponible par taille) et l'envoie sur Telegram à Nuiman.

## Fonctionnement

1. Lecture de la liste des produits à surveiller (`config/products.yaml`).
2. Scraping de chaque page produit avec Playwright (le scraper est choisi
   automatiquement selon le domaine de l'URL).
3. Détection de la disponibilité de chaque taille.
4. Génération d'un rapport texte + sauvegarde JSON dans `reports/`.
5. Envoi du rapport sur Telegram.

Sites supportés actuellement : **Dafy Moto** (`dafy-moto.com`).
L'architecture est générique : ajouter un site = ajouter un scraper (voir plus bas).

## Installation

```bash
cd nuiman-scrapping-moto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configuration

1. Copier `.env.example` vers `.env` et renseigner :
   - `TELEGRAM_BOT_TOKEN` : token du bot (créé via [@BotFather](https://t.me/BotFather)).
   - `TELEGRAM_CHAT_ID` : chat ID de Nuiman.

   > Pour obtenir le chat ID : Nuiman démarre une conversation avec le bot, puis
   > ouvrir `https://api.telegram.org/bot<TOKEN>/getUpdates` et lire `chat.id`.

2. Éditer `config/products.yaml` pour la liste des modèles à surveiller.

## Utilisation

```bash
# Test sans envoi Telegram (affiche le rapport dans le terminal)
python -m src.main --dry-run

# Exécution complète (scrape + envoi Telegram)
python -m src.main
```

## Tests

```bash
pytest
```

## Ajouter un nouveau site

1. Créer `src/scrapers/<site>.py` avec une classe héritant de `BaseScraper`,
   en définissant `domains` et la méthode `scrape`.
2. L'enregistrer dans `src/scrapers/registry.py`.

Le reste (config, rapport, Telegram, parallélisme) est mutualisé.

## Structure

```
config/products.yaml   liste des produits surveillés
src/
  config.py            chargement env + produits
  models.py            dataclasses (SizeStatus, ProductResult, Report)
  scrapers/
    base.py            interface BaseScraper
    dafy.py            scraper Dafy Moto
    registry.py        sélection du scraper par domaine
  report.py            génération du texte du rapport
  telegram.py          envoi Telegram
  main.py              orchestration (point d'entrée)
reports/               rapports JSON archivés
tests/
```
