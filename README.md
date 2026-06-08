# nuiman-scrapping-moto

Surveillance quotidienne de la disponibilité des **casques SHOEI** par taille sur
**motoblouz.com**. Génère un rapport journalier (pastilles 🟢/🔴 par taille,
organisé par gamme, avec prix et lien fiche) et l'envoie sur Telegram à Nuiman.

## Fonctionnement

1. Récupération de tous les casques Shoei du listing Motoblouz (chargement
   « Voir plus de produits » jusqu'à épuisement).
2. Pour chaque casque : lecture du payload SSR Nuxt (`#__NUXT_DATA__`) qui contient
   nom, gamme, coloris, prix et la disponibilité de chaque taille (SKU `forSale`).
3. Génération d'un rapport texte (pastilles par taille, groupé par gamme) +
   sauvegarde JSON dans `reports/`.
4. Envoi du rapport sur Telegram (découpé en plusieurs messages si nécessaire).

Architecture **multi-sites générique** : ajouter un site = ajouter un scraper
(`src/scrapers/<site>.py` + enregistrement dans `registry.py`). Sites supportés :
**Motoblouz** (`motoblouz.com`), Dafy Moto (`dafy-moto.com`, non utilisé actuellement).

## Installation

```bash
cd nuiman-scrapping-moto
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

## Configuration

1. Copier `.env.example` vers `.env` et renseigner `TELEGRAM_BOT_TOKEN` et
   `TELEGRAM_CHAT_ID` (groupe Telegram de destination).
2. `config/products.yaml` définit le périmètre surveillé (catégories/produits).
   Par défaut : `https://www.motoblouz.com/marque/shoei/casque.html`.

## Utilisation

```bash
python -m src.main --dry-run   # scrape + affiche le rapport (pas d'envoi)
python -m src.main             # scrape + envoi Telegram
```

Variables d'env utiles : `SCRAPE_CONCURRENCY` (défaut 3), `REQUEST_DELAY_MS`
(défaut 250), `MAX_PRODUCTS` (plafond de test, 0 = illimité).

## Automatisation (GitHub Actions)

`.github/workflows/daily-report.yml` exécute le rapport **tous les jours à 8h**
(cron `0 6 * * *` UTC), indépendamment de toute machine locale. Les identifiants
Telegram sont stockés en **GitHub Secrets** (`TELEGRAM_BOT_TOKEN`,
`TELEGRAM_CHAT_ID`). Le cron ne s'active que lorsque le workflow est sur la
branche par défaut (`main`).

## Tests

```bash
pytest
```

## Structure

```
config/products.yaml      périmètre surveillé
src/
  config.py               chargement env + produits/catégories
  models.py               dataclasses (SizeStatus, ProductResult, Report)
  scrapers/
    base.py               interface BaseScraper
    motoblouz.py          scraper Motoblouz (payload Nuxt, dispo par taille)
    dafy.py               scraper Dafy Moto
    registry.py           sélection du scraper par domaine
  report.py               rapport Telegram (pastilles, groupé par gamme)
  telegram.py             envoi Telegram
  main.py                 orchestration (point d'entrée)
reports/                  rapports JSON archivés
tests/
```
