# Мае паходы

Сайт-карта маіх хайкінгаў: трэкі з Komoot, фота, відэа з YouTube і ўражанні.
Беларуская і англійская мовы, светлая і цёмная тэма, 3D-рэльеф, фільтры і агульная статыстыка.

## Як дадаць новы паход

1. Запішы тур у Komoot (від спорту «Пешы паход» або «Альпінізм»).
   Шматдзённы паход = асобны тур на кожны дзень.
2. Скапіюй 10–20 лепшых фота ў падтэчку `Сайт` у тэчцы паходу:
   `F:\Фота і відэа\20250517_Зялёнае_возера\Сайт\`.
   Падыходзіць і назва `для сайту`.
3. У тэрмінале ў `D:\IT\my-hiking`:

   ```powershell
   .\hike komoot    # спампаваць туры (спытае email і пароль Komoot)
   .\hike youtube   # абнавіць спіс відэа, калі выйшла новае
   .\hike new       # стварыць чарнавік content\hikes\<дата-назва>.yaml
   ```

4. Адкрый новы `.yaml` і дапішы: англійскую назву, складанасць, уражанні.
   Правер, ці правільна падабраныя відэа.
5. Збяры і паглядзі сайт:

   ```powershell
   .\hike build     # фота → WebP, трэкі, docs\data\hikes.json
   .\hike serve     # http://localhost:8000
   ```

6. Апублікуй:

   ```powershell
   git add -A
   git commit -m "Паход: Зялёнае возера"
   git push
   ```

## Файл паходу (`content/hikes/*.yaml`)

```yaml
date: 2025-05-17
title:
  be: "Зялёнае возера"
  en: "Green Lake"          # калі пуста, паказваецца беларуская назва
region:
  be: "Аджарыя"
  en: "Adjara"
difficulty: medium          # easy | medium | hard | expert
photos_folder: "20250517_Зялёнае_возера"
cover: _MG_0945.JPG         # неабавязкова; інакш першае фота па часе здымкі
komoot:                     # туры аднаго дня аб'ядноўваюцца ў адзін дзень
  - 1234567890
youtube:
  - VQ3BMsY4Fjs
location: [41.65, 41.87]    # [шырата, даўгата] толькі для паходу без трэку
hidden: true                # неабавязкова: схаваць паход з сайта
impressions:
  be: |
    Першы абзац.

    Другі абзац (абзацы раздзяляюцца пустым радком).
  en: |
    First paragraph.
```

Калі тур або тэчку не трэба ператвараць у паход, дадай іх у `content/ignore.yaml`.

## Як гэта працуе

```
config.yaml           шляхі і налады (тэчка з фота, канал YouTube, віды спорту Komoot)
content/hikes/*.yaml  апісанні паходаў, якія пішаш ты
scripts/              Python-скрыпты (hike komoot | youtube | new | build | serve)
cache/                (не ў git) туры Komoot, спіс відэа, кэш рэгіёнаў і фота
docs/                 гатовы сайт для GitHub Pages
  index.html, assets/ карта (MapLibre), панэль, профіль вышыні, галерэя (PhotoSwipe)
  data/               hikes.json і трэкі, якія генеруе `hike build`
  photos/             фота ў WebP (2000 px + мініяцюры 640 px)
```

* Пароль Komoot выкарыстоўваецца толькі падчас `hike komoot` і нікуды не захоўваецца.
* Спасылка «Трэк у Komoot» з'яўляецца толькі для публічных тураў. Прыватныя туры
  для наведвальнікаў усё роўна не адкрыюцца.
* Фота пераціскаюцца толькі тады, калі змяніліся (`hike build --force`, каб пераціснуць усе).
  Метаданыя EXIF, у тым ліку GPS, з фота на сайце выдаляюцца.
* Падкладкі карты: OpenTopoMap, Esri World Imagery, CARTO; рэльеф: AWS Terrain Tiles.

## Публікацыя на GitHub Pages (адзін раз)

1. Ствары пусты рэпазітар на GitHub, напрыклад `my-hiking`.
2. У `D:\IT\my-hiking`:

   ```powershell
   git init -b main
   git add -A
   git commit -m "Першая версія"
   git remote add origin https://github.com/<login>/my-hiking.git
   git push -u origin main
   ```

3. На GitHub: **Settings → Pages → Build and deployment → Deploy from a branch**,
   галіна `main`, тэчка `/docs`.
4. Праз хвіліну сайт будзе на `https://<login>.github.io/my-hiking/`.

## Першае ўсталяванне на іншым камп'ютары

```powershell
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt
```
