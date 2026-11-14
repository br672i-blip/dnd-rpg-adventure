# D&D RPG Adventure

A fantasy role-playing game made with Python and pygame. Pick a Warrior, Mage or Rogue, explore an endless generated world, fight turn-based battles and real-time dungeons, and travel through four realms (Overworld, Nether, Void, Celestial) to face Solgareth, the Fallen.

**Play in your browser:** https://br672i-blip.github.io/dnd-rpg-adventure/ (the game itself is at [/play/](https://br672i-blip.github.io/dnd-rpg-adventure/play/))

## Run it on your computer

Needs Python 3.12 or newer.

```sh
pip install -r requirements.txt
python pygame_DnD_RPG_infinite_map_v2.py
```

Saves go to `rpg_savegame_<slot>.pkl` next to the game file (3 slots).

## Controls

| Action | Keys |
|---|---|
| Move | WASD or arrow keys |
| Sprint | Shift |
| Interact, enter, loot | E |
| Inventory / quest journal | I / J |
| Heal (10 MP) | H |
| Attack in dungeons | Space |
| Class skill / ultimate (level 10) | B / Q |
| Save | F5 |
| Fullscreen (desktop) | F11 |

## How the web version works

`.github/workflows/pages.yml` compiles the game to WebAssembly with [pygbag](https://github.com/pygame-web/pygbag) on every push to `main` and publishes it to GitHub Pages with the landing page in `site/`. In the browser, save files are kept in IndexedDB so they survive page reloads.
