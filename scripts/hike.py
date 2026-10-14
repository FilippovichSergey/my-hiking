"""Адзіная кропка ўваходу: hike <каманда> [параметры]

  komoot   спампаваць туры з Komoot (спытае email і пароль)
  youtube  абнавіць спіс відэа з YouTube-канала
  new      стварыць чарнавікі content/hikes/*.yaml для новых паходаў
  fetch    спампаваць даныя сэрвісаў для практычнай часткі: надвор'е падчас паходаў і звычайнае па месяцах,
           курс лары, падказкі OpenStreetMap, дарогу на машыне, назвы населеных пунктаў
  build    сабраць сайт у docs/ (фота, трэкі, даныя)
  status   паказаць, чаго не хапае ў паходах (назвы, уражанні, практычная частка, фота на карце)
  serve    запусціць сайт лакальна: http://localhost:8000
"""
import importlib
import sys

COMMANDS = {"komoot": "komoot", "youtube": "youtube", "new": "scaffold", "fetch": "fetch", "build": "build",
            "serve": "serve", "status": "status"}


def main() -> None:
    if len(sys.argv) < 2 or sys.argv[1] not in COMMANDS:
        print(__doc__)
        sys.exit(1)
    module = importlib.import_module(COMMANDS[sys.argv[1]])
    module.main(sys.argv[2:])


if __name__ == "__main__":
    main()
