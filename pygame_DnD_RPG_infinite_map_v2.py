import pygame
import random
import math
import sys
import os
import pickle
import asyncio
import base64
import zlib

# Running in a web browser through pygbag (WebAssembly)?
IS_WEB = sys.platform == "emscripten"

SAVE_DIR = os.path.dirname(os.path.abspath(__file__))
SAVE_SLOTS = 3
# Legacy single-file save (kept for backwards-compat migration on first load)
SAVE_PATH = os.path.join(SAVE_DIR, "rpg_savegame.pkl")

def save_path_for_slot(slot):
    return os.path.join(SAVE_DIR, f"rpg_savegame_{slot}.pkl")

def meta_path_for_slot(slot):
    return os.path.join(SAVE_DIR, f"rpg_savegame_{slot}.meta.txt")

# In the browser, files only live in memory and vanish when the page reloads. Save files are
# mirrored into the browser's IndexedDB (compressed, base64 encoded): restored into SAVE_DIR at
# startup, and written back whenever a save is made or deleted. On desktop these do nothing.
WEB_STORE_JS = """
window.rpgStore = (() => {
  const open = () => new Promise((res, rej) => {
    const r = indexedDB.open('dnd-rpg-saves', 1);
    r.onupgradeneeded = () => r.result.createObjectStore('files');
    r.onsuccess = () => res(r.result); r.onerror = () => rej(r.error);
  });
  const S = { ready: false, files: {},
    names() { return Object.keys(S.files).join('\\n'); },
    get(name) { return S.files[name] || ''; },
    put(name, data) { S.files[name] = data; open().then(db => db.transaction('files', 'readwrite').objectStore('files').put(data, name)).catch(e => console.warn('save failed', e)); },
    delete(name) { delete S.files[name]; open().then(db => db.transaction('files', 'readwrite').objectStore('files').delete(name)).catch(e => console.warn('delete failed', e)); },
  };
  open().then(db => {
    const req = db.transaction('files').objectStore('files').openCursor();
    req.onsuccess = () => { const c = req.result; if (c) { S.files[c.key] = c.value; c.continue(); } else S.ready = true; };
    req.onerror = () => { S.ready = true; };
  }).catch(() => { S.ready = true; });
  return S;
})();
"""

async def web_restore_saves():
    """Browser only: copy save files from IndexedDB into SAVE_DIR before the game starts."""
    if not IS_WEB:
        return
    import platform as web
    web.window.eval(WEB_STORE_JS)
    for _ in range(300):  # IndexedDB answers within a few frames; give up after ~5 s
        if web.window.rpgStore.ready:
            break
        await asyncio.sleep(1 / 60)
    names = str(web.window.rpgStore.names() or '')
    for name in filter(None, names.split('\n')):
        try:
            data = zlib.decompress(base64.b64decode(str(web.window.rpgStore.get(name))))
            with open(os.path.join(SAVE_DIR, os.path.basename(name)), 'wb') as f:
                f.write(data)
        except Exception as e:
            print(f"Could not restore {name}: {e}")

def web_persist(path):
    """Browser only: store a just-written save file in IndexedDB."""
    if not IS_WEB:
        return
    import platform as web
    with open(path, 'rb') as f:
        data = base64.b64encode(zlib.compress(f.read(), 6)).decode('ascii')
    web.window.rpgStore.put(os.path.basename(path), data)

def web_forget(path):
    """Browser only: remove a deleted save file from IndexedDB."""
    if not IS_WEB:
        return
    import platform as web
    web.window.rpgStore.delete(os.path.basename(path))

# Fighting animation lengths, in frames. Attacks wind up, strike, then recover;
# anything taking a hit recoils and flashes for a shorter beat.
ATK_ANIM_FRAMES = 18
HURT_ANIM_FRAMES = 12

# Initialize Pygame
pygame.init()

# Constants
SCREEN_WIDTH = 1024
SCREEN_HEIGHT = 768
FPS = 60

# Colors
BLACK = (0, 0, 0)
WHITE = (255, 255, 255)
RED = (255, 0, 0)
GREEN = (0, 255, 0)
BLUE = (0, 0, 255)
YELLOW = (255, 255, 0)
GRAY = (128, 128, 128)
DARK_GRAY = (64, 64, 64)
LIGHT_GRAY = (192, 192, 192)
BROWN = (139, 69, 19)
DARK_GREEN = (0, 100, 0)

DIFFICULTIES = [
    {
        'name': 'Easy',
        'color': (80, 200, 80),
        'desc': 'Relaxed exploration. Weaker enemies, more loot.',
        'enemy_hp':   0.6,
        'enemy_dmg':  0.6,
        'enemy_count': 0.6,
        'loot_bonus': 20,
        'stamina_drain': 0.7,
        'start_gold': 150,
    },
    {
        'name': 'Normal',
        'color': (200, 200, 80),
        'desc': 'Balanced challenge. The intended experience.',
        'enemy_hp':   1.0,
        'enemy_dmg':  1.0,
        'enemy_count': 1.0,
        'loot_bonus': 0,
        'stamina_drain': 1.0,
        'start_gold': 50,
    },
    {
        'name': 'Hard',
        'color': (220, 120, 40),
        'desc': 'Tougher enemies, scarce resources. Plan carefully.',
        'enemy_hp':   1.5,
        'enemy_dmg':  1.5,
        'enemy_count': 1.3,
        'loot_bonus': -10,
        'stamina_drain': 1.3,
        'start_gold': 25,
    },
    {
        'name': 'Nightmare',
        'color': (220, 40, 40),
        'desc': 'Maximum brutality. Good luck.',
        'enemy_hp':   2.5,
        'enemy_dmg':  2.5,
        'enemy_count': 1.6,
        'loot_bonus': -20,
        'stamina_drain': 2.0,
        'start_gold': 0,
    },
]

MOB_TYPES = {
    # Overworld
    'Goblin':         {'hp': 1.0, 'dmg': 1.0, 'speed': 1.5, 'aggro': 100, 'range': 40,  'exp': 1.0, 'gold': 1.0},
    'Wolf':           {'hp': 0.7, 'dmg': 0.9, 'speed': 2.5, 'aggro': 130, 'range': 35,  'exp': 0.9, 'gold': 0.4},
    'Zombie':         {'hp': 1.5, 'dmg': 0.8, 'speed': 0.6, 'aggro':  70, 'range': 38,  'exp': 1.1, 'gold': 0.6},
    'Skeleton':       {'hp': 0.8, 'dmg': 1.2, 'speed': 1.2, 'aggro': 110, 'range': 40,  'exp': 1.2, 'gold': 0.8},
    'Bandit':         {'hp': 1.2, 'dmg': 1.3, 'speed': 1.4, 'aggro':  90, 'range': 42,  'exp': 1.3, 'gold': 2.0},
    'Spider':         {'hp': 0.6, 'dmg': 1.1, 'speed': 2.0, 'aggro': 120, 'range': 35,  'exp': 1.0, 'gold': 0.7},
    'Orc':            {'hp': 2.0, 'dmg': 1.5, 'speed': 1.0, 'aggro':  80, 'range': 45,  'exp': 1.5, 'gold': 1.5},
    'Dark Elf':       {'hp': 1.1, 'dmg': 1.6, 'speed': 1.8, 'aggro': 100, 'range': 50,  'exp': 1.8, 'gold': 1.8},
    'Troll':          {'hp': 3.0, 'dmg': 2.0, 'speed': 0.8, 'aggro':  80, 'range': 50,  'exp': 2.5, 'gold': 2.0},
    'Dragon':         {'hp': 3.2, 'dmg': 2.2, 'speed': 1.1, 'aggro': 120, 'range': 55,  'exp': 5.0, 'gold': 4.0},
    'Vampire':        {'hp': 1.6, 'dmg': 1.6, 'speed': 2.0, 'aggro': 140, 'range': 45,
                       'exp': 2.4, 'gold': 2.0, 'lifesteal': 0.35},   # heals on hit
    # Nether
    'Imp':            {'hp': 0.7, 'dmg': 1.3, 'speed': 2.2, 'aggro': 120, 'range': 38,  'exp': 1.4, 'gold': 0.8},
    'Demon':          {'hp': 2.2, 'dmg': 2.0, 'speed': 1.3, 'aggro': 100, 'range': 48,  'exp': 2.2, 'gold': 1.8},
    'Fire Giant':     {'hp': 4.5, 'dmg': 2.8, 'speed': 0.8, 'aggro':  90, 'range': 58,  'exp': 4.0, 'gold': 3.0},
    # Void
    'Wraith':         {'hp': 0.8, 'dmg': 1.5, 'speed': 2.0, 'aggro': 130, 'range': 45,  'exp': 1.6, 'gold': 0.5},
    'Shadow Beast':   {'hp': 2.0, 'dmg': 2.2, 'speed': 1.5, 'aggro': 110, 'range': 52,  'exp': 2.8, 'gold': 1.2},
    'Void Lord':      {'hp': 5.5, 'dmg': 3.2, 'speed': 1.0, 'aggro': 150, 'range': 62,  'exp': 5.5, 'gold': 3.5},
    # Celestial
    'Fallen Angel':   {'hp': 2.0, 'dmg': 2.2, 'speed': 1.8, 'aggro': 120, 'range': 52,  'exp': 2.8, 'gold': 2.2},
    'Celestial Guard':{'hp': 3.2, 'dmg': 2.0, 'speed': 1.2, 'aggro':  90, 'range': 50,  'exp': 3.2, 'gold': 2.8},
    'Seraph':         {'hp': 6.5, 'dmg': 4.2, 'speed': 1.5, 'aggro': 160, 'range': 68,  'exp': 7.0, 'gold': 5.5},
}

DIMENSION_PORTAL = {
    'overworld': {'dest': 'nether',    'color': (220, 80, 20),   'label': 'Nether Portal'},
    'nether':    {'dest': 'void',      'color': (100, 0, 180),   'label': 'Void Portal'},
    'void':      {'dest': 'celestial', 'color': (200, 220, 255), 'label': 'Celestial Portal'},
    'celestial': {'dest': 'overworld', 'color': (60, 180, 255),  'label': 'Return Portal'},
}

DIMENSION_SKY = {
    'overworld': (30, 30, 30),
    'nether':    (80, 20, 0),
    'void':      (5, 0, 20),
    'celestial': (180, 200, 255),
}

# Which mob types can appear at each distance tier from the origin chunk
MOB_TIERS = [
    ['Goblin', 'Wolf', 'Zombie'],                                          # tier 0 (dist 0)
    ['Goblin', 'Wolf', 'Skeleton', 'Bandit', 'Spider'],                    # tier 1 (dist 1-2)
    ['Orc', 'Skeleton', 'Bandit', 'Spider', 'Dark Elf', 'Vampire'],        # tier 2 (dist 3-4)
    ['Orc', 'Dark Elf', 'Troll', 'Dragon', 'Vampire'],                     # tier 3 (dist 5+)
]

DIMENSION_MOBS = {
    'overworld': MOB_TIERS,
    'nether':    [['Imp'], ['Imp', 'Demon'], ['Demon', 'Fire Giant'], ['Fire Giant']],
    'void':      [['Wraith'], ['Wraith', 'Shadow Beast'], ['Shadow Beast', 'Void Lord'], ['Void Lord']],
    'celestial': [['Fallen Angel'], ['Fallen Angel', 'Celestial Guard'], ['Celestial Guard', 'Seraph'], ['Seraph']],
}

# Themed dungeon variants. Each entry: visuals, mob roster, chest count, cover obstacles.
# Obstacles are (fx, fy, w, h) where fx/fy are fractions of the dungeon room dimensions.
DUNGEON_TYPES = {
    'kingdom': {
        'name': 'Abandoned Kingdom',
        'floor': (110, 100, 85),
        'wall':  (210, 190, 110),
        'accent': (255, 220, 120),
        'mobs': ['Orc', 'Dark Elf', 'Bandit', 'Skeleton'],
        'chest_range': (3, 4),
        'obstacles': [(0.22, 0.30, 50, 110), (0.70, 0.30, 50, 110),
                      (0.22, 0.62, 50, 110), (0.70, 0.62, 50, 110)],
        'rarity_bias': ['common', 'uncommon', 'rare', 'rare'],
    },
    'crypt': {
        'name': 'Cursed Crypt',
        'floor': (55, 50, 70),
        'wall':  (130, 100, 160),
        'accent': (190, 160, 220),
        'mobs': ['Zombie', 'Skeleton', 'Wraith', 'Shadow Beast'],
        'chest_range': (2, 3),
        'obstacles': [(0.30, 0.50, 70, 70), (0.50, 0.50, 70, 70), (0.70, 0.50, 70, 70)],
        'rarity_bias': ['common', 'uncommon', 'rare'],
    },
    'hideout': {
        'name': 'Bandit Hideout',
        'floor': (90, 70, 50),
        'wall':  (160, 130, 80),
        'accent': (220, 180, 80),
        'mobs': ['Bandit', 'Goblin', 'Wolf', 'Spider'],
        'chest_range': (4, 5),
        'obstacles': [(0.20, 0.55, 60, 60), (0.50, 0.55, 60, 60), (0.80, 0.55, 60, 60)],
        'rarity_bias': ['common', 'common', 'uncommon'],
    },
    'lair': {
        'name': "Dragon's Lair",
        'floor': (80, 30, 25),
        'wall':  (220, 90, 40),
        'accent': (255, 140, 60),
        'mobs': ['Dragon', 'Troll', 'Orc', 'Dark Elf'],
        'chest_range': (2, 3),
        'obstacles': [(0.30, 0.40, 100, 60), (0.65, 0.40, 100, 60), (0.45, 0.65, 110, 70)],
        'rarity_bias': ['uncommon', 'rare', 'rare'],
    },
}

def create_mob(mob_name, level, x, y):
    """Create an Enemy with stats scaled by its mob type multipliers."""
    m = MOB_TYPES[mob_name]
    e = Enemy(mob_name, level, x, y)
    e.max_hp    = max(1, int(e.max_hp    * m['hp']))
    e.hp        = e.max_hp
    e.damage    = max(1, int(e.damage    * m['dmg']))
    e.exp_reward= max(1, int(e.exp_reward* m['exp']))
    e.gold_reward=max(0, int(e.gold_reward*m['gold']))
    e.speed     = m['speed']
    e.aggro_range  = m['aggro']
    e.attack_range = m['range']
    e.lifesteal    = m.get('lifesteal', 0.0)   # optional: heals on hit
    return e

FINAL_BOSS_NAME = 'Solgareth, the Fallen'

def create_final_boss(x, y, level=25):
    """Create the unique final boss of the Celestial Realm."""
    e = Enemy(FINAL_BOSS_NAME, level, x, y)
    e.max_hp = 3600   # quadrupled — a true marathon boss fight
    e.hp = e.max_hp
    e.damage = 38
    e.exp_reward = 7500
    e.gold_reward = 7500
    e.speed = 1.6
    e.aggro_range = 320
    e.attack_range = 80
    e.is_final_boss = True
    e.dimension = 'celestial'
    return e

class Character:
    def __init__(self, name, char_class, x=0, y=0):
        self.name = name
        self.char_class = char_class
        self.level = 1
        self.x = x
        self.y = y
        self.speed = 3
        self.base_speed = 3   # invariant walking speed; sprint multiplies this temporarily
        self.facing = 1       # 1 = facing right, -1 = facing left (for sprite mirroring)
        self.atk_anim = 0     # frames left in an attack animation
        self.hurt_anim = 0    # frames left in a hit-reaction animation
        
        # Base stats based on class
        if char_class == "Warrior":
            self.max_hp = 120
            self.max_mp = 30
            self.strength = 16
            self.dexterity = 12
            self.intelligence = 8
            self.constitution = 15
        elif char_class == "Mage":
            self.max_hp = 70
            self.max_mp = 100
            self.strength = 8
            self.dexterity = 10
            self.intelligence = 16
            self.constitution = 10
        elif char_class == "Rogue":
            self.max_hp = 110
            self.max_mp = 70
            self.strength = 15
            self.dexterity = 20
            self.intelligence = 14
            self.constitution = 14
        
        self.hp = self.max_hp
        self.mp = self.max_mp

        # Stamina — Rogues get more, Warriors get medium, Mages get less
        if char_class == "Warrior":
            self.max_stamina = 120
            self.stamina_regen = 0.4
        elif char_class == "Mage":
            self.max_stamina = 80
            self.stamina_regen = 0.3
        elif char_class == "Rogue":
            self.max_stamina = 150
            self.stamina_regen = 0.6
        else:
            self.max_stamina = 100
            self.stamina_regen = 0.4
        self.stamina = float(self.max_stamina)

        self.exp = 0
        self.exp_to_next = 100
        self.gold = 50
        self.shards = 0  # Soul Shards: rare currency for temple blessings
        self.essence = 0  # Celestial Essence: dropped ONLY by the final boss
        self.pending_level_messages = []  # surfaced to the combat log by the game loop
        self.ultimate_cd = 0   # class ultimate move cooldown, unlocked at level 10
        self.skill_cd = 0      # class skill cooldown (Warrior Shield, Rogue Super Dash)
        self.max_inventory = 24   # carry capacity (slots; potions don't stack)
        self.alderine_heal_left = 0   # HP remaining to regenerate from Alderine
        self.alderine_tick_cd = 0     # frames until the next 1 HP regen tick
        self.active_quests = []       # references to quest dicts owned by NPC interactables
        
        # Equipment
        self.weapon = None
        self.armor = None
        self.inventory = []
        
        # Equipment slots for paper doll system
        self.equipped_weapon = None
        self.equipped_armor = None
        self.equipped_accessory = None
        self.equipped_helmet = None
        self.equipped_shield = None
        self.equipped_boots = None
        self.equipped_ring = None
        
        # Combat
        self.in_combat = False
        self.target = None
        
    def move(self, dx, dy):
        # Calculate new position
        new_x = self.x + dx * self.speed
        new_y = self.y + dy * self.speed
        
        # Store old position for rollback
        old_x, old_y = self.x, self.y
        
        # Try to move
        self.x = new_x
        self.y = new_y
        
        return old_x, old_y  # Return old position for collision checking
    
    def attack(self, target):
        try:
            if not target or not hasattr(target, 'take_damage'):
                return 0
            damage = random.randint(1, 6) + self.get_attack_bonus()
            self.atk_anim = ATK_ANIM_FRAMES
            target.take_damage(damage)
            w = self.equipped_weapon
            if w and getattr(w, 'lifesteal', 0) > 0:
                heal = int(damage * w.lifesteal)
                if heal:
                    self.hp = min(self.max_hp, self.hp + heal)
            return damage
        except Exception as e:
            print(f"Attack error: {e}")
            return 0
    
    def cast_spell(self, spell_name, target=None):
        try:
            if spell_name == "Heal" and self.mp >= 10:
                self.mp -= 10
                heal_amount = random.randint(10, 20) + self.intelligence // 2
                self.hp = min(self.max_hp, self.hp + heal_amount)
                return f"Healed for {heal_amount} HP"
            elif spell_name == "Fireball" and self.mp >= 15:
                self.mp -= 15
                damage = random.randint(8, 16) + self.intelligence // 2
                if target and hasattr(target, 'take_damage'):
                    target.take_damage(damage)
                return f"Fireball deals {damage} damage"
            return "Not enough MP"
        except Exception as e:
            print(f"Cast spell error: {e}")
            return "Spell failed"
    
    def take_damage(self, damage):
        try:
            if damage > 0:
                self.hp -= damage
                self.hurt_anim = HURT_ANIM_FRAMES
            if self.hp <= 0:
                self.hp = 0
        except Exception as e:
            print(f"Take damage error: {e}")
            self.hp = 0
    
    def get_attack_bonus(self):
        try:
            str_bonus = self.strength // 2 - 5
            if getattr(self, 'equipped_ring', None):
                str_bonus += self.equipped_ring.bonus
            bonus = str_bonus
            if self.equipped_weapon:
                bonus += self.equipped_weapon.damage
            elif self.weapon:  # Fallback to old system
                bonus += self.weapon.damage
            return max(0, bonus)
        except:
            return 0

    def get_defense(self):
        try:
            defense = self.constitution // 2 - 5
            if self.equipped_armor:
                defense += self.equipped_armor.defense
            elif self.armor:  # Fallback to old system
                defense += self.armor.defense
            for slot_attr in ('equipped_helmet', 'equipped_shield', 'equipped_boots'):
                gear = getattr(self, slot_attr, None)
                if gear:
                    defense += gear.defense
            return max(0, defense)
        except:
            return 0

    def equip_item(self, item):
        """Equip an item and return the previously equipped item"""
        # Map item class to its slot attribute. Check the most specific classes first
        # so subclassing in the future doesn't mis-route.
        slot_map = [
            (Helmet, 'equipped_helmet'),
            (Shield, 'equipped_shield'),
            (Boots,  'equipped_boots'),
            (Ring,   'equipped_ring'),
            (Weapon, 'equipped_weapon'),
            (Armor,  'equipped_armor'),
        ]
        for cls, attr in slot_map:
            if isinstance(item, cls):
                old_item = getattr(self, attr, None)
                setattr(self, attr, item)
                if attr == 'equipped_weapon':
                    self.weapon = item  # legacy compatibility
                elif attr == 'equipped_armor':
                    self.armor = item   # legacy compatibility
                return old_item
        return None

    def unequip_item(self, slot):
        """Unequip an item from a specific slot"""
        slot_attrs = {
            "weapon":    'equipped_weapon',
            "armor":     'equipped_armor',
            "helmet":    'equipped_helmet',
            "shield":    'equipped_shield',
            "boots":     'equipped_boots',
            "ring":      'equipped_ring',
            "accessory": 'equipped_accessory',
        }
        attr = slot_attrs.get(slot)
        if not attr:
            return None
        item = getattr(self, attr, None)
        setattr(self, attr, None)
        if slot == "weapon":
            self.weapon = None
        elif slot == "armor":
            self.armor = None
        return item
    
    def gain_exp(self, amount):
        self.exp += amount
        # Loop in case a big EXP reward triggers multiple level-ups at once
        while self.exp >= self.exp_to_next:
            self.level_up()

    def level_up(self):
        self.level += 1
        self.exp -= self.exp_to_next
        self.exp_to_next = int(self.exp_to_next * 1.5)

        # Permanent HP / MP / Stamina growth
        hp_gain = random.randint(8, 14)
        mp_gain = random.randint(4, 9)
        sta_gain = 5
        self.max_hp += hp_gain
        self.max_mp += mp_gain
        self.max_stamina += sta_gain
        self.hp = self.max_hp
        self.mp = self.max_mp
        self.stamina = float(self.max_stamina)

        # Class-biased ability-score gains (permanent)
        gains = {'STR': 0, 'DEX': 0, 'INT': 0, 'CON': 0}
        if self.char_class == "Warrior":
            gains['STR'] += 2; gains['CON'] += 1
            gains['DEX' if self.level % 2 == 0 else 'INT'] += 1
        elif self.char_class == "Mage":
            gains['INT'] += 2; gains['DEX'] += 1
            gains['CON' if self.level % 2 == 0 else 'STR'] += 1
        elif self.char_class == "Rogue":
            gains['DEX'] += 2; gains['STR'] += 1
            gains['CON' if self.level % 2 == 0 else 'INT'] += 1
        else:
            for k in gains:
                gains[k] += 1

        self.strength     += gains['STR']
        self.dexterity    += gains['DEX']
        self.intelligence += gains['INT']
        self.constitution += gains['CON']

        parts = [f"+{hp_gain} HP", f"+{mp_gain} MP", f"+{sta_gain} SP"]
        for k in ('STR', 'DEX', 'INT', 'CON'):
            if gains[k]:
                parts.append(f"+{gains[k]} {k}")
        if not hasattr(self, 'pending_level_messages'):
            self.pending_level_messages = []
        self.pending_level_messages.append(f"Level {self.level}! " + ", ".join(parts))

class Enemy:
    def __init__(self, name, level, x, y):
        self.name = name
        self.level = level
        self.x = x
        self.y = y
        self.max_hp = 30 + level * 10
        self.hp = self.max_hp
        self.damage = 5 + level * 2
        self.exp_reward = 20 + level * 10
        self.gold_reward = 10 + level * 5
        self.alive = True
        self.dimension = 'overworld'   # which dimension this enemy belongs to
        self.is_final_boss = False
        self.lifesteal = 0.0           # fraction of damage healed on hit (Vampires use this)

        # AI variables
        self.target = None
        self.aggro_range = 100
        self.attack_range = 40
        self.speed = 1
        self.facing = 1                # 1 = facing right, -1 = facing left
        self.atk_anim = 0              # frames left in an attack animation
        self.hurt_anim = 0             # frames left in a hit-reaction animation
    
    def update(self, player):
        if not self.alive:
            return
            
        # Check if player is in aggro range
        distance = math.sqrt((self.x - player.x)**2 + (self.y - player.y)**2)
        
        if distance <= self.aggro_range:
            self.target = player
            # Face the player whenever we're aware of them
            if abs(player.x - self.x) > 1:
                self.facing = 1 if player.x > self.x else -1

            # Move towards player
            if distance > self.attack_range:
                dx = player.x - self.x
                dy = player.y - self.y
                length = math.sqrt(dx**2 + dy**2)
                if length > 0:
                    dx /= length
                    dy /= length
                    self.x += dx * self.speed
                    self.y += dy * self.speed
    
    def attack(self, target):
        try:
            if not target or not hasattr(target, 'take_damage'):
                return 0
            damage = random.randint(1, self.damage)
            self.atk_anim = ATK_ANIM_FRAMES
            target.take_damage(damage)
            # Lifesteal: vampires (or any enemy with .lifesteal > 0) heal on hit
            if getattr(self, 'lifesteal', 0) > 0:
                heal = int(damage * self.lifesteal)
                if heal:
                    self.hp = min(self.max_hp, self.hp + heal)
            return damage
        except Exception as e:
            print(f"Enemy attack error: {e}")
            return 0

    def take_damage(self, damage):
        try:
            if damage > 0:
                self.hp -= damage
                self.hurt_anim = HURT_ANIM_FRAMES
            if self.hp <= 0:
                self.hp = 0
                self.alive = False
        except Exception as e:
            print(f"Enemy take damage error: {e}")
            self.hp = 0
            self.alive = False

class Item:
    def __init__(self, name, item_type, value):
        self.name = name
        self.type = item_type
        self.value = value

class Weapon(Item):
    def __init__(self, name, damage, value, lifesteal=0):
        super().__init__(name, "weapon", value)
        self.damage = damage
        self.lifesteal = lifesteal

class Backpack(Item):
    """Consumable bag-upgrade: using one permanently raises max_inventory by `bonus`."""
    def __init__(self, name, bonus, value):
        super().__init__(name, "backpack", value)
        self.bonus = bonus

class Armor(Item):
    def __init__(self, name, defense, value):
        super().__init__(name, "armor", value)
        self.defense = defense

class Helmet(Item):
    def __init__(self, name, defense, value):
        super().__init__(name, "helmet", value)
        self.defense = defense

class Shield(Item):
    def __init__(self, name, defense, value):
        super().__init__(name, "shield", value)
        self.defense = defense

class Boots(Item):
    def __init__(self, name, defense, value):
        super().__init__(name, "boots", value)
        self.defense = defense

class Ring(Item):
    def __init__(self, name, bonus, value):
        super().__init__(name, "ring", value)
        self.bonus = bonus  # +N strength while worn

class BattleEffect:
    def __init__(self, x, y, effect_type, duration=60):
        self.x = x
        self.y = y
        self.effect_type = effect_type
        self.duration = duration
        self.timer = 0
        self.particles = []
        
        if effect_type == "sword_slash":
            # Create slash particles
            for i in range(8):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(2, 6)
                self.particles.append({
                    'x': x, 'y': y,
                    'vx': math.cos(angle) * speed,
                    'vy': math.sin(angle) * speed,
                    'life': random.randint(20, 40),
                    'size': random.randint(2, 4)
                })
        
        elif effect_type == "fireball":
            # Create fire particles
            for i in range(15):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(1, 4)
                self.particles.append({
                    'x': x, 'y': y,
                    'vx': math.cos(angle) * speed,
                    'vy': math.sin(angle) * speed,
                    'life': random.randint(30, 60),
                    'size': random.randint(3, 8)
                })
        
        elif effect_type == "heal":
            # Create healing sparkles
            for i in range(12):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(0.5, 2)
                self.particles.append({
                    'x': x + random.randint(-20, 20), 
                    'y': y + random.randint(-20, 20),
                    'vx': math.cos(angle) * speed,
                    'vy': math.sin(angle) * speed - 1,  # Float upward
                    'life': random.randint(40, 80),
                    'size': random.randint(2, 5)
                })
        
        elif effect_type == "hit":
            # Create impact particles
            for i in range(6):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(3, 8)
                self.particles.append({
                    'x': x, 'y': y,
                    'vx': math.cos(angle) * speed,
                    'vy': math.sin(angle) * speed,
                    'life': random.randint(15, 30),
                    'size': random.randint(2, 3)
                })
        
        elif effect_type == "mp_regen":
            # Create small blue sparkles for MP regeneration
            for i in range(3):
                angle = random.uniform(0, 2 * math.pi)
                speed = random.uniform(0.5, 1.5)
                self.particles.append({
                    'x': x + random.randint(-10, 10), 
                    'y': y + random.randint(-10, 10),
                    'vx': math.cos(angle) * speed,
                    'vy': math.sin(angle) * speed - 0.5,  # Float upward slowly
                    'life': random.randint(30, 50),
                    'size': random.randint(1, 3)
                })
    
    def update(self):
        self.timer += 1
        
        # Update particles
        for particle in self.particles[:]:
            particle['x'] += particle['vx']
            particle['y'] += particle['vy']
            particle['life'] -= 1
            particle['vx'] *= 0.98  # Friction
            particle['vy'] *= 0.98
            
            if particle['life'] <= 0:
                self.particles.remove(particle)
        
        return self.timer < self.duration or len(self.particles) > 0
    
    def draw(self, screen):
        for particle in self.particles:
            alpha = particle['life'] / 60.0
            size = int(particle['size'] * alpha)
            if size > 0:
                if self.effect_type == "sword_slash":
                    color = (255, 255, 255)
                elif self.effect_type == "fireball":
                    # Fire colors - red to yellow
                    red = 255
                    green = int(255 * alpha)
                    blue = 0
                    color = (red, green, blue)
                elif self.effect_type == "heal":
                    # Green healing sparkles
                    color = (0, 255, 0)
                elif self.effect_type == "hit":
                    color = (255, 100, 100)
                elif self.effect_type == "mp_regen":
                    # Light blue sparkles for MP regeneration
                    color = (100, 150, 255)
                
                pygame.draw.circle(screen, color, (int(particle['x']), int(particle['y'])), size)

class Game:
    def __init__(self):
        self.fullscreen = False
        self.current_slot = 1   # which save slot F5 writes to
        self.save_select_index = 0   # highlighted slot on the save-select screen
        # In the browser the page scales the canvas itself; a resizable window there gets stretched
        flags = 0 if IS_WEB else pygame.SCALED | pygame.RESIZABLE
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT), flags)
        if IS_WEB:  # the page sized the canvas before it had pixels; fit it again now it is 1024x768
            import platform as web
            web.window.window_resize()
        pygame.display.set_caption("D&D RPG Adventure")
        self.clock = pygame.time.Clock()
        self.font = pygame.font.Font(None, 36)
        self.small_font = pygame.font.Font(None, 24)
        
        self.state = "menu"  # menu, char_creation, playing, combat, inventory
        self.player = None
        self.enemies = []
        self.items = []
        self.current_enemy = None
        
        # Menu variables
        self.selected_class = 0
        self.classes = ["Warrior", "Mage", "Rogue"]
        self.player_name = ""
        self.selected_difficulty = 1  # Default: Normal
        self.difficulty = DIFFICULTIES[1]
        
        # Battle effects
        self.battle_effects = []
        self.screen_shake = 0
        self.screen_shake_intensity = 0
        self.flash_screen = 0
        self.flash_color = WHITE
        self.combat_log = []
        self.log_timer = 0
        
        # MP regeneration timer (60 FPS = 60 frames per second)
        self.mp_regen_timer = 0
        self.mp_regen_rate = 60  # Regenerate 1 MP every 60 frames (1 second)
        
        # Terrain and environment
        self.terrain_tiles = []
        self.buildings = []
        self.vegetation = []
        self.collision_map = {}  # For collision detection
        self.interactable_objects = []  # Initialize this here!
        self.current_building = None  # Track if player is inside a building
        
        # Infinite map system
        self.chunk_size = 1024
        self.current_chunk_x = 0
        self.current_chunk_y = 0
        # Dimension system
        self.current_dimension = 'overworld'
        self.all_dimension_chunks = {'overworld': {}, 'nether': {}, 'void': {}, 'celestial': {}}
        self.all_loaded_chunks   = {'overworld': set(), 'nether': set(), 'void': set(), 'celestial': set()}
        self.dimension_player_pos = {}  # save/restore position per dimension
        self.boss_spawned = False  # final boss spawns once, in the Celestial Realm
        self.in_boss_fight = False  # locked battlefield — no escape from the final boss
        self.world_chunks  = self.all_dimension_chunks['overworld']
        self.loaded_chunks = self.all_loaded_chunks['overworld']
        self.world_seed = random.randint(0, 2**31 - 1)  # Seed for deterministic noise terrain
        self.camera_x = 0  # Camera offset for infinite scrolling
        self.camera_y = 0
        
        # Shop system
        self.shop_inventory = []
        self.selected_item_index = 0
        self.shop_restock_timer = 0
        self.shop_restock_interval = 2700   # partial restock every ~45s (at 60 FPS)
        self._was_game_over = False         # detect the moment of death to restock
        
        # Inventory system
        self.inventory_selected_index = 0
        self.inventory_mode = "view"  # "view", "equip"
        self.inventory_return_state = "playing"  # where to go back to when closing inventory
        
        # Generate initial world
        self.generate_world()
    
    def generate_world(self):
        # Generate terrain tiles for the initial area
        self.generate_terrain()
        
        # Generate buildings for the initial area
        self.generate_buildings()
        
        # Generate vegetation for the initial area
        self.generate_vegetation()
        
        # Enemies are spawned per-chunk; starting town chunk has no enemies
        
        # Create some items
        weapons = [
            Weapon("Iron Sword", 3, 50),
            Weapon("Steel Axe", 4, 80),
            Weapon("Magic Staff", 2, 100)
        ]
        armors = [
            Armor("Leather Armor", 2, 40),
            Armor("Chain Mail", 3, 70),
            Armor("Plate Armor", 5, 120)
        ]
        
        for weapon in weapons:
            self.items.append(weapon)
        for armor in armors:
            self.items.append(armor)
        
        # Create dropped item list for potential enemy drops
        self.drop_table = [
            # Common drops (weapons)
            Weapon("Rusty Sword", 2, 25),
            Weapon("Wooden Club", 1, 15),
            Weapon("Bronze Dagger", 2, 30),
            Weapon("Goblin Spear", 3, 40),
            # Common drops (armor)
            Armor("Torn Leather", 1, 20),
            Armor("Goblin Hide", 2, 35),
            # Rare drops
            Weapon("Enchanted Blade", 5, 150),
            Armor("Magic Cloak", 4, 100),
            # Lifesteal weapons — heal a fraction of damage dealt
            Weapon("Vampire Fang",       4, 220, lifesteal=0.20),
            Weapon("Bloodletter",        6, 320, lifesteal=0.25),
            Weapon("Crimson Greatsword", 8, 480, lifesteal=0.30),
            # Helmets, shields, boots, rings — populate the new slots
            Helmet("Leather Cap", 1, 25),
            Helmet("Iron Helm",   3, 70),
            Shield("Wooden Shield", 2, 45),
            Shield("Iron Shield",   4, 110),
            Boots("Cloth Boots", 1, 20),
            Boots("Iron Boots",  3, 65),
            Ring("Power Ring",  2, 90),
            Ring("Mighty Band", 4, 220),
            # Consumables (represented as items for now)
            Item("Health Potion", "consumable", 50),
            Item("Mana Potion", "consumable", 40),
            Item("Alderine", "consumable", 70),  # regen HoT — heals 80 HP slowly
            Item("Gold Coin", "currency", 25),
            # Backpacks — use one to permanently expand your inventory
            Backpack("Small Backpack", 4, 80),
            Backpack("Traveler's Pack", 8, 200),
        ]
        
        # Initialize shop inventory
        self.initialize_shop_inventory()
        
        # Note: We don't build collision map here anymore since we use infinite world
    
    def _hash01(self, x, y, seed):
        """Deterministic pseudo-random value in [0,1) from integer lattice coords."""
        n = (int(x) * 374761393 + int(y) * 668265263 + int(seed) * 2654435761) & 0xFFFFFFFF
        n = ((n ^ (n >> 13)) * 1274126177) & 0xFFFFFFFF
        n = (n ^ (n >> 16)) & 0xFFFFFFFF
        return n / 4294967295.0

    def _value_noise(self, x, y, seed):
        """Smooth value noise: interpolate hashed lattice corners with smoothstep."""
        x0 = math.floor(x)
        y0 = math.floor(y)
        fx = x - x0
        fy = y - y0
        u = fx * fx * (3 - 2 * fx)
        v = fy * fy * (3 - 2 * fy)
        n00 = self._hash01(x0, y0, seed)
        n10 = self._hash01(x0 + 1, y0, seed)
        n01 = self._hash01(x0, y0 + 1, seed)
        n11 = self._hash01(x0 + 1, y0 + 1, seed)
        nx0 = n00 + u * (n10 - n00)
        nx1 = n01 + u * (n11 - n01)
        return nx0 + v * (nx1 - nx0)

    def world_noise(self, wx, wy, scale, octaves=4, seed_offset=0):
        """Multi-octave fractal value noise in [0,1) for world coordinates.

        Coherent across chunk boundaries and deterministic for a given world_seed,
        so terrain looks natural and varied rather than repeating sine-wave bands.
        """
        total = 0.0
        amplitude = 1.0
        max_amp = 0.0
        freq = scale
        for o in range(octaves):
            total += self._value_noise(wx * freq, wy * freq,
                                       self.world_seed + seed_offset + o * 1013) * amplitude
            max_amp += amplitude
            amplitude *= 0.5
            freq *= 2.0
        return total / max_amp

    def classify_terrain(self, world_x, world_y):
        """Pick a terrain type from layered elevation + moisture + temperature noise."""
        elevation = self.world_noise(world_x, world_y, 0.0008, octaves=5, seed_offset=0)
        moisture = self.world_noise(world_x, world_y, 0.0013, octaves=4, seed_offset=7919)
        # Temperature — coarser than moisture so biomes come in latitude-like bands
        temperature = self.world_noise(world_x, world_y, 0.0005, octaves=3, seed_offset=13337)

        if elevation < 0.32:
            return "water"
        if elevation > 0.74:
            # Cold peaks turn to snow; warm peaks stay stony
            return "snow" if temperature < 0.4 else "mountain"
        if elevation > 0.60:
            return "hills"
        # Mid elevations: (moisture, temperature) grid → distinct biomes
        if moisture > 0.62 and elevation < 0.42:
            return "swamp"        # wet + low → boggy
        if moisture < 0.35 and temperature > 0.55:
            return "desert"       # dry + hot → sand
        if temperature < 0.30:
            return "snow"         # cold flatlands → snowy plain
        if moisture > 0.55:
            return "forest"
        return "grass"

    def generate_terrain(self):
        """Generate different terrain types across the map"""
        tile_size = 32

        # Create a base terrain map, storing elevation + slope shading per tile so
        # relief-shaded biomes (mountains) render the same way as in streamed chunks.
        def elev_at(wx, wy):
            return self.world_noise(wx, wy, 0.0008, octaves=5, seed_offset=0)

        for y in range(0, SCREEN_HEIGHT, tile_size):
            for x in range(0, SCREEN_WIDTH, tile_size):
                terrain_type = self.classify_terrain(x, y)
                elev = elev_at(x, y)
                dzdx = elev_at(x + tile_size, y) - elev_at(x - tile_size, y)
                dzdy = elev_at(x, y + tile_size) - elev_at(x, y - tile_size)
                lit = max(0.0, min(1.0, 0.5 + (-dzdx - dzdy) * 6.0))

                self.terrain_tiles.append({
                    'x': x, 'y': y, 'type': terrain_type, 'size': tile_size,
                    'elev': elev, 'lit': lit,
                })
    
    def generate_buildings(self):
        """Generate various buildings and structures"""
        building_types = [
            {'type': 'house', 'width': 48, 'height': 48, 'color': BROWN},
            {'type': 'tower', 'width': 32, 'height': 64, 'color': GRAY},
            {'type': 'shop', 'width': 56, 'height': 40, 'color': (139, 90, 43)},
            {'type': 'inn', 'width': 64, 'height': 48, 'color': (160, 82, 45)},
            {'type': 'blacksmith', 'width': 52, 'height': 44, 'color': (105, 105, 105)},
            {'type': 'temple', 'width': 60, 'height': 52, 'color': (218, 165, 32)},
            {'type': 'library', 'width': 58, 'height': 46, 'color': (139, 69, 19)}
        ]
        
        # Ensure we have at least 3 buildings and make them all enterable
        min_buildings = 6
        max_buildings = 10
        num_buildings = random.randint(min_buildings, max_buildings)
        
        # First, add one of each essential building type
        essential_buildings = ['house', 'shop', 'inn', 'blacksmith', 'temple', 'library']
        
        for building_type in essential_buildings:
            building_data = next(b for b in building_types if b['type'] == building_type)
            building = building_data.copy()
            
            # Find a good location
            attempts = 0
            while attempts < 100:  # More attempts for better placement
                x = random.randint(building['width']//2, SCREEN_WIDTH - building['width'])
                y = random.randint(building['height']//2, SCREEN_HEIGHT - building['height'])
                
                # Check if location is clear of other buildings (minimum 60 pixel spacing)
                clear = True
                for existing in self.buildings:
                    distance = math.sqrt((x - existing['x'])**2 + (y - existing['y'])**2)
                    if distance < 80:  # Minimum distance between buildings
                        clear = False
                        break
                
                if clear:
                    building.update({'x': x, 'y': y})
                    self.buildings.append(building)
                    print(f"Created {building_type} at ({x}, {y})")
                    break
                
                attempts += 1
        
        # Add additional random buildings if needed
        remaining_slots = num_buildings - len(self.buildings)
        for _ in range(remaining_slots):
            building = random.choice(building_types).copy()
            
            attempts = 0
            while attempts < 50:
                x = random.randint(building['width'], SCREEN_WIDTH - building['width'])
                y = random.randint(building['height'], SCREEN_HEIGHT - building['height'])
                
                clear = True
                for existing in self.buildings:
                    distance = math.sqrt((x - existing['x'])**2 + (y - existing['y'])**2)
                    if distance < 80:
                        clear = False
                        break
                
                if clear:
                    building.update({'x': x, 'y': y})
                    self.buildings.append(building)
                    break
                
                attempts += 1
    
    def generate_vegetation(self):
        """Generate trees, bushes, and other vegetation"""
        vegetation_types = [
            {'type': 'tree', 'size': 16, 'color': DARK_GREEN},
            {'type': 'bush', 'size': 8, 'color': (34, 139, 34)},
            {'type': 'flower', 'size': 4, 'color': (255, 20, 147)},
            {'type': 'rock', 'size': 12, 'color': GRAY}
        ]
        
        # Generate 50-80 vegetation pieces
        num_vegetation = random.randint(50, 80)
        
        for _ in range(num_vegetation):
            veg = random.choice(vegetation_types).copy()
            
            # Random placement
            x = random.randint(veg['size'], SCREEN_WIDTH - veg['size'])
            y = random.randint(veg['size'], SCREEN_HEIGHT - veg['size'])
            
            # Check if location doesn't conflict with buildings
            clear = True
            for building in self.buildings:
                if (x > building['x'] - veg['size'] and x < building['x'] + building['width'] + veg['size'] and
                    y > building['y'] - veg['size'] and y < building['y'] + building['height'] + veg['size']):
                    clear = False
                    break
            
            if clear:
                veg.update({'x': x, 'y': y})
                self.vegetation.append(veg)
    
    def build_collision_map(self):
        """Build a collision map for movement validation"""
        self.collision_map = {}
        
        # Mark impassable terrain
        for tile in self.terrain_tiles:
            if tile['type'] in ['water', 'mountain']:
                for px in range(tile['x'], tile['x'] + tile['size'], 4):
                    for py in range(tile['y'], tile['y'] + tile['size'], 4):
                        self.collision_map[(px, py)] = 'impassable'
        
        # Mark ALL buildings as solid (but keep doors clear)
        for building in self.buildings:
            for px in range(building['x'], building['x'] + building['width'], 4):
                for py in range(building['y'], building['y'] + building['height'], 4):
                    # Create larger door area that's passable for ALL building types
                    door_x_start = building['x'] + building['width']//2 - 16
                    door_x_end = building['x'] + building['width']//2 + 16
                    door_y_start = building['y'] + building['height'] - 24
                    door_y_end = building['y'] + building['height']
                    
                    # Don't mark door area as collision
                    if not (door_x_start <= px <= door_x_end and door_y_start <= py <= door_y_end):
                        self.collision_map[(px, py)] = 'building'
        
        # Mark large trees and rocks as obstacles
        for veg in self.vegetation:
            if veg['type'] in ['tree', 'rock'] and veg['size'] >= 12:
                for px in range(veg['x'] - veg['size']//2, veg['x'] + veg['size']//2, 4):
                    for py in range(veg['y'] - veg['size']//2, veg['y'] + veg['size']//2, 4):
                        self.collision_map[(px, py)] = 'obstacle'
    
    def create_interactables(self):
        """Create interactable objects like doors, chests, NPCs"""
        print(f"=== create_interactables called ===")
        print(f"Number of buildings: {len(self.buildings)}")
        print(f"Buildings list: {[b['type'] for b in self.buildings]}")
        
        # Clear existing interactables and recreate
        self.interactable_objects = []
        print("Cleared and recreated interactable_objects list")
        
        # Make ALL buildings enterable
        for i, building in enumerate(self.buildings):
            print(f"Processing building {i}: {building['type']} at ({building['x']}, {building['y']})")
            
            # Add door interaction for ALL building types
            door_x = building['x'] + building['width']//2 - 8  # Center horizontally
            door_y = building['y'] + building['height'] - 20    # At the bottom front
            
            door_obj = {
                'type': 'door',
                'x': door_x,
                'y': door_y,
                'width': 16,
                'height': 20,
                'building_type': building['type'],
                'building_data': building,
                'action': 'enter_building'
            }
            
            self.interactable_objects.append(door_obj)
            print(f"  -> Created door at ({door_x}, {door_y}) for {building['type']}")
            print(f"  -> Current interactables count: {len(self.interactable_objects)}")
        
        # Add some treasure chests in random locations
        print("Creating treasure chests...")
        for chest_num in range(3):  # Reduced to 3 for simpler debugging
            attempts = 0
            while attempts < 20:
                x = random.randint(100, SCREEN_WIDTH - 100)
                y = random.randint(100, SCREEN_HEIGHT - 100)
                
                # Simple check - just avoid building centers
                clear = True
                for building in self.buildings:
                    if abs(x - building['x']) < 50 and abs(y - building['y']) < 50:
                        clear = False
                        break
                
                if clear:
                    chest_obj = {
                        'type': 'chest',
                        'x': x,
                        'y': y,
                        'width': 16,
                        'height': 12,
                        'opened': False,
                        'action': 'open_chest'
                    }
                    self.interactable_objects.append(chest_obj)
                    print(f"  -> Created chest {chest_num} at ({x}, {y})")
                    break
                attempts += 1
            
            if attempts >= 20:
                print(f"  -> Failed to place chest {chest_num} after 20 attempts")
        
        print(f"=== create_interactables finished ===")
        print(f"Total interactable objects created: {len(self.interactable_objects)}")
        
        # Double-check by printing all objects
        for i, obj in enumerate(self.interactable_objects):
            print(f"  Final object {i}: {obj['type']} at ({obj['x']}, {obj['y']}) - {obj.get('building_type', 'N/A')}")
    
    def initialize_shop_inventory(self):
        """Initialize shop with items for sale"""
        self.shop_inventory = [
            # Weapons for sale
            {'item': Weapon("Iron Sword", 4, 80), 'quantity': 3},
            {'item': Weapon("Steel Axe", 5, 120), 'quantity': 2},
            {'item': Weapon("Magic Staff", 3, 150), 'quantity': 1},
            {'item': Weapon("Silver Dagger", 3, 60), 'quantity': 4},
            # Lifesteal weapons — pricier, restocked sparingly
            {'item': Weapon("Vampire Fang", 4, 260, lifesteal=0.20), 'quantity': 1},
            {'item': Weapon("Bloodletter",  6, 360, lifesteal=0.25), 'quantity': 1},
            
            # Armor for sale
            {'item': Armor("Chain Mail", 4, 100), 'quantity': 2},
            {'item': Armor("Plate Armor", 6, 200), 'quantity': 1},
            {'item': Armor("Leather Vest", 2, 50), 'quantity': 3},
            {'item': Armor("Wizard Robe", 3, 120), 'quantity': 2},
            # Helmets / shields / boots / rings for the new slots
            {'item': Helmet("Leather Cap", 1, 30), 'quantity': 2},
            {'item': Helmet("Iron Helm",   3, 90), 'quantity': 1},
            {'item': Shield("Wooden Shield", 2, 55), 'quantity': 2},
            {'item': Shield("Iron Shield",   4, 130), 'quantity': 1},
            {'item': Boots("Cloth Boots", 1, 25), 'quantity': 2},
            {'item': Boots("Iron Boots",  3, 75), 'quantity': 1},
            {'item': Ring("Power Ring", 2, 110), 'quantity': 1},
            
            # Consumables
            {'item': Item("Health Potion", "consumable", 30), 'quantity': 10},
            {'item': Item("Mana Potion", "consumable", 25), 'quantity': 8},
            {'item': Item("Stamina Potion", "consumable", 20), 'quantity': 5},
            {'item': Item("Alderine", "consumable", 60), 'quantity': 4},
            # Backpacks — limited stock, used to permanently expand inventory
            {'item': Backpack("Small Backpack", 4, 100), 'quantity': 2},
            {'item': Backpack("Traveler's Pack", 8, 260), 'quantity': 1},
        ]
        # Remember each item's full stock so we can restock toward it
        for entry in self.shop_inventory:
            entry['max'] = entry['quantity']

    def restock_shop(self, full=False):
        """Refill shop stock. full=True resets to max; otherwise tops up partway."""
        if not getattr(self, 'shop_inventory', None):
            self.initialize_shop_inventory()
            return
        for entry in self.shop_inventory:
            cap = entry.get('max', entry['quantity'])
            if full:
                entry['quantity'] = cap
            else:
                entry['quantity'] = min(cap, entry['quantity'] + max(1, cap // 2))

    def is_position_blocked(self, x, y, width=32, height=32):
        """Check if a position is blocked by terrain or objects (for initial world only)"""
        # This is only used for the initial world area, not the infinite world
        # For infinite world, use is_position_blocked_infinite
        return False
    
    def check_interactions(self):
        """Check for nearby interactable objects"""
        if not hasattr(self, 'player') or not self.player:
            print("No player found in check_interactions")
            return None
            
        if not hasattr(self, 'interactable_objects') or not self.interactable_objects:
            print("No interactable_objects found in check_interactions")
            return None
            
        player_center_x = self.player.x + 16
        player_center_y = self.player.y + 16
        
        print(f"check_interactions: Player at ({player_center_x}, {player_center_y})")
        print(f"check_interactions: Checking {len(self.interactable_objects)} objects")
        
        closest_object = None
        closest_distance = float('inf')
        
        for i, obj in enumerate(self.interactable_objects):
            obj_center_x = obj['x'] + obj['width'] // 2
            obj_center_y = obj['y'] + obj['height'] // 2
            
            # Calculate distance from player center to object center
            distance = math.sqrt((player_center_x - obj_center_x)**2 + (player_center_y - obj_center_y)**2)
            
            # Different interaction ranges for different objects
            if obj['type'] == 'door':
                interaction_range = 50  # Larger range for doors
            else:
                interaction_range = 30  # Smaller range for chests
            
            print(f"  Object {i}: {obj['type']} at ({obj_center_x}, {obj_center_y}) - Distance: {distance:.1f}, Range: {interaction_range}")
            
            if distance <= interaction_range:
                print(f"    -> Within range!")
                if distance < closest_distance:
                    closest_distance = distance
                    closest_object = obj
                    print(f"    -> New closest object!")
        
        if closest_object:
            print(f"Found closest object: {closest_object['type']} at distance {closest_distance:.1f}")
            if closest_object['action'] == 'enter_building':
                self.add_combat_log(f"Press E to enter {closest_object['building_type']}")
                return closest_object
            elif closest_object['action'] == 'open_chest' and not closest_object['opened']:
                self.add_combat_log("Press E to open chest")
                return closest_object
        else:
            print("No objects within interaction range")
        
        return None
    
    def interact_with_object(self, obj):
        """Handle interaction with an object"""
        if obj['action'] == 'enter_building':
            self.enter_building(obj['building_data'])
        elif obj['action'] == 'open_chest' and not obj['opened']:
            self.open_chest(obj)
        elif obj['action'] == 'use_portal':
            self.switch_dimension(obj['destination'])
        elif obj['action'] == 'enter_dungeon':
            # Each dungeon entrance can only be used 3 times before it collapses
            entries = obj.get('entries_used', 0)
            if entries >= 3:
                self.add_combat_log("This dungeon has collapsed and can no longer be entered.")
                return
            obj['entries_used'] = entries + 1
            remaining = 3 - obj['entries_used']
            if remaining == 0:
                self.add_combat_log("Your last expedition into this dungeon.")
            elif remaining == 1:
                self.add_combat_log("You have 1 more entry into this dungeon.")
            # Auto-save while still in the playing state, before the dungeon takes over
            self.save_game(auto=True, label="Auto-saved before dungeon.")
            self.start_dungeon(obj)
        elif obj['action'] == 'use_beacon':
            self.use_beacon(obj)
        elif obj['action'] == 'talk_npc':
            self.talk_to_npc(obj)
        elif obj['action'] == 'use_altar':
            self.resurrect_boss(obj)
    
    def talk_to_npc(self, npc):
        """Run the quest state machine for an NPC: offer → progress → reward."""
        quest = npc.get('quest', {})
        name = npc.get('name', 'NPC')
        state = quest.get('state', 'not_started')
        if not hasattr(self.player, 'active_quests') or self.player.active_quests is None:
            self.player.active_quests = []

        if state == 'not_started':
            quest['state'] = 'active'
            if quest not in self.player.active_quests:
                self.player.active_quests.append(quest)
            self.add_combat_log(f"{name}: {quest['desc']}")
            self.add_combat_log(f"Quest accepted: {quest['name']}")
        elif state == 'active':
            qtype = quest.get('type')
            if qtype in ('kill', 'shipwreck'):
                self.add_combat_log(f"{name}: Progress {quest['progress']}/{quest['target']}.")
            elif qtype == 'reach_dim':
                self.add_combat_log(f"{name}: Travel to the {quest['target']}.")
            else:
                self.add_combat_log(f"{name}: keep at it.")
        elif state == 'complete':
            gold = quest.get('reward_gold', 0)
            exp_r = quest.get('reward_exp', 0)
            shards = quest.get('reward_shards', 0)
            if gold:
                self.player.gold += gold
            if shards:
                self.player.shards += shards
            if exp_r:
                self.player.gain_exp(exp_r)
            if quest in self.player.active_quests:
                self.player.active_quests.remove(quest)
            parts = []
            if gold:   parts.append(f"+{gold} gold")
            if exp_r:  parts.append(f"+{exp_r} EXP")
            if shards: parts.append(f"+{shards} shards")
            self.add_combat_log(f"{name}: Quest complete! {', '.join(parts) or 'thanks'}")
            self.add_screen_flash(YELLOW, 12)
            # Refresh the NPC's quest at a harder tier so they're a repeat source
            self._regenerate_quest(npc)
            self.add_combat_log(f"{name}: I have another job for you — come back when you're ready.")
        else:
            self.add_combat_log(f"{name}: Thanks for your help, traveler.")

    def _roman(self, n):
        if n <= 0:
            return ""
        table = [(1000, 'M'), (900, 'CM'), (500, 'D'), (400, 'CD'),
                 (100, 'C'), (90, 'XC'), (50, 'L'), (40, 'XL'),
                 (10, 'X'), (9, 'IX'), (5, 'V'), (4, 'IV'), (1, 'I')]
        out = []
        for val, sym in table:
            while n >= val:
                out.append(sym)
                n -= val
        return ''.join(out)

    def _regenerate_quest(self, npc):
        """Turn the NPC's quest into a harder, higher-tier version with bigger rewards."""
        q = npc.get('quest')
        if not q:
            return
        # Make sure base values exist (older saves may lack them)
        if 'base_name' not in q:
            q['base_name'] = q.get('name', 'Quest')
        if 'base_target' not in q and q.get('type') != 'reach_dim':
            q['base_target'] = q.get('target', 1)
        for key, default in (('base_reward_gold', q.get('reward_gold', 100)),
                             ('base_reward_exp',  q.get('reward_exp',  100)),
                             ('base_reward_shards', q.get('reward_shards', 0))):
            q.setdefault(key, default)

        tier = q.get('tier', 1) + 1
        q['tier'] = tier
        qtype = q.get('type')

        if qtype == 'kill':
            # 10, 18, 28, 40, 54, ...
            q['target'] = q['base_target'] + (tier - 1) * 6 + (tier - 1) * (tier - 1)
            q['desc'] = f"Slay {q['target']} monsters."
        elif qtype == 'shipwreck':
            q['target'] = q['base_target'] + (tier - 1)
            q['desc'] = f"Plunder {q['target']} shipwreck(s)."
        elif qtype == 'reach_dim':
            dims = ['nether', 'void', 'celestial']
            q['target'] = dims[(tier - 1) % len(dims)]
            q['desc'] = f"Travel to the {q['target']} dimension."

        q['name'] = f"{q['base_name']} {self._roman(tier)}"
        q['progress'] = 0
        q['state'] = 'not_started'
        # Rewards scale with tier
        q['reward_gold']   = int(q['base_reward_gold']   * tier)
        q['reward_exp']    = int(q['base_reward_exp']    * tier)
        q['reward_shards'] = q['base_reward_shards'] + (tier - 1)

    def _quest_progress_event(self, qtype, amount=1, target=None):
        """Advance any active quest of the given type, marking complete when target met."""
        if not getattr(self, 'player', None):
            return
        for q in getattr(self.player, 'active_quests', None) or []:
            if q.get('state') != 'active' or q.get('type') != qtype:
                continue
            if qtype == 'reach_dim':
                # target is a dimension name (string), not a count
                if q.get('target') != target:
                    continue
                q['progress'] = 1
                q['state'] = 'complete'
                self.add_combat_log(f"Quest ready to turn in: {q['name']}!")
            else:
                q['progress'] = q.get('progress', 0) + amount
                if q['progress'] >= q.get('target', 1):
                    q['state'] = 'complete'
                    self.add_combat_log(f"Quest ready to turn in: {q['name']}!")

    def resurrect_boss(self, altar):
        """Call Solgareth back to the Celestial settlement for another fight."""
        # Already alive somewhere on the field?
        for e in self.enemies:
            if getattr(e, 'is_final_boss', False) and e.alive:
                self.add_combat_log("Solgareth already walks the world — go find him!")
                return
        cost = 3
        if self.player.shards < cost:
            self.add_combat_log(f"You need {cost} Soul Shards to perform the rite.")
            return
        self.player.shards -= cost
        # Spawn a fresh boss next to the altar
        boss = create_final_boss(int(altar['x']), int(altar['y']) + 60)
        boss.max_hp = max(1, int(boss.max_hp * self.difficulty['enemy_hp']))
        boss.hp = boss.max_hp
        boss.damage = max(1, int(boss.damage * self.difficulty['enemy_dmg']))
        # Make sure he's in this chunk's enemy list so saves preserve him
        ckey = (0, 0)
        if ckey in self.world_chunks:
            self.world_chunks[ckey].setdefault('enemies', []).append(boss)
        self.enemies.append(boss)
        self.boss_spawned = True
        self.add_combat_log("*** The altar blazes! Solgareth rises again! ***")
        self.add_screen_flash((255, 80, 200), 24)
        self.add_screen_shake(8, 14)

    def use_beacon(self, beacon):
        """Teleport the player to the current dimension's settlement (chunk 0,0)."""
        # Make sure the settlement chunk exists, then drop the player at its centre
        if (0, 0) not in self.world_chunks:
            self.generate_world_chunk(0, 0)
            self.loaded_chunks.add((0, 0))
        self.player.x, self.player.y = 512, 512
        self.camera_x = self.player.x - SCREEN_WIDTH // 2
        self.camera_y = self.player.y - SCREEN_HEIGHT // 2
        self.update_world_loading()
        self.player.spawn_shield = 120
        self.add_combat_log("A beacon flares — you are pulled to the settlement!")
        self.add_screen_flash(beacon.get('color', (180, 220, 255)), 10)

    def enter_building(self, building):
        """Enter a building and change game state"""
        print(f"enter_building called with: {building}")
        print(f"Building type: {building['type']}")
        print(f"Current state before: {self.state}")
        
        self.current_building = building
        self.state = "inside_building"

        print(f"State changed to: {self.state}")
        print(f"Current building set to: {self.current_building['type']}")

        if building['type'] == 'shipwreck':
            self.loot_shipwreck(building)
        elif building['type'] == 'shop':
            self.add_combat_log("Welcome to the shop! Press ESC to leave")
        elif building['type'] == 'inn':
            if building.get('abandoned'):
                self.add_combat_log("This inn is abandoned and empty. Press ESC to leave")
            else:
                self.add_combat_log("Welcome to the inn! Press R to rest (one stay only). Press ESC to leave")
        elif building['type'] == 'house':
            self.search_house(building)
        elif building['type'] == 'blacksmith':
            self.add_combat_log("Welcome to the forge! Press 1-5 to upgrade equipped gear")
        elif building['type'] == 'temple':
            self.add_combat_log("The temple accepts Soul Shards for blessings. Press 1/2/3/4")
        elif building['type'] == 'library':
            self.study_library(building)
        elif building['type'] == 'tower':
            self.attune_tower(building)
        else:
            self.add_combat_log(f"You've entered the {building['type']}! Press ESC to leave")
        
        print(f"Welcome message sent for {building['type']}")

    def search_house(self, house):
        """Search a house once for a small but useful stash."""
        if house.get('searched'):
            house['loot_summary'] = ["You've already searched this home.", "Press ESC to leave"]
            self.add_combat_log("You've already searched this home. Press ESC to leave")
            return

        house['searched'] = True
        summary = ["You search the home and find:"]

        gold = random.randint(20, 80)
        self.player.gold += gold
        summary.append(f"Gold:  +{gold}")

        # Always a Health Potion — handy for the road
        self.add_item_to_inventory(Item("Health Potion", "consumable", 30))
        summary.append("Item:  Health Potion")

        # Sometimes a Mana Potion, sometimes a piece of loot
        if random.random() < 0.4:
            self.add_item_to_inventory(Item("Mana Potion", "consumable", 25))
            summary.append("Item:  Mana Potion")
        if self.drop_table and random.random() < 0.3:
            item = random.choice(self.drop_table)
            self.add_item_to_inventory(item)
            summary.append(f"Loot:  {item.name}")

        summary.append("Press ESC to leave")
        house['loot_summary'] = summary
        self.add_combat_log(f"You search the home and find {gold} gold and supplies!")
        self.add_screen_flash((120, 200, 120), 8)

    def study_library(self, library):
        """Study a library once to gain knowledge: EXP and a point of intelligence."""
        if library.get('studied'):
            library['loot_summary'] = ["You have already absorbed the knowledge here.",
                                       "Press ESC to leave"]
            self.add_combat_log("You've already studied everything here. Press ESC to leave")
            return

        library['studied'] = True
        exp = random.randint(50, 90)
        self.player.intelligence += 1
        self.player.gain_exp(exp)
        # A flash of insight occasionally restores mental focus (MP)
        restored = 0
        if self.player.mp < self.player.max_mp:
            restored = min(self.player.max_mp - self.player.mp, random.randint(10, 25))
            self.player.mp += restored

        summary = ["You pore over the ancient tomes...",
                   f"Knowledge:  +{exp} EXP",
                   "Intelligence:  +1"]
        if restored:
            summary.append(f"Focus:  +{restored} MP")
        summary.append("Press ESC to leave")
        library['loot_summary'] = summary
        self.add_combat_log(f"You study the tomes: +{exp} EXP and +1 Intelligence!")
        self.add_screen_flash((120, 160, 255), 10)

    def attune_tower(self, tower):
        """Climb an arcane tower once to attune: permanent max MP and full restore."""
        if tower.get('attuned'):
            tower['loot_summary'] = ["The arcane font here is already spent.",
                                     "Press ESC to leave"]
            self.add_combat_log("You've already attuned to this tower. Press ESC to leave")
            return

        tower['attuned'] = True
        mp_gain = random.randint(15, 25)
        self.player.max_mp += mp_gain
        self.player.mp = self.player.max_mp  # fully recharge at the font

        summary = ["You climb to the arcane font and attune...",
                   f"Max MP:  +{mp_gain}",
                   "Mana fully restored"]

        # A chance at a magical reward
        if random.random() < 0.5:
            self.add_item_to_inventory(Item("Mana Potion", "consumable", 25))
            summary.append("Item:  Mana Potion")
        if self.drop_table and random.random() < 0.25:
            arcane = [i for i in self.drop_table if i.value >= 100]
            item = random.choice(arcane) if arcane else random.choice(self.drop_table)
            self.add_item_to_inventory(item)
            summary.append(f"Loot:  {item.name}")

        summary.append("Press ESC to leave")
        tower['loot_summary'] = summary
        self.add_combat_log(f"You attune to the arcane font: +{mp_gain} max MP!")
        self.add_screen_flash((120, 160, 255), 10)

    TEMPLE_BLESSINGS = {
        1: ('Vitality',  3, 'Max HP +20'),
        2: ('Power',     4, 'Strength +2'),
        3: ('Wisdom',    4, 'Intelligence +2'),
        4: ('Renewal',   2, 'Fully restore HP & MP'),
    }

    def blacksmith_upgrade(self, slot):
        """Permanently increase an equipped piece of gear's stat for gold."""
        slot_to_attr = {
            'weapon': ('equipped_weapon', 'damage', 'damage'),
            'armor':  ('equipped_armor',  'defense', 'defense'),
            'helmet': ('equipped_helmet', 'defense', 'defense'),
            'shield': ('equipped_shield', 'defense', 'defense'),
            'boots':  ('equipped_boots',  'defense', 'defense'),
        }
        spec = slot_to_attr.get(slot)
        if not spec:
            return
        attr, stat_attr, stat_name = spec
        gear = getattr(self.player, attr, None)
        if not gear:
            self.add_combat_log(f"No {slot} equipped — nothing to forge.")
            return
        current = getattr(gear, stat_attr, 0)
        cost = 80 + current * 40  # scales with current power
        if self.player.gold < cost:
            self.add_combat_log(f"Need {cost} gold to upgrade {gear.name} (+1 {stat_name}).")
            return
        self.player.gold -= cost
        setattr(gear, stat_attr, current + 1)
        gear.value += cost // 2   # the gear is now more valuable
        self.add_combat_log(f"Forged {gear.name}! +1 {stat_name} (now {current + 1}). -{cost} gold")
        self.add_screen_flash((255, 180, 80), 8)

    def temple_blessing(self, choice):
        """Spend Soul Shards at the temple for a blessing."""
        if choice not in self.TEMPLE_BLESSINGS:
            return
        name, cost, desc = self.TEMPLE_BLESSINGS[choice]
        if self.player.shards < cost:
            self.add_combat_log(f"Not enough Soul Shards! {name} costs {cost}.")
            return
        self.player.shards -= cost
        if choice == 1:
            self.player.max_hp += 20
            self.player.hp += 20
        elif choice == 2:
            self.player.strength += 2
        elif choice == 3:
            self.player.intelligence += 2
        elif choice == 4:
            self.player.hp = self.player.max_hp
            self.player.mp = self.player.max_mp
        self.add_combat_log(f"Blessing of {name}: {desc}!")
        self.add_screen_flash((150, 220, 255), 8)

    def loot_shipwreck(self, wreck):
        """Plunder a shipwreck once for gold, items and EXP."""
        if wreck.get('looted'):
            wreck['loot_summary'] = ["The hold has already been stripped bare.",
                                     "Press ESC to leave"]
            self.add_combat_log("This wreck has already been plundered. Press ESC to leave")
            return

        wreck['looted'] = True
        summary = ["You pry open the waterlogged hold!"]

        gold = random.randint(120, 350) + max(0, self.difficulty['loot_bonus']) * 2
        self.player.gold += gold
        summary.append(f"Gold:  +{gold}")

        shards = random.randint(2, 5)
        self.player.shards += shards
        summary.append(f"Shards:  +{shards}")

        if self.drop_table:
            valuable = sorted(self.drop_table, key=lambda i: i.value)
            for _ in range(random.randint(1, 3)):
                # Bias toward the more valuable half of the loot table
                item = random.choice(valuable[len(valuable) // 2:]) if random.random() < 0.6 \
                    else random.choice(self.drop_table)
                self.add_item_to_inventory(item)
                summary.append(f"Loot:  {item.name}")

        self.player.gain_exp(60)
        summary.append("EXP:   +60")
        summary.append("Press ESC to leave")
        wreck['loot_summary'] = summary

        self.add_combat_log(f"You plunder the shipwreck for {gold} gold!")
        self.add_screen_flash(YELLOW, 12)
        self._quest_progress_event('shipwreck')

    def buy_item(self):
        """Handle buying an item from the shop"""
        if not self.shop_inventory or self.selected_item_index >= len(self.shop_inventory):
            return
            
        selected_shop_item = self.shop_inventory[self.selected_item_index]
        item = selected_shop_item['item']
        quantity = selected_shop_item['quantity']
        
        if quantity <= 0:
            self.add_combat_log("Item out of stock!")
            return
            
        if self.player.gold < item.value:
            self.add_combat_log(f"Not enough gold! Need {item.value}, have {self.player.gold}")
            return
        
        # Create a copy of the item for the player's bag
        if isinstance(item, Weapon):
            purchased_item = Weapon(item.name, item.damage, item.value,
                                    lifesteal=getattr(item, 'lifesteal', 0.0))
        elif isinstance(item, Helmet):
            purchased_item = Helmet(item.name, item.defense, item.value)
        elif isinstance(item, Shield):
            purchased_item = Shield(item.name, item.defense, item.value)
        elif isinstance(item, Boots):
            purchased_item = Boots(item.name, item.defense, item.value)
        elif isinstance(item, Ring):
            purchased_item = Ring(item.name, item.bonus, item.value)
        elif isinstance(item, Armor):
            purchased_item = Armor(item.name, item.defense, item.value)
        elif isinstance(item, Backpack):
            purchased_item = Backpack(item.name, item.bonus, item.value)
        else:
            purchased_item = Item(item.name, item.type, item.value)

        # Try to add to inventory; refund if full
        self.player.gold -= item.value
        selected_shop_item['quantity'] -= 1
        if not self.add_item_to_inventory(purchased_item, silent=True):
            self.player.gold += item.value
            selected_shop_item['quantity'] += 1
            self.add_combat_log("Inventory full! Purchase refunded.")
            return

        self.add_combat_log(f"Purchased {item.name} for {item.value} gold!")
        self.add_battle_effect(400, 300, "heal")
    
    def use_inventory_item(self):
        """Use or equip the selected inventory item"""
        if not hasattr(self.player, 'inventory') or not self.player.inventory:
            return
            
        if self.inventory_selected_index >= len(self.player.inventory):
            return
            
        selected_item = self.player.inventory[self.inventory_selected_index]
        
        if isinstance(selected_item, Backpack):
            # Use a backpack: permanently raise inventory capacity
            bonus = selected_item.bonus
            self.player.inventory.pop(self.inventory_selected_index)
            self.player.max_inventory += bonus
            self.add_combat_log(f"You strap on the {selected_item.name}! Bag size +{bonus} (now {self.player.max_inventory}).")
            self.add_screen_flash((150, 200, 255), 8)
            if self.inventory_selected_index >= len(self.player.inventory):
                self.inventory_selected_index = max(0, len(self.player.inventory) - 1)
            return
        if isinstance(selected_item, (Weapon, Armor, Helmet, Shield, Boots, Ring)):
            # Equip the item
            old_item = self.player.equip_item(selected_item)
            
            # Remove from inventory
            self.player.inventory.pop(self.inventory_selected_index)
            
            # Add old item back to inventory if there was one
            if old_item:
                self.player.inventory.append(old_item)
                self.add_combat_log(f"Equipped {selected_item.name}, unequipped {old_item.name}")
            else:
                self.add_combat_log(f"Equipped {selected_item.name}")
            
            # Adjust selection if needed
            if self.inventory_selected_index >= len(self.player.inventory):
                self.inventory_selected_index = max(0, len(self.player.inventory) - 1)
                
        elif selected_item.type == "consumable":
            # Use consumable
            self.player.inventory.pop(self.inventory_selected_index)
            
            if selected_item.name == "Health Potion":
                heal_amount = random.randint(20, 40)
                self.player.hp = min(self.player.max_hp, self.player.hp + heal_amount)
                self.add_combat_log(f"Used {selected_item.name} - healed {heal_amount} HP!")
                self.add_battle_effect(self.player.x + 16, self.player.y + 16, "heal")
            elif selected_item.name == "Mana Potion":
                mana_amount = random.randint(15, 30)
                self.player.mp = min(self.player.max_mp, self.player.mp + mana_amount)
                self.add_combat_log(f"Used {selected_item.name} - restored {mana_amount} MP!")
                self.add_battle_effect(self.player.x + 16, self.player.y + 16, "mp_regen")
            elif selected_item.name == "Alderine":
                regen = 80
                if not hasattr(self.player, 'alderine_heal_left'):
                    self.player.alderine_heal_left = 0
                self.player.alderine_heal_left += regen
                if getattr(self.player, 'alderine_tick_cd', 0) <= 0:
                    self.player.alderine_tick_cd = 10
                self.add_combat_log(f"You sip the Alderine — regenerating {regen} HP slowly.")
                self.add_battle_effect(self.player.x + 16, self.player.y + 16, "heal")
            
            # Adjust selection if needed
            if self.inventory_selected_index >= len(self.player.inventory):
                self.inventory_selected_index = max(0, len(self.player.inventory) - 1)
    
    def discard_inventory_item(self):
        """Throw away the selected inventory item."""
        if not hasattr(self.player, 'inventory') or not self.player.inventory:
            self.add_combat_log("Nothing to discard.")
            return
        if self.inventory_selected_index >= len(self.player.inventory):
            return
        item = self.player.inventory.pop(self.inventory_selected_index)
        if self.inventory_selected_index >= len(self.player.inventory):
            self.inventory_selected_index = max(0, len(self.player.inventory) - 1)
        self.add_combat_log(f"Discarded {item.name}.")

    def show_unequip_options(self):
        """Unequip everything that's currently equipped (capacity permitting)."""
        slots = [
            ("weapon", "equipped_weapon"),
            ("helmet", "equipped_helmet"),
            ("armor",  "equipped_armor"),
            ("shield", "equipped_shield"),
            ("boots",  "equipped_boots"),
            ("ring",   "equipped_ring"),
        ]
        unequipped_something = False
        for slot_name, attr in slots:
            gear = getattr(self.player, attr, None)
            if not gear:
                continue
            cap = getattr(self.player, 'max_inventory', 24)
            if len(self.player.inventory) >= cap:
                self.add_combat_log(f"Bag full — could not unequip {gear.name}")
                continue
            self.player.inventory.append(gear)
            self.player.unequip_item(slot_name)
            self.add_combat_log(f"Unequipped {gear.name}")
            unequipped_something = True
        if not unequipped_something:
            self.add_combat_log("Nothing equipped to unequip!")
    
    def is_position_blocked_infinite(self, x, y, width=32, height=32, ignore_water=False):
        """Check if a position is blocked in the infinite world"""
        visible_terrain = self.get_visible_objects('terrain')

        px = x + width // 2
        py = y + height // 2

        impassable = {'water', 'lava', 'void'}
        for tile in visible_terrain:
            if (tile['x'] <= px < tile['x'] + tile['size'] and
                    tile['y'] <= py < tile['y'] + tile['size']):
                if tile['type'] in impassable and not ignore_water:
                    return True

        return False

    def terrain_type_at(self, x, y, width=32, height=32):
        """Return the terrain type the given footprint centre is standing on, or None."""
        px = x + width // 2
        py = y + height // 2
        for tile in self.get_visible_objects('terrain'):
            if (tile['x'] <= px < tile['x'] + tile['size'] and
                    tile['y'] <= py < tile['y'] + tile['size']):
                return tile['type']
        return None

    def check_interactions_infinite(self):
        """Check for nearby interactable objects in infinite world"""
        if not hasattr(self, 'player') or not self.player:
            return None
            
        player_center_x = self.player.x + 16
        player_center_y = self.player.y + 16
        
        closest_object = None
        closest_distance = float('inf')
        
        # Check all visible interactables
        for chunk_data in self.world_chunks.values():
            for obj in chunk_data.get('interactables', []):
                obj_center_x = obj['x'] + obj['width'] // 2
                obj_center_y = obj['y'] + obj['height'] // 2
                
                # Calculate distance
                distance = math.sqrt((player_center_x - obj_center_x)**2 + (player_center_y - obj_center_y)**2)
                
                # Check if within interaction range
                interaction_range = 50 if obj['type'] in ('door', 'dungeon', 'beacon', 'npc', 'altar') else 30

                if distance <= interaction_range and distance < closest_distance:
                    closest_distance = distance
                    closest_object = obj

        if closest_object:
            if closest_object['action'] == 'enter_building':
                self.add_combat_log(f"Press E to enter {closest_object['building_type']}")
                return closest_object
            elif closest_object['action'] == 'open_chest' and not closest_object['opened']:
                self.add_combat_log("Press E to open chest")
                return closest_object
            elif closest_object['action'] == 'use_portal':
                dest = closest_object['destination']
                self.add_combat_log(f"Press E to enter portal → {dest}")
                return closest_object
            elif closest_object['action'] == 'enter_dungeon':
                remaining = max(0, 3 - closest_object.get('entries_used', 0))
                if remaining == 0:
                    self.add_combat_log("This dungeon has collapsed.")
                else:
                    self.add_combat_log(f"Press E to enter the dungeon ({remaining}/3 left)")
                return closest_object
            elif closest_object['action'] == 'use_beacon':
                self.add_combat_log("Press E to teleport to the settlement")
                return closest_object
            elif closest_object['action'] == 'talk_npc':
                self.add_combat_log(f"Press E to talk to {closest_object.get('name', 'NPC')}")
                return closest_object
            elif closest_object['action'] == 'use_altar':
                self.add_combat_log("Press E to resurrect Solgareth (costs 3 Soul Shards)")
                return closest_object

        return None

    def is_enemy_visible(self, enemy):
        """Check if an enemy is on screen and belongs to the current dimension"""
        if getattr(enemy, 'dimension', 'overworld') != self.current_dimension:
            return False
        visible_left = self.camera_x
        visible_right = self.camera_x + SCREEN_WIDTH
        visible_top = self.camera_y
        visible_bottom = self.camera_y + SCREEN_HEIGHT

        return (enemy.x + 24 > visible_left and enemy.x < visible_right and
                enemy.y + 24 > visible_top and enemy.y < visible_bottom)
    
    def start_selling_mode(self):
        """Start selling mode - for now just show a message"""
        if not hasattr(self.player, 'inventory') or not self.player.inventory:
            self.add_combat_log("You have nothing to sell!")
            return
        
        # Simple selling - sell the last item for half price
        last_item = self.player.inventory[-1]
        sell_price = last_item.value // 2
        
        self.player.gold += sell_price
        self.player.inventory.remove(last_item)
        
        self.add_combat_log(f"Sold {last_item.name} for {sell_price} gold!")
        self.add_battle_effect(400, 300, "heal")
    
    def open_chest(self, chest):
        """Open a treasure chest"""
        chest['opened'] = True
        rarity = chest.get('rarity', 'common')

        if rarity == 'rare':
            gold = random.randint(60, 180)
            self.player.gold += gold
            self.add_combat_log(f"*** RARE CHEST: Found {gold} gold! ***")
            # Rare item is no longer guaranteed (60% chance)
            rare_items = [i for i in self.drop_table if i.value >= 100]
            if rare_items and random.random() < 0.6:
                item = random.choice(rare_items)
                self.add_item_to_inventory(item)
                self.add_combat_log(f"Also found: {item.name}!")
            if random.random() < 0.7:
                shards = random.randint(1, 3)
                self.player.shards += shards
                self.add_combat_log(f"Found {shards} Soul Shards!")
            self.player.gain_exp(40)
            self.add_screen_flash(YELLOW, 15)
        elif rarity == 'uncommon':
            gold = random.randint(25, 80)
            self.player.gold += gold
            self.add_combat_log(f"Uncommon chest: Found {gold} gold!")
            if random.random() < 0.3:
                shards = random.randint(1, 2)
                self.player.shards += shards
                self.add_combat_log(f"Found {shards} Soul Shards!")
            if self.drop_table and random.random() < 0.4:
                item = random.choice(self.drop_table)
                self.add_item_to_inventory(item)
                self.add_combat_log(f"Also found: {item.name}!")
            self.player.gain_exp(15)
            self.add_screen_flash((80, 120, 255), 8)
        else:
            roll = random.randint(1, 100)
            if roll <= 35:
                gold = random.randint(8, 30)
                self.player.gold += gold
                self.add_combat_log(f"Found {gold} gold in the chest!")
            elif roll <= 60:
                if self.drop_table and random.random() < 0.6:
                    item = random.choice(self.drop_table)
                    self.add_item_to_inventory(item)
                    self.add_combat_log(f"Found {item.name} in the chest!")
                else:
                    self.add_combat_log("The chest is empty.")
            elif roll <= 80:
                self.player.gain_exp(10)
                self.add_combat_log("Found a tattered tome. (+10 EXP)")
            else:
                self.add_combat_log("The chest is empty.")
            self.add_screen_flash(YELLOW, 5)

        self.add_battle_effect(chest['x'] + 10, chest['y'] + 8, "heal")
    
    def add_battle_effect(self, x, y, effect_type):
        try:
            effect = BattleEffect(x, y, effect_type)
            self.battle_effects.append(effect)
        except Exception as e:
            print(f"Battle effect error: {e}")
    
    def add_screen_shake(self, intensity, duration=10):
        try:
            self.screen_shake = duration
            self.screen_shake_intensity = intensity
        except Exception as e:
            print(f"Screen shake error: {e}")
    
    def add_screen_flash(self, color, duration=5):
        try:
            self.flash_screen = duration
            self.flash_color = color
        except Exception as e:
            print(f"Screen flash error: {e}")
    
    def add_combat_log(self, message):
        try:
            if not hasattr(self, 'combat_log'):
                self.combat_log = []
            self.combat_log.append(str(message))
            if len(self.combat_log) > 5:
                self.combat_log.pop(0)
            self.log_timer = 180
        except Exception as e:
            print(f"Combat log error: {e}")
    
    def add_item_to_inventory(self, item, silent=False):
        """Add an item to the player inventory. Returns True on success, False if full."""
        if not item or not self.player:
            return False

        try:
            # Loose currency items auto-convert to gold (legacy)
            if getattr(item, 'type', None) == "currency":
                bonus_gold = random.randint(10, 25)
                self.player.gold += bonus_gold
                if not silent:
                    self.add_combat_log(f"Found {item.name} - gained {bonus_gold} extra gold!")
                return True
            # Everything else (potions, weapons, armor) goes to the bag
            if not hasattr(self.player, 'inventory'):
                self.player.inventory = []
            cap = getattr(self.player, 'max_inventory', 24)
            if len(self.player.inventory) >= cap:
                if not silent:
                    self.add_combat_log(f"Inventory full! ({item.name} left behind)")
                return False
            self.player.inventory.append(item)
            if not silent:
                self.add_combat_log(f"Picked up: {item.name}")
            return True
        except Exception as e:
            print(f"Add item to inventory error: {e}")
            return False
    
    def roll_item_drop(self, enemy_level):
        """Roll for random item drop based on enemy level"""
        try:
            # Base drop chance: 30% + 10% per enemy level, adjusted by difficulty
            drop_chance = 30 + (enemy_level * 10) + self.difficulty['loot_bonus']
            drop_chance = max(5, min(80, drop_chance))
            
            if random.randint(1, 100) <= drop_chance:
                # Determine rarity based on enemy level and luck
                rarity_roll = random.randint(1, 100)
                
                if enemy_level >= 3 and rarity_roll <= 5:  # 5% chance for rare items from level 3+ enemies
                    rare_items = [item for item in self.drop_table if item.value >= 100]
                    if rare_items:
                        dropped_item = random.choice(rare_items)
                        return dropped_item, "rare"
                
                elif rarity_roll <= 25:  # 25% chance for uncommon items
                    uncommon_items = [item for item in self.drop_table if 50 <= item.value < 100]
                    if uncommon_items:
                        dropped_item = random.choice(uncommon_items)
                        return dropped_item, "uncommon"
                
                else:  # Common items
                    common_items = [item for item in self.drop_table if item.value < 50]
                    if common_items:
                        dropped_item = random.choice(common_items)
                        return dropped_item, "common"
            
            return None, None
        except Exception as e:
            print(f"Roll item drop error: {e}")
            return None, None
    
    def handle_enemy_defeat(self, enemy_x, enemy_y):
        """Handle enemy defeat, drops, and cleanup"""
        if not self.current_enemy:
            return
            
        try:
            # Store enemy info before clearing reference
            enemy_name = self.current_enemy.name
            enemy_level = self.current_enemy.level
            exp_reward = self.current_enemy.exp_reward
            gold_reward = self.current_enemy.gold_reward
            was_final_boss = getattr(self.current_enemy, 'is_final_boss', False)

            self.add_battle_effect(enemy_x, enemy_y, "hit")
            self.add_screen_flash(YELLOW, 10)
            self.add_combat_log(f"{enemy_name} defeated!")
            self.add_combat_log(f"Gained {exp_reward} EXP and {gold_reward} gold!")
            
            # Check for item drop
            dropped_item, rarity = self.roll_item_drop(enemy_level)
            if dropped_item:
                if rarity == "rare":
                    self.add_combat_log(f"*** RARE DROP: {dropped_item.name} ***")
                    self.add_screen_flash(YELLOW, 15)
                elif rarity == "uncommon":
                    self.add_combat_log(f"** {dropped_item.name} **")
                else:
                    self.add_combat_log(f"Dropped: {dropped_item.name}")
                
                self.add_item_to_inventory(dropped_item)
                # Add sparkle effect for item drop
                self.add_battle_effect(enemy_x, enemy_y, "heal")
            
            # Award experience and gold
            self.player.gain_exp(exp_reward)
            self.player.gold += gold_reward

            # Signature mob drops (in addition to the normal random roll)
            if enemy_name == 'Vampire' and random.random() < 0.40:
                fang = Weapon("Vampire Fang", 4, 220, lifesteal=0.20)
                if self.add_item_to_inventory(fang, silent=True):
                    self.add_combat_log("*** Vampire Fang dropped! ***")
                    self.add_screen_flash(YELLOW, 12)
                    self.add_battle_effect(enemy_x, enemy_y, "heal")

            # Advance any active "kill" quests
            self._quest_progress_event('kill')

            # Soul Shards: rare from normal foes, a big haul from the final boss
            if was_final_boss:
                shards = random.randint(25, 40)
                self.player.shards += shards
                self.add_combat_log(f"You harvest {shards} Soul Shards!")
                # Celestial Essence: ONLY the final boss drops this
                essence = random.randint(3, 5)
                self.player.essence = getattr(self.player, 'essence', 0) + essence
                self.add_combat_log(f"*** You absorb {essence} Celestial Essence! ***")
                self.add_screen_flash((255, 240, 200), 18)
            elif random.random() < 0.18 + enemy_level * 0.01:
                shards = random.randint(1, 2 + enemy_level // 3)
                self.player.shards += shards
                self.add_combat_log(f"You collect {shards} Soul Shard(s)!")

            # Exit combat — defeating the final boss wins the game
            self.current_enemy = None
            if was_final_boss:
                self.in_boss_fight = False
                self.state = "victory"
            else:
                self.state = "playing"
        except Exception as e:
            print(f"Handle enemy defeat error: {e}")
            # Failsafe - just exit combat
            self.state = "playing"
            self.current_enemy = None
    
    def _generate_town_chunk(self, chunk_key):
        """Generate the starting town at chunk (0,0)."""
        chunk_data = {'terrain': [], 'buildings': [], 'vegetation': [], 'enemies': [], 'interactables': []}
        tile_size = 32
        base_x, base_y = 0, 0

        # All grass terrain in town
        for y in range(0, self.chunk_size, tile_size):
            for x in range(0, self.chunk_size, tile_size):
                chunk_data['terrain'].append({'x': x, 'y': y, 'type': 'grass', 'size': tile_size})

        # One of every building type, arranged in two rows
        town_buildings = [
            {'type': 'shop',       'width': 56, 'height': 40, 'color': (139, 90, 43)},
            {'type': 'inn',        'width': 64, 'height': 48, 'color': (160, 82, 45)},
            {'type': 'blacksmith', 'width': 52, 'height': 44, 'color': (105, 105, 105)},
            {'type': 'temple',     'width': 60, 'height': 52, 'color': (218, 165, 32)},
            {'type': 'library',    'width': 58, 'height': 46, 'color': (139, 69, 19)},
            {'type': 'house',      'width': 48, 'height': 48, 'color': BROWN},
            {'type': 'tower',      'width': 32, 'height': 64, 'color': GRAY},
        ]
        # Place buildings in a grid around the centre (512, 512)
        cols = 4
        spacing_x, spacing_y = 180, 160
        start_x = 512 - (min(cols, len(town_buildings)) * spacing_x) // 2
        start_y = 300
        for i, bdef in enumerate(town_buildings):
            b = bdef.copy()
            col = i % cols
            row = i // cols
            b['x'] = start_x + col * spacing_x
            b['y'] = start_y + row * spacing_y
            chunk_data['buildings'].append(b)
            door_x = b['x'] + b['width'] // 2 - 8
            door_y = b['y'] + b['height'] - 20
            chunk_data['interactables'].append({
                'type': 'door', 'x': door_x, 'y': door_y,
                'width': 16, 'height': 20,
                'building_type': b['type'], 'building_data': b, 'action': 'enter_building'
            })

        # Nether portal in the town square
        portal_info = DIMENSION_PORTAL['overworld']
        chunk_data['interactables'].append({
            'type': 'portal', 'x': 512, 'y': 700,
            'width': 40, 'height': 56,
            'destination': portal_info['dest'],
            'color': portal_info['color'],
            'label': portal_info['label'],
            'action': 'use_portal'
        })

        # A few chests scattered around town
        for cx, cy, rar in [(200, 450, 'common'), (820, 450, 'uncommon'), (512, 820, 'rare')]:
            chunk_data['interactables'].append({
                'type': 'chest', 'x': cx, 'y': cy,
                'width': 20, 'height': 16,
                'opened': False, 'rarity': rar, 'action': 'open_chest'
            })

        # Decorative trees around the perimeter (kept clear of buildings)
        for i in range(30):
            angle = i * 2 * math.pi / 30
            tx = int(512 + 440 * math.cos(angle))
            ty = int(512 + 380 * math.sin(angle))
            if not (0 < tx < self.chunk_size and 0 < ty < self.chunk_size):
                continue
            if any(b['x'] - 8 <= tx <= b['x'] + b['width'] + 8 and
                   b['y'] - 8 <= ty <= b['y'] + b['height'] + 8
                   for b in chunk_data['buildings']):
                continue
            chunk_data['vegetation'].append({'type': 'tree', 'size': 16, 'color': DARK_GREEN, 'x': tx, 'y': ty})

        # Quest-giving NPCs across the top of the town
        npc_defs = [
            (200, 240, 'Marcus the Hunter', (140, 90, 50), {
                'id': 'kill_10', 'name': "Hunter's Bounty",
                'base_name': "Hunter's Bounty",
                'desc': 'Slay 10 monsters of any kind.',
                'type': 'kill', 'target': 10, 'base_target': 10, 'progress': 0,
                'reward_gold': 250, 'reward_exp': 150, 'reward_shards': 1,
                'base_reward_gold': 250, 'base_reward_exp': 150, 'base_reward_shards': 1,
                'tier': 1,
            }),
            (512, 240, 'Mira the Sage', (90, 100, 200), {
                'id': 'reach_nether', 'name': 'Through the Veil',
                'base_name': 'Through the Veil',
                'desc': 'Travel to the Nether dimension.',
                'type': 'reach_dim', 'target': 'nether', 'progress': 0,
                'reward_gold': 150, 'reward_exp': 200, 'reward_shards': 5,
                'base_reward_gold': 150, 'base_reward_exp': 200, 'base_reward_shards': 5,
                'tier': 1,
            }),
            (824, 240, 'Captain Vance', (60, 60, 90), {
                'id': 'plunder_wreck', 'name': "Sea Wraith's Hold",
                'base_name': "Sea Wraith's Hold",
                'desc': 'Plunder a shipwreck out in the water.',
                'type': 'shipwreck', 'target': 1, 'base_target': 1, 'progress': 0,
                'reward_gold': 180, 'reward_exp': 120, 'reward_shards': 2,
                'base_reward_gold': 180, 'base_reward_exp': 120, 'base_reward_shards': 2,
                'tier': 1,
            }),
        ]
        for nx, ny, name, col, qbase in npc_defs:
            quest = dict(qbase)
            quest['state'] = 'not_started'
            quest['giver'] = name
            chunk_data['interactables'].append({
                'type': 'npc', 'x': nx, 'y': ny,
                'width': 24, 'height': 32,
                'name': name, 'color': col,
                'quest': quest,
                'action': 'talk_npc',
            })

        # No enemies in town
        self.world_chunks[chunk_key] = chunk_data

    def _generate_nether_settlement_chunk(self, chunk_key):
        """Generate a safe Nether settlement at chunk (0,0) of the Nether."""
        chunk_data = {'terrain': [], 'buildings': [], 'vegetation': [], 'enemies': [], 'interactables': []}
        tile_size = 32

        # Ash ground floor, with a ring of nether rock and lava pools around the edges
        center = self.chunk_size // 2
        for y in range(0, self.chunk_size, tile_size):
            for x in range(0, self.chunk_size, tile_size):
                # Distance from the settlement centre
                dx = (x + tile_size // 2) - center
                dy = (y + tile_size // 2) - center
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < 360:
                    ttype = 'ash'              # walkable settlement floor
                elif dist < 440:
                    ttype = 'nether_rock'      # rocky perimeter wall
                else:
                    # Lava moat with occasional rock islands, deterministic per tile
                    h = ((x * 374761393) ^ (y * 668265263)) & 0xFFFFFFFF
                    ttype = 'nether_rock' if (h % 5 == 0) else 'lava'
                chunk_data['terrain'].append({'x': x, 'y': y, 'type': ttype, 'size': tile_size})

        # Nether-themed buildings, arranged in a grid around the centre
        nether_buildings = [
            {'type': 'shop',       'width': 56, 'height': 40, 'color': (90, 30, 20)},
            {'type': 'blacksmith', 'width': 52, 'height': 44, 'color': (70, 70, 75)},
            {'type': 'inn',        'width': 64, 'height': 48, 'color': (120, 40, 20)},
            {'type': 'temple',     'width': 60, 'height': 52, 'color': (150, 60, 20)},
            {'type': 'tower',      'width': 32, 'height': 64, 'color': (60, 40, 45)},
            {'type': 'house',      'width': 48, 'height': 48, 'color': (80, 35, 25)},
        ]
        cols = 3
        spacing_x, spacing_y = 200, 170
        start_x = center - (cols * spacing_x) // 2 + 40
        start_y = 320
        for i, bdef in enumerate(nether_buildings):
            b = bdef.copy()
            col = i % cols
            row = i // cols
            b['x'] = start_x + col * spacing_x
            b['y'] = start_y + row * spacing_y
            chunk_data['buildings'].append(b)
            door_x = b['x'] + b['width'] // 2 - 8
            door_y = b['y'] + b['height'] - 20
            chunk_data['interactables'].append({
                'type': 'door', 'x': door_x, 'y': door_y,
                'width': 16, 'height': 20,
                'building_type': b['type'], 'building_data': b, 'action': 'enter_building'
            })

        # Return portal back to the Overworld
        chunk_data['interactables'].append({
            'type': 'portal', 'x': center - 140, 'y': 700,
            'width': 40, 'height': 56,
            'destination': 'overworld',
            'color': (60, 180, 255),
            'label': 'Return Portal',
            'action': 'use_portal'
        })

        # Portal deeper into the Void
        void_info = DIMENSION_PORTAL['nether']
        chunk_data['interactables'].append({
            'type': 'portal', 'x': center + 140, 'y': 700,
            'width': 40, 'height': 56,
            'destination': void_info['dest'],
            'color': void_info['color'],
            'label': void_info['label'],
            'action': 'use_portal'
        })

        # Treasure chests with better Nether loot
        for cx, cy, rar in [(center - 240, 460, 'uncommon'), (center + 240, 460, 'rare'), (center, 820, 'rare')]:
            chunk_data['interactables'].append({
                'type': 'chest', 'x': cx, 'y': cy,
                'width': 20, 'height': 16,
                'opened': False, 'rarity': rar, 'action': 'open_chest'
            })

        # No enemies in the settlement (safe zone)
        self.world_chunks[chunk_key] = chunk_data

    def _generate_void_settlement_chunk(self, chunk_key):
        """Generate a safe floating Void settlement at chunk (0,0) of the Void."""
        chunk_data = {'terrain': [], 'buildings': [], 'vegetation': [], 'enemies': [], 'interactables': []}
        tile_size = 32

        # A floating island of void rock ringed by crystals, surrounded by the abyss
        center = self.chunk_size // 2
        for y in range(0, self.chunk_size, tile_size):
            for x in range(0, self.chunk_size, tile_size):
                dx = (x + tile_size // 2) - center
                dy = (y + tile_size // 2) - center
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < 360:
                    ttype = 'void_island'      # walkable island floor
                elif dist < 430:
                    h = ((x * 374761393) ^ (y * 668265263)) & 0xFFFFFFFF
                    ttype = 'void_crystal' if (h % 4 == 0) else 'void_island'
                else:
                    ttype = 'void'             # the impassable abyss
                chunk_data['terrain'].append({'x': x, 'y': y, 'type': ttype, 'size': tile_size})

        # Void-themed buildings, arranged in a grid around the centre
        void_buildings = [
            {'type': 'shop',       'width': 56, 'height': 40, 'color': (40, 20, 70)},
            {'type': 'blacksmith', 'width': 52, 'height': 44, 'color': (30, 30, 55)},
            {'type': 'inn',        'width': 64, 'height': 48, 'color': (55, 25, 85)},
            {'type': 'temple',     'width': 60, 'height': 52, 'color': (80, 0, 140)},
            {'type': 'tower',      'width': 32, 'height': 64, 'color': (35, 15, 60)},
            {'type': 'house',      'width': 48, 'height': 48, 'color': (45, 25, 75)},
        ]
        cols = 3
        spacing_x, spacing_y = 200, 170
        start_x = center - (cols * spacing_x) // 2 + 40
        start_y = 320
        for i, bdef in enumerate(void_buildings):
            b = bdef.copy()
            col = i % cols
            row = i // cols
            b['x'] = start_x + col * spacing_x
            b['y'] = start_y + row * spacing_y
            chunk_data['buildings'].append(b)
            door_x = b['x'] + b['width'] // 2 - 8
            door_y = b['y'] + b['height'] - 20
            chunk_data['interactables'].append({
                'type': 'door', 'x': door_x, 'y': door_y,
                'width': 16, 'height': 20,
                'building_type': b['type'], 'building_data': b, 'action': 'enter_building'
            })

        # Return portal back to the Nether
        chunk_data['interactables'].append({
            'type': 'portal', 'x': center - 140, 'y': 700,
            'width': 40, 'height': 56,
            'destination': 'nether',
            'color': (220, 80, 20),
            'label': 'Return Portal',
            'action': 'use_portal'
        })

        # Portal deeper into the Celestial Realm
        cel_info = DIMENSION_PORTAL['void']
        chunk_data['interactables'].append({
            'type': 'portal', 'x': center + 140, 'y': 700,
            'width': 40, 'height': 56,
            'destination': cel_info['dest'],
            'color': cel_info['color'],
            'label': cel_info['label'],
            'action': 'use_portal'
        })

        # Treasure chests with high-end Void loot
        for cx, cy, rar in [(center - 240, 460, 'rare'), (center + 240, 460, 'rare'), (center, 820, 'rare')]:
            chunk_data['interactables'].append({
                'type': 'chest', 'x': cx, 'y': cy,
                'width': 20, 'height': 16,
                'opened': False, 'rarity': rar, 'action': 'open_chest'
            })

        # No enemies in the settlement (safe zone)
        self.world_chunks[chunk_key] = chunk_data

    def _generate_celestial_settlement_chunk(self, chunk_key):
        """Generate a safe heavenly settlement at chunk (0,0) of the Celestial Realm."""
        chunk_data = {'terrain': [], 'buildings': [], 'vegetation': [], 'enemies': [], 'interactables': []}
        tile_size = 32

        # A golden courtyard ringed by celestial peaks, set amid the clouds
        center = self.chunk_size // 2
        for y in range(0, self.chunk_size, tile_size):
            for x in range(0, self.chunk_size, tile_size):
                dx = (x + tile_size // 2) - center
                dy = (y + tile_size // 2) - center
                dist = (dx * dx + dy * dy) ** 0.5
                if dist < 360:
                    ttype = 'gold_ground'      # walkable golden courtyard
                elif dist < 430:
                    h = ((x * 374761393) ^ (y * 668265263)) & 0xFFFFFFFF
                    ttype = 'celestial_peak' if (h % 3 == 0) else 'gold_ground'
                else:
                    ttype = 'cloud'            # surrounding clouds
                chunk_data['terrain'].append({'x': x, 'y': y, 'type': ttype, 'size': tile_size})

        # Celestial-themed buildings, arranged in a grid around the centre
        celestial_buildings = [
            {'type': 'shop',       'width': 56, 'height': 40, 'color': (230, 210, 150)},
            {'type': 'blacksmith', 'width': 52, 'height': 44, 'color': (200, 200, 210)},
            {'type': 'inn',        'width': 64, 'height': 48, 'color': (240, 220, 170)},
            {'type': 'temple',     'width': 60, 'height': 52, 'color': (255, 240, 180)},
            {'type': 'tower',      'width': 32, 'height': 64, 'color': (220, 225, 240)},
            {'type': 'house',      'width': 48, 'height': 48, 'color': (235, 225, 195)},
        ]
        cols = 3
        spacing_x, spacing_y = 200, 170
        start_x = center - (cols * spacing_x) // 2 + 40
        start_y = 320
        for i, bdef in enumerate(celestial_buildings):
            b = bdef.copy()
            col = i % cols
            row = i // cols
            b['x'] = start_x + col * spacing_x
            b['y'] = start_y + row * spacing_y
            chunk_data['buildings'].append(b)
            door_x = b['x'] + b['width'] // 2 - 8
            door_y = b['y'] + b['height'] - 20
            chunk_data['interactables'].append({
                'type': 'door', 'x': door_x, 'y': door_y,
                'width': 16, 'height': 20,
                'building_type': b['type'], 'building_data': b, 'action': 'enter_building'
            })

        # Return portal back to the Void
        chunk_data['interactables'].append({
            'type': 'portal', 'x': center - 140, 'y': 700,
            'width': 40, 'height': 56,
            'destination': 'void',
            'color': (100, 0, 180),
            'label': 'Return Portal',
            'action': 'use_portal'
        })

        # Portal back to the Overworld (completes the loop)
        home_info = DIMENSION_PORTAL['celestial']
        chunk_data['interactables'].append({
            'type': 'portal', 'x': center + 140, 'y': 700,
            'width': 40, 'height': 56,
            'destination': home_info['dest'],
            'color': home_info['color'],
            'label': home_info['label'],
            'action': 'use_portal'
        })

        # Treasure chests with the best loot in the game
        for cx, cy, rar in [(center - 240, 460, 'rare'), (center + 240, 460, 'rare'), (center, 820, 'rare')]:
            chunk_data['interactables'].append({
                'type': 'chest', 'x': cx, 'y': cy,
                'width': 20, 'height': 16,
                'opened': False, 'rarity': rar, 'action': 'open_chest'
            })

        # Resurrection Altar — call Solgareth back for another fight
        chunk_data['interactables'].append({
            'type': 'altar', 'x': center, 'y': 250,
            'width': 40, 'height': 56,
            'action': 'use_altar',
        })

        # The final boss guards the heart of the Celestial Realm
        if not getattr(self, 'boss_spawned', False):
            boss = create_final_boss(center, 150)
            boss.max_hp = max(1, int(boss.max_hp * self.difficulty['enemy_hp']))
            boss.hp = boss.max_hp
            boss.damage = max(1, int(boss.damage * self.difficulty['enemy_dmg']))
            chunk_data['enemies'].append(boss)
            self.enemies.append(boss)
            self.boss_spawned = True

        self.world_chunks[chunk_key] = chunk_data

    def _classify_terrain_for_dim(self, wx, wy):
        """Return a terrain type string for the current dimension."""
        e = self.world_noise(wx, wy, 0.0008, octaves=5, seed_offset=0)
        m = self.world_noise(wx, wy, 0.0013, octaves=4, seed_offset=7919)
        dim = self.current_dimension
        if dim == 'nether':
            if e < 0.35: return 'lava'
            if e > 0.72: return 'nether_rock'
            return 'ash'
        elif dim == 'void':
            if e < 0.42: return 'void'
            if e > 0.70: return 'void_crystal'
            return 'void_island'
        elif dim == 'celestial':
            if e < 0.30: return 'cloud'
            if e > 0.75: return 'celestial_peak'
            return 'gold_ground'
        else:  # overworld
            return self.classify_terrain(wx, wy)

    def switch_dimension(self, dest):
        """Teleport to a different dimension."""
        # Save current position
        self.dimension_player_pos[self.current_dimension] = (self.player.x, self.player.y)
        # Switch chunk storage
        self.current_dimension = dest
        self.world_chunks  = self.all_dimension_chunks[dest]
        self.loaded_chunks = self.all_loaded_chunks[dest]
        # Restore or reset position
        if dest in self.dimension_player_pos:
            self.player.x, self.player.y = self.dimension_player_pos[dest]
        else:
            self.player.x, self.player.y = 512, 512
        self.camera_x = self.player.x - SCREEN_WIDTH // 2
        self.camera_y = self.player.y - SCREEN_HEIGHT // 2
        self.update_world_loading()
        self.player.spawn_shield = 180  # brief immunity on arrival
        dim_names = {'overworld': 'the Overworld', 'nether': 'the Nether',
                     'void': 'the Void', 'celestial': 'the Celestial Realm'}
        self.add_combat_log(f"You enter {dim_names.get(dest, dest)}!")
        self._quest_progress_event('reach_dim', target=dest)

    def generate_world_chunk(self, chunk_x, chunk_y):
        """Generate a chunk of the world at given chunk coordinates"""
        chunk_key = (chunk_x, chunk_y)

        if chunk_key in self.world_chunks:
            return  # Chunk already generated

        # Origin chunk is a settlement (town in overworld, settlement in nether)
        if chunk_x == 0 and chunk_y == 0:
            if self.current_dimension == 'overworld':
                self._generate_town_chunk(chunk_key)
                return
            elif self.current_dimension == 'nether':
                self._generate_nether_settlement_chunk(chunk_key)
                return
            elif self.current_dimension == 'void':
                self._generate_void_settlement_chunk(chunk_key)
                return
            elif self.current_dimension == 'celestial':
                self._generate_celestial_settlement_chunk(chunk_key)
                return
        
        chunk_data = {
            'terrain': [],
            'buildings': [],
            'vegetation': [],
            'enemies': [],
            'interactables': []
        }
        
        # Generate terrain for this chunk
        tile_size = 32
        base_x = chunk_x * self.chunk_size
        base_y = chunk_y * self.chunk_size
        
        # Sample the elevation field once over the chunk plus a one-tile border, so
        # each tile can derive a slope from its neighbours. Doing this on the grid
        # (rather than 4 extra noise lookups per tile) keeps chunk generation cheap.
        n = self.chunk_size // tile_size
        elev_grid = [[self.world_noise(base_x + (gx - 1) * tile_size,
                                       base_y + (gy - 1) * tile_size,
                                       0.0008, octaves=5, seed_offset=0)
                      for gx in range(n + 2)] for gy in range(n + 2)]

        for y in range(0, self.chunk_size, tile_size):
            for x in range(0, self.chunk_size, tile_size):
                world_x = base_x + x
                world_y = base_y + y
                terrain_type = self._classify_terrain_for_dim(world_x, world_y)
                gx, gy = x // tile_size + 1, y // tile_size + 1
                elev = elev_grid[gy][gx]
                # Central-difference slope, then Lambert-style shading from a
                # north-west sun. `lit` < 0.5 faces away, > 0.5 faces into the light.
                dzdx = elev_grid[gy][gx + 1] - elev_grid[gy][gx - 1]
                dzdy = elev_grid[gy + 1][gx] - elev_grid[gy - 1][gx]
                lit = max(0.0, min(1.0, 0.5 + (-dzdx - dzdy) * 6.0))
                chunk_data['terrain'].append({
                    'x': world_x, 'y': world_y, 'type': terrain_type, 'size': tile_size,
                    'elev': elev, 'lit': lit,
                })
        
        # Generate buildings for this chunk
        building_types = [
            {'type': 'house', 'width': 48, 'height': 48, 'color': BROWN},
            {'type': 'tower', 'width': 32, 'height': 64, 'color': GRAY},
            {'type': 'shop', 'width': 56, 'height': 40, 'color': (139, 90, 43)},
            {'type': 'inn', 'width': 64, 'height': 48, 'color': (160, 82, 45)},
            {'type': 'blacksmith', 'width': 52, 'height': 44, 'color': (105, 105, 105)},
            {'type': 'temple', 'width': 60, 'height': 52, 'color': (218, 165, 32)},
            {'type': 'library', 'width': 58, 'height': 46, 'color': (139, 69, 19)}
        ]
        # Guarantee one shop per chunk, then add random extras
        shop_def = next(b for b in building_types if b['type'] == 'shop')
        buildings_to_place = [shop_def.copy()] + [
            random.choice(building_types).copy() for _ in range(random.randint(2, 4))
        ]

        for building in buildings_to_place:
            building = random.choice(building_types).copy()

            placed = False
            margin = 24  # minimum gap between buildings
            liquid = {'water', 'lava', 'void'}  # impassable terrain across all dimensions
            for attempt in range(50):
                bx = base_x + random.randint(100, self.chunk_size - 200)
                by = base_y + random.randint(100, self.chunk_size - 200)
                # Scan the whole footprint (per-dimension terrain), not just a few corners
                footprint_ok = True
                py = by
                while py <= by + building['height'] and footprint_ok:
                    px = bx
                    while px <= bx + building['width']:
                        if self._classify_terrain_for_dim(px, py) in liquid:
                            footprint_ok = False
                            break
                        px += 16
                    py += 16
                if not footprint_ok:
                    continue
                # Check no overlap with already-placed buildings in this chunk
                overlaps = False
                for existing in chunk_data['buildings']:
                    if (bx < existing['x'] + existing['width'] + margin and
                            bx + building['width'] + margin > existing['x'] and
                            by < existing['y'] + existing['height'] + margin and
                            by + building['height'] + margin > existing['y']):
                        overlaps = True
                        break
                if not overlaps:
                    building['x'] = bx
                    building['y'] = by
                    placed = True
                    break

            if not placed:
                continue

            chunk_data['buildings'].append(building)

            # Create door interactable for each building
            door_x = building['x'] + building['width']//2 - 8
            door_y = building['y'] + building['height'] - 20

            chunk_data['interactables'].append({
                'type': 'door',
                'x': door_x,
                'y': door_y,
                'width': 16,
                'height': 20,
                'building_type': building['type'],
                'building_data': building,
                'action': 'enter_building'
            })

        # Shipwrecks out in the water (overworld only) — dash out to reach and loot them
        if self.current_dimension == 'overworld' and random.random() < 0.30:
            ww, wh = 64, 44
            for attempt in range(40):
                sxw = base_x + random.randint(120, self.chunk_size - 160)
                syw = base_y + random.randint(120, self.chunk_size - 160)
                # Require the footprint to sit on open water
                corners = [(sxw, syw), (sxw + ww, syw), (sxw, syw + wh),
                           (sxw + ww, syw + wh), (sxw + ww // 2, syw + wh // 2)]
                if all(self.classify_terrain(cxp, cyp) == 'water' for cxp, cyp in corners):
                    wreck = {'type': 'shipwreck', 'width': ww, 'height': wh,
                             'color': (90, 60, 35), 'x': sxw, 'y': syw, 'looted': False}
                    chunk_data['buildings'].append(wreck)
                    chunk_data['interactables'].append({
                        'type': 'door', 'x': sxw + ww // 2 - 8, 'y': syw + wh - 20,
                        'width': 16, 'height': 20,
                        'building_type': 'shipwreck', 'building_data': wreck,
                        'action': 'enter_building'
                    })
                    break

        # Generate decoration appropriate to the current dimension
        dim_vegetation = {
            'overworld': [
                {'type': 'tree',     'size': 16, 'color': DARK_GREEN},
                {'type': 'bush',     'size': 8,  'color': (34, 139, 34)},
                {'type': 'rock',     'size': 12, 'color': GRAY},
                {'type': 'flower',   'size': 5,  'color': (255, 80, 140)},
                {'type': 'flower',   'size': 5,  'color': (255, 200, 80)},
                {'type': 'mushroom', 'size': 6,  'color': (220, 60, 60)},
                {'type': 'log',      'size': 14, 'color': (110, 70, 35)},
            ],
            'nether': [
                {'type': 'rock',     'size': 12, 'color': (60, 20, 15)},
                {'type': 'rock',     'size': 10, 'color': (90, 30, 20)},
                {'type': 'bush',     'size': 7,  'color': (120, 40, 10)},
                {'type': 'skull',    'size': 8,  'color': (200, 180, 160)},
                {'type': 'crystal',  'size': 9,  'color': (255, 110, 30)},
                {'type': 'mushroom', 'size': 6,  'color': (180, 40, 30)},
            ],
            'void': [
                {'type': 'rock',     'size': 12, 'color': (60, 30, 90)},
                {'type': 'rock',     'size': 9,  'color': (90, 50, 140)},
                {'type': 'bush',     'size': 6,  'color': (110, 70, 170)},
                {'type': 'crystal',  'size': 10, 'color': (160, 80, 255)},
                {'type': 'crystal',  'size': 8,  'color': (220, 180, 255)},
                {'type': 'skull',    'size': 8,  'color': (180, 160, 220)},
            ],
            'celestial': [
                {'type': 'tree',     'size': 16, 'color': (180, 200, 160)},
                {'type': 'bush',     'size': 8,  'color': (220, 210, 150)},
                {'type': 'rock',     'size': 11, 'color': (230, 230, 240)},
                {'type': 'flower',   'size': 5,  'color': (255, 240, 180)},
                {'type': 'crystal',  'size': 9,  'color': (255, 220, 120)},
                {'type': 'orb',      'size': 8,  'color': (255, 250, 200)},
            ],
        }
        vegetation_types = dim_vegetation.get(self.current_dimension, dim_vegetation['overworld'])

        num_vegetation = random.randint(20, 40)
        veg_margin = 8  # keep scenery clear of building footprints
        veg_liquid = {'water', 'lava', 'void'}  # never place scenery on impassable liquid
        for _ in range(num_vegetation):
            veg = random.choice(vegetation_types).copy()
            for _attempt in range(10):
                vx = base_x + random.randint(50, self.chunk_size - 50)
                vy = base_y + random.randint(50, self.chunk_size - 50)
                if self._classify_terrain_for_dim(vx, vy) in veg_liquid:
                    continue
                if not any(b['x'] - veg_margin <= vx <= b['x'] + b['width'] + veg_margin and
                           b['y'] - veg_margin <= vy <= b['y'] + b['height'] + veg_margin
                           for b in chunk_data['buildings']):
                    veg['x'] = vx
                    veg['y'] = vy
                    chunk_data['vegetation'].append(veg)
                    break
        
        # Generate enemies using dimension-specific mob pool
        impassable = {'water', 'lava', 'void'}
        chunk_dist = abs(chunk_x) + abs(chunk_y)
        tier = min(3, chunk_dist // 2)
        dim_pool_tiers = DIMENSION_MOBS.get(self.current_dimension, MOB_TIERS)
        mob_pool = dim_pool_tiers[tier]
        base_count = random.randint(6, 12)
        num_enemies = max(1, int(base_count * self.difficulty['enemy_count']))
        for i in range(num_enemies):
            # Find a passable spawn tile (no water/lava/void in any dimension)
            enemy_x = enemy_y = None
            for _ in range(12):
                tx = base_x + random.randint(100, self.chunk_size - 100)
                ty = base_y + random.randint(100, self.chunk_size - 100)
                if self._classify_terrain_for_dim(tx, ty) not in impassable:
                    enemy_x, enemy_y = tx, ty
                    break
            if enemy_x is None:
                continue  # couldn't find dry ground, skip this enemy
            level = random.randint(1, 3 + chunk_dist)
            mob_name = random.choice(mob_pool)
            enemy = create_mob(mob_name, level, enemy_x, enemy_y)
            enemy.max_hp = max(1, int(enemy.max_hp * self.difficulty['enemy_hp']))
            enemy.hp = enemy.max_hp
            enemy.damage = max(1, int(enemy.damage * self.difficulty['enemy_dmg']))
            enemy.dimension = self.current_dimension
            chunk_data['enemies'].append(enemy)
            self.enemies.append(enemy)

        # Add treasure chests (avoid impassable terrain)
        num_chests = random.randint(2, 5)
        rarity_table = ['common'] * 6 + ['uncommon'] * 3 + ['rare']
        for _ in range(num_chests):
            for attempt in range(30):
                cx = base_x + random.randint(80, self.chunk_size - 80)
                cy = base_y + random.randint(80, self.chunk_size - 80)
                if self._classify_terrain_for_dim(cx, cy) not in impassable:
                    chunk_data['interactables'].append({
                        'type': 'chest', 'x': cx, 'y': cy,
                        'width': 20, 'height': 16,
                        'opened': False, 'rarity': random.choice(rarity_table),
                        'action': 'open_chest'
                    })
                    break

        # Spawn a dimension portal rarely (~1-in-12 chunks)
        if random.random() < 0.08 and self.current_dimension in DIMENSION_PORTAL:
            info = DIMENSION_PORTAL[self.current_dimension]
            px = base_x + random.randint(150, self.chunk_size - 150)
            py = base_y + random.randint(150, self.chunk_size - 150)
            chunk_data['interactables'].append({
                'type': 'portal', 'x': px, 'y': py,
                'width': 40, 'height': 56,
                'destination': info['dest'],
                'color': info['color'],
                'label': info['label'],
                'action': 'use_portal'
            })

        # Beacon: fast-travel totem back to this dimension's settlement (~10% per chunk)
        beacon_colors = {
            'overworld': (140, 200, 255),
            'nether':    (255, 140, 60),
            'void':      (180, 120, 255),
            'celestial': (255, 230, 150),
        }
        if random.random() < 0.10:
            for _ in range(30):
                bxn = base_x + random.randint(150, self.chunk_size - 150)
                byn = base_y + random.randint(150, self.chunk_size - 150)
                if self._classify_terrain_for_dim(bxn, byn) not in impassable:
                    chunk_data['interactables'].append({
                        'type': 'beacon', 'x': bxn, 'y': byn,
                        'width': 28, 'height': 60,
                        'color': beacon_colors.get(self.current_dimension, (180, 220, 255)),
                        'action': 'use_beacon',
                    })
                    break

        # Dungeon entrance (~8% chance per chunk; tougher the further out you go)
        if random.random() < 0.08:
            for _ in range(30):
                dxe = base_x + random.randint(160, self.chunk_size - 160)
                dye = base_y + random.randint(160, self.chunk_size - 160)
                if self._classify_terrain_for_dim(dxe, dye) not in impassable:
                    chunk_data['interactables'].append({
                        'type': 'dungeon', 'x': dxe, 'y': dye,
                        'width': 48, 'height': 56,
                        'tier': min(3, (abs(chunk_x) + abs(chunk_y)) // 2),
                        'dimension': self.current_dimension,
                        'dtype': random.choice(list(DUNGEON_TYPES.keys())),
                        'action': 'enter_dungeon',
                    })
                    break

        self.world_chunks[chunk_key] = chunk_data
    
    def update_world_loading(self):
        """Update which chunks are loaded based on player position"""
        if not self.player:
            return
        
        # Determine which chunk the player is in
        player_chunk_x = int(self.player.x // self.chunk_size)
        player_chunk_y = int(self.player.y // self.chunk_size)
        
        # Handle negative coordinates properly
        if self.player.x < 0:
            player_chunk_x = int((self.player.x - self.chunk_size + 1) // self.chunk_size)
        if self.player.y < 0:
            player_chunk_y = int((self.player.y - self.chunk_size + 1) // self.chunk_size)
        
        # Update camera to follow player
        self.camera_x = self.player.x - SCREEN_WIDTH // 2
        self.camera_y = self.player.y - SCREEN_HEIGHT // 2
        
        # Load chunks in a 3x3 grid around the player
        chunks_to_load = set()
        for dx in range(-1, 2):
            for dy in range(-1, 2):
                chunk_x = player_chunk_x + dx
                chunk_y = player_chunk_y + dy
                chunks_to_load.add((chunk_x, chunk_y))
        
        # Generate new chunks if needed
        for chunk_pos in chunks_to_load:
            if chunk_pos not in self.loaded_chunks:
                self.generate_world_chunk(chunk_pos[0], chunk_pos[1])
                self.loaded_chunks.add(chunk_pos)
        
        # Update current chunk position
        self.current_chunk_x = player_chunk_x
        self.current_chunk_y = player_chunk_y
    
    def get_visible_objects(self, object_type):
        """Get all objects of a specific type that are visible on screen.

        Two-level cull: skip whole chunks that lie off-screen, then check each
        remaining object. The chunk-level cull is the big win — terrain has
        ~1024 tiles per chunk and we have 9 chunks loaded, so eliminating
        even one chunk saves ~1k per-object checks.
        """
        visible_objects = []
        margin = 200
        visible_left = self.camera_x - margin
        visible_right = self.camera_x + SCREEN_WIDTH + margin
        visible_top = self.camera_y - margin
        visible_bottom = self.camera_y + SCREEN_HEIGHT + margin
        chunk_size = self.chunk_size

        for (chunk_x, chunk_y), chunk_data in self.world_chunks.items():
            # Chunk bounding box
            cleft = chunk_x * chunk_size
            ctop = chunk_y * chunk_size
            cright = cleft + chunk_size
            cbottom = ctop + chunk_size
            # If the chunk is fully off-screen, skip every object it owns
            if (cright < visible_left or cleft > visible_right or
                    cbottom < visible_top or ctop > visible_bottom):
                continue
            objs = chunk_data.get(object_type)
            if not objs:
                continue
            for obj in objs:
                obj_x = obj.get('x', 0)
                obj_y = obj.get('y', 0)
                obj_width = obj.get('width', obj.get('size', 32))
                obj_height = obj.get('height', obj.get('size', 32))
                if (obj_x + obj_width >= visible_left and
                        obj_x <= visible_right and
                        obj_y + obj_height >= visible_top and
                        obj_y <= visible_bottom):
                    visible_objects.append(obj)
        return visible_objects
    
    def handle_events(self):
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                return False

            # Global F11 toggle — works in every state
            if event.type == pygame.KEYDOWN and event.key == pygame.K_F11:
                self.toggle_fullscreen()
                continue

            if self.state == "menu":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_RETURN:
                        self.state = "char_creation"
                    elif event.key in (pygame.K_l, pygame.K_c) and self.has_save():
                        self.save_select_index = 0
                        self.state = "save_select"
                    elif event.key == pygame.K_q and not IS_WEB:  # nothing to quit to in a browser tab
                        return False

            elif self.state == "save_select":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.state = "menu"
                    elif event.key == pygame.K_UP:
                        self.save_select_index = (self.save_select_index - 1) % SAVE_SLOTS
                    elif event.key == pygame.K_DOWN:
                        self.save_select_index = (self.save_select_index + 1) % SAVE_SLOTS
                    elif event.key in (pygame.K_1, pygame.K_2, pygame.K_3):
                        slot = {pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 3}[event.key]
                        if self.has_save(slot):
                            self.load_game(slot)
                    elif event.key == pygame.K_RETURN:
                        slot = self.save_select_index + 1
                        if self.has_save(slot):
                            self.load_game(slot)
                    elif event.key == pygame.K_d:
                        # Delete the highlighted slot
                        slot = self.save_select_index + 1
                        for pth in (save_path_for_slot(slot), meta_path_for_slot(slot)):
                            try:
                                if os.path.exists(pth):
                                    os.remove(pth)
                                web_forget(pth)
                            except Exception:
                                pass

            elif self.state == "char_creation":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        # Back to title screen
                        self.player_name = ""
                        self.state = "menu"
                    elif event.key == pygame.K_UP:
                        self.selected_class = (self.selected_class - 1) % len(self.classes)
                    elif event.key == pygame.K_DOWN:
                        self.selected_class = (self.selected_class + 1) % len(self.classes)
                    elif event.key == pygame.K_RETURN:
                        if self.player_name:
                            self.state = "difficulty"
                    elif event.key == pygame.K_BACKSPACE:
                        self.player_name = self.player_name[:-1]
                    else:
                        if len(self.player_name) < 20:
                            self.player_name += event.unicode

            elif self.state == "difficulty":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        # Back to title screen
                        self.state = "menu"
                    elif event.key == pygame.K_UP:
                        self.selected_difficulty = (self.selected_difficulty - 1) % len(DIFFICULTIES)
                    elif event.key == pygame.K_DOWN:
                        self.selected_difficulty = (self.selected_difficulty + 1) % len(DIFFICULTIES)
                    elif event.key == pygame.K_RETURN:
                        self.difficulty = DIFFICULTIES[self.selected_difficulty]
                        self.create_character()
                        self.state = "playing"
            
            elif self.state == "playing":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_F5:
                        self.save_game()
                    elif event.key == pygame.K_i:
                        self.inventory_return_state = "playing"
                        self.state = "inventory"
                    elif event.key == pygame.K_j:
                        self.state = "quest_log"
                    elif event.key == pygame.K_h and self.player.mp >= 10:
                        self.player.cast_spell("Heal")
                        self.add_battle_effect(self.player.x + 16, self.player.y + 16, "heal")
                        self.add_screen_flash(GREEN, 5)
                        self.add_combat_log("You heal yourself!")
                    elif event.key == pygame.K_e:
                        # Interaction key - let's debug this thoroughly
                        print("=== E KEY PRESSED ===")
                        print(f"Player position: ({self.player.x}, {self.player.y})")
                        print(f"Total interactable objects: {len(self.interactable_objects)}")
                        
                        # Use infinite world interaction check
                        nearby_object = self.check_interactions_infinite()
                        if nearby_object:
                            print(f"Found nearby object: {nearby_object['type']} - {nearby_object.get('building_type', 'unknown')}")
                            print(f"Interacting with {nearby_object['type']}: {nearby_object.get('building_type', 'unknown')}")
                            self.interact_with_object(nearby_object)
                        else:
                            print("No nearby objects found by check_interactions_infinite()")
                            self.add_combat_log("Nothing to interact with here")
            
            elif self.state == "inside_building":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE:
                        self.state = "playing"
                        self.current_building = None
                        self.add_combat_log("Left the building")
                    elif event.key == pygame.K_r and self.current_building['type'] == 'inn':
                        # Rest at inn — only once; the inn is abandoned afterward
                        if self.current_building.get('abandoned'):
                            self.add_combat_log("This inn is abandoned. There is nowhere to rest.")
                        else:
                            self.player.hp = self.player.max_hp
                            self.player.mp = self.player.max_mp
                            self.current_building['abandoned'] = True
                            self.add_combat_log("You rest and recover all HP and MP!")
                            self.add_combat_log("As you wake, the inn lies abandoned behind you.")
                            self.add_battle_effect(400, 300, "heal")
                            self.add_screen_flash(GREEN, 8)
                    elif self.current_building['type'] == 'temple':
                        # Spend Soul Shards on blessings
                        temple_keys = {pygame.K_1: 1, pygame.K_2: 2, pygame.K_3: 3, pygame.K_4: 4}
                        if event.key in temple_keys:
                            self.temple_blessing(temple_keys[event.key])
                    elif self.current_building['type'] == 'blacksmith':
                        # Pay gold to permanently upgrade an equipped piece of gear
                        forge_keys = {
                            pygame.K_1: 'weapon',
                            pygame.K_2: 'armor',
                            pygame.K_3: 'helmet',
                            pygame.K_4: 'shield',
                            pygame.K_5: 'boots',
                        }
                        if event.key in forge_keys:
                            self.blacksmith_upgrade(forge_keys[event.key])
                    elif self.current_building['type'] == 'shop':
                        # Shop navigation and trading
                        if event.key == pygame.K_UP:
                            self.selected_item_index = (self.selected_item_index - 1) % len(self.shop_inventory)
                        elif event.key == pygame.K_DOWN:
                            self.selected_item_index = (self.selected_item_index + 1) % len(self.shop_inventory)
                        elif event.key == pygame.K_RETURN or event.key == pygame.K_b:
                            # Buy selected item
                            self.buy_item()
                        elif event.key == pygame.K_s:
                            # Sell item (show player inventory for selling)
                            self.start_selling_mode()
            
            elif self.state == "combat":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_a:
                        self.combat_attack()
                    elif event.key == pygame.K_s and self.player.char_class == "Mage":
                        self.combat_spell()
                    elif event.key == pygame.K_h:
                        self.combat_heal()
                    elif event.key == pygame.K_i:
                        # Open inventory mid-fight (pauses combat); return here on close
                        self.inventory_return_state = "combat"
                        self.state = "inventory"
                    elif event.key == pygame.K_r:
                        self.combat_run()

            elif self.state == "boss_arena":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_i:
                        # Open inventory mid-fight (pauses the battle); return here on close
                        self.inventory_return_state = "boss_arena"
                        self.state = "inventory"
                    elif event.key == pygame.K_h and self.player.mp >= 10:
                        self.player.cast_spell("Heal")
                        self.add_battle_effect(self.arena['px'], self.arena['py'], "heal")
                        self.add_screen_flash(GREEN, 5)
                        self.add_combat_log("You heal yourself!")
                    elif event.key == pygame.K_q:
                        self._boss_arena_ultimate()
                    elif event.key == pygame.K_b:
                        self._boss_arena_skill()

            elif self.state == "dungeon":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_e:
                        self._dungeon_interact()
                    elif event.key == pygame.K_i:
                        self.inventory_return_state = "dungeon"
                        self.state = "inventory"
                    elif event.key == pygame.K_h and self.player.mp >= 10:
                        self.player.cast_spell("Heal")
                        self.add_battle_effect(self.dungeon['px'], self.dungeon['py'], "heal")
                        self.add_screen_flash(GREEN, 5)
                        self.add_combat_log("You heal yourself!")
                    elif event.key == pygame.K_q:
                        self._dungeon_ultimate()
                    elif event.key == pygame.K_b:
                        self._dungeon_skill()

            elif self.state == "inventory":
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_ESCAPE or event.key == pygame.K_i:
                        self.state = self.inventory_return_state
                    elif event.key == pygame.K_UP:
                        if hasattr(self.player, 'inventory') and self.player.inventory:
                            self.inventory_selected_index = (self.inventory_selected_index - 1) % len(self.player.inventory)
                    elif event.key == pygame.K_DOWN:
                        if hasattr(self.player, 'inventory') and self.player.inventory:
                            self.inventory_selected_index = (self.inventory_selected_index + 1) % len(self.player.inventory)
                    elif event.key == pygame.K_RETURN or event.key == pygame.K_e:
                        # Equip/use selected item
                        self.use_inventory_item()
                    elif event.key == pygame.K_u:
                        # Unequip items
                        self.show_unequip_options()
                    elif event.key == pygame.K_d:
                        # Discard the selected item permanently
                        self.discard_inventory_item()

            elif self.state == "quest_log":
                if event.type == pygame.KEYDOWN:
                    if event.key in (pygame.K_j, pygame.K_ESCAPE):
                        self.state = "playing"

            elif self.state in ("victory", "game_over"):
                if event.type == pygame.KEYDOWN:
                    if event.key == pygame.K_q and not IS_WEB:
                        return False
                    elif event.key == pygame.K_RETURN:
                        self.reset_to_menu()
                    elif event.key == pygame.K_c and self.state == "victory" and self.player:
                        # Continue exploring after defeating Solgareth
                        self.state = "playing"
                        # Make sure we're somewhere safe
                        if self.current_dimension != 'overworld':
                            self.switch_dimension('overworld')
                        self.player.spawn_shield = 240
                        self.add_combat_log("The realms still await — your journey continues.")

        return True

    def reset_to_menu(self):
        """Wipe run-specific world state and return to the main menu."""
        self.enemies = []
        self.current_enemy = None
        self.current_building = None
        self.current_dimension = 'overworld'
        self.all_dimension_chunks = {'overworld': {}, 'nether': {}, 'void': {}, 'celestial': {}}
        self.all_loaded_chunks = {'overworld': set(), 'nether': set(), 'void': set(), 'celestial': set()}
        self.world_chunks = self.all_dimension_chunks['overworld']
        self.loaded_chunks = self.all_loaded_chunks['overworld']
        self.dimension_player_pos = {}
        self.boss_spawned = False
        self.in_boss_fight = False
        self.dungeon = None
        self.player = None
        self.player_name = ""
        self.state = "menu"

    def toggle_fullscreen(self):
        """Flip between fullscreen and windowed display. F11."""
        if IS_WEB:  # the browser owns fullscreen (its own F11 works)
            return
        self.fullscreen = not self.fullscreen
        flags = pygame.SCALED | pygame.RESIZABLE
        if self.fullscreen:
            flags |= pygame.FULLSCREEN
        self.screen = pygame.display.set_mode((SCREEN_WIDTH, SCREEN_HEIGHT), flags)
        self.add_combat_log("Fullscreen on" if self.fullscreen else "Fullscreen off")

    def save_game(self, auto=False, force=False, label=None, slot=None):
        """Serialize the current run to a save slot. Saving is blocked during battle (unless forced)."""
        if not self.player:
            return False
        if self.state in ("combat", "boss_arena", "dungeon") and not force:
            if not auto:
                self.add_combat_log("You can't save during battle!")
            return False
        if slot is None:
            slot = getattr(self, 'current_slot', 1)
        # Keep the live dimension's chunks in sync before saving
        self.all_dimension_chunks[self.current_dimension] = self.world_chunks
        self.all_loaded_chunks[self.current_dimension] = self.loaded_chunks
        state = {
            'version': 1,
            'player': self.player,
            'player_name': self.player_name,
            'enemies': self.enemies,
            'all_dimension_chunks': self.all_dimension_chunks,
            'all_loaded_chunks': self.all_loaded_chunks,
            'dimension_player_pos': self.dimension_player_pos,
            'current_dimension': self.current_dimension,
            'selected_difficulty': self.selected_difficulty,
            'boss_spawned': self.boss_spawned,
            'shop_inventory': self.shop_inventory,
            'shop_restock_timer': self.shop_restock_timer,
            'camera': (self.camera_x, self.camera_y),
        }
        try:
            path = save_path_for_slot(slot)
            with open(path, 'wb') as f:
                pickle.dump(state, f)
            # Sidecar with human-readable summary so we can show slot info fast
            try:
                with open(meta_path_for_slot(slot), 'w') as m:
                    m.write("|".join([
                        str(getattr(self.player, 'name', '?')),
                        str(getattr(self.player, 'char_class', '?')),
                        str(getattr(self.player, 'level', 1)),
                        str(self.current_dimension),
                        DIFFICULTIES[self.selected_difficulty]['name'],
                    ]))
            except Exception:
                pass
            for pth in (path, meta_path_for_slot(slot)):
                if os.path.exists(pth):
                    web_persist(pth)
            if not force:
                if label:
                    self.add_combat_log(label)
                else:
                    self.add_combat_log(("Auto-saved" if auto else "Game saved") + f" to slot {slot}.")
            return True
        except Exception as e:
            print(f"Save error: {e}")
            self.add_combat_log("Save failed!")
            return False

    def has_save(self, slot=None):
        """True if any slot has a save (or the specific slot if given)."""
        if slot is not None:
            return os.path.exists(save_path_for_slot(slot))
        if os.path.exists(SAVE_PATH):
            return True
        return any(os.path.exists(save_path_for_slot(s)) for s in range(1, SAVE_SLOTS + 1))

    def read_slot_summary(self, slot):
        """Return a dict summarising the save in this slot, or None if empty."""
        if not os.path.exists(save_path_for_slot(slot)):
            return None
        mp = meta_path_for_slot(slot)
        if os.path.exists(mp):
            try:
                parts = open(mp).read().split("|")
                return {
                    'name':      parts[0] if len(parts) > 0 else '?',
                    'class':     parts[1] if len(parts) > 1 else '?',
                    'level':     parts[2] if len(parts) > 2 else '?',
                    'dimension': parts[3] if len(parts) > 3 else '?',
                    'difficulty': parts[4] if len(parts) > 4 else '?',
                }
            except Exception:
                pass
        return {'name': 'Save', 'class': '?', 'level': '?', 'dimension': '?', 'difficulty': '?'}

    def load_game(self, slot=None):
        """Restore a saved run from a slot. If no slot given, prefer legacy file, then slot 1."""
        if slot is None:
            if os.path.exists(SAVE_PATH):
                path = SAVE_PATH
                slot = 1
            else:
                for s in range(1, SAVE_SLOTS + 1):
                    if os.path.exists(save_path_for_slot(s)):
                        slot = s
                        break
                else:
                    return False
                path = save_path_for_slot(slot)
        else:
            path = save_path_for_slot(slot)
        if not os.path.exists(path):
            return False
        try:
            with open(path, 'rb') as f:
                state = pickle.load(f)
        except Exception as e:
            print(f"Load error: {e}")
            return False

        self.current_slot = slot

        self.player = state['player']
        self.player_name = state.get('player_name', getattr(self.player, 'name', 'Hero'))
        self.enemies = state['enemies']
        self.all_dimension_chunks = state['all_dimension_chunks']
        self.all_loaded_chunks = state['all_loaded_chunks']
        self.dimension_player_pos = state['dimension_player_pos']
        self.current_dimension = state['current_dimension']
        self.world_chunks = self.all_dimension_chunks[self.current_dimension]
        self.loaded_chunks = self.all_loaded_chunks[self.current_dimension]
        self.selected_difficulty = state.get('selected_difficulty', 1)
        self.difficulty = DIFFICULTIES[self.selected_difficulty]
        self.boss_spawned = state.get('boss_spawned', False)
        self.shop_inventory = state.get('shop_inventory', self.shop_inventory)
        self.shop_restock_timer = state.get('shop_restock_timer', 0)
        self.camera_x, self.camera_y = state.get('camera', (0, 0))

        # Reset transient combat/run state
        self.current_enemy = None
        self.current_building = None
        self.in_boss_fight = False
        self.state = "playing"
        self.update_world_loading()
        self._migrate_loaded_save()
        self.add_combat_log("Game loaded.")
        return True

    def _migrate_loaded_save(self):
        """Patch older saved chunks to include features added after they were generated."""
        # 1) Resurrection Altar in the Celestial settlement
        cel_chunks = self.all_dimension_chunks.get('celestial', {})
        chunk = cel_chunks.get((0, 0))
        if chunk:
            interactables = chunk.setdefault('interactables', [])
            if not any(i.get('type') == 'altar' for i in interactables):
                center = self.chunk_size // 2
                interactables.append({
                    'type': 'altar', 'x': center, 'y': 250,
                    'width': 40, 'height': 56,
                    'action': 'use_altar',
                })
                self.add_combat_log("A Resurrection Altar has appeared in the Celestial settlement.")

    def create_character(self):
        char_class = self.classes[self.selected_class]
        
        # Find a safe starting position on grass terrain
        start_x = 512  # Start in middle of first chunk
        start_y = 512
        
        # Create character at safe position
        self.player = Character(self.player_name, char_class, start_x, start_y)
        self.player.gold = self.difficulty['start_gold']
        self.player.spawn_shield = 300  # 5 seconds of spawn protection at 60 FPS
        
        print(f"Created character '{self.player_name}' as {char_class} at position ({self.player.x}, {self.player.y})")
        print(f"Character speed: {self.player.speed}")
        
        # Initialize camera to center on player
        self.camera_x = self.player.x - SCREEN_WIDTH // 2
        self.camera_y = self.player.y - SCREEN_HEIGHT // 2
        
        print(f"Camera position: ({self.camera_x}, {self.camera_y})")
        
        # Initialize the infinite world system
        self.generate_world_chunk(0, 0)  # Generate initial chunk at origin
        self.loaded_chunks.add((0, 0))
        
        # Update world to load surrounding chunks
        self.update_world_loading()
        
        # Find a clear starting position if current one is blocked
        if self.is_position_blocked_infinite(self.player.x, self.player.y):
            print("Initial position blocked, finding clear area...")
            # Try different positions in a spiral pattern
            for radius in range(32, 256, 32):
                for angle in range(0, 360, 45):
                    test_x = start_x + radius * math.cos(math.radians(angle))
                    test_y = start_y + radius * math.sin(math.radians(angle))
                    if not self.is_position_blocked_infinite(test_x, test_y):
                        self.player.x = test_x
                        self.player.y = test_y
                        print(f"Found clear position at ({test_x}, {test_y})")
                        break
                else:
                    continue
                break
        
        # Update camera after finding position
        self.camera_x = self.player.x - SCREEN_WIDTH // 2
        self.camera_y = self.player.y - SCREEN_HEIGHT // 2
        
        print(f"Final player position: ({self.player.x}, {self.player.y})")
        print(f"Loaded chunks: {self.loaded_chunks}")
        print(f"Number of world chunks: {len(self.world_chunks)}")
        
        # FORCE create interactables after character is created
        print("=== FORCING INTERACTABLES CREATION ===")
        self.create_interactables()
        print(f"Interactables after force creation: {len(self.interactable_objects)}")
    

    # ---- Fighting animation ------------------------------------------------
    # Entities carry two countdown timers: `atk_anim` while they are swinging
    # and `hurt_anim` while they are reeling from a hit. Both tick down in
    # update(); draw code turns them into offsets, arcs and flashes.
    ATK_FRAMES = ATK_ANIM_FRAMES
    HURT_FRAMES = HURT_ANIM_FRAMES

    def start_attack_anim(self, entity):
        """Begin a wind-up/strike/recover animation on an attacker."""
        if entity is not None:
            entity.atk_anim = self.ATK_FRAMES

    def start_hurt_anim(self, entity):
        """Begin a recoil + flash animation on something that just took a hit."""
        if entity is not None:
            entity.hurt_anim = self.HURT_FRAMES

    @classmethod
    def attack_phase(cls, entity):
        """Map an attacker's timer to (lunge, swing) where lunge is -0.4..1.0
        (back during wind-up, forward on the strike) and swing is the arc
        progress 0..1, or None when no arc should be drawn this frame."""
        timer = getattr(entity, 'atk_anim', 0) or 0
        if timer <= 0:
            return 0.0, None
        p = 1.0 - (timer / cls.ATK_FRAMES)      # 0 at start -> 1 at end
        if p < 0.25:                             # wind up: pull back
            lunge = -0.4 * (p / 0.25)
        elif p < 0.5:                            # strike: snap forward
            lunge = -0.4 + 1.4 * ((p - 0.25) / 0.25)
        else:                                    # recover: ease home
            lunge = 1.0 * (1.0 - (p - 0.5) / 0.5)
        swing = None
        if 0.2 <= p <= 0.62:
            swing = (p - 0.2) / 0.42
        return lunge, swing

    @classmethod
    def hurt_phase(cls, entity):
        """Map a victim's timer to 0..1, 1 being the instant of impact."""
        timer = getattr(entity, 'hurt_anim', 0) or 0
        if timer <= 0:
            return 0.0
        return timer / cls.HURT_FRAMES

    def anim_offset(self, entity, facing=1, reach=22):
        """Pixel (dx, dy) to shift a sprite: lunge forward, recoil backward."""
        lunge, _ = self.attack_phase(entity)
        hurt = self.hurt_phase(entity)
        f = 1 if facing >= 0 else -1
        dx = f * lunge * reach
        # Knocked away from the attacker, with a small vertical hop
        dx -= f * hurt * 9
        dy = -abs(math.sin(hurt * math.pi)) * 4
        return int(dx), int(dy)

    def hurt_tint(self, entity, color):
        """Blend a colour toward white at the moment of impact."""
        hurt = self.hurt_phase(entity)
        if hurt <= 0:
            return color
        k = hurt * 0.75
        return tuple(min(255, int(c + (255 - c) * k)) for c in color)

    def draw_swing_arc(self, surface, entity, cx, cy, radius, facing=1,
                       color=(255, 255, 210)):
        """Draw the weapon arc sweeping through its swing, if mid-attack."""
        _, swing = self.attack_phase(entity)
        if swing is None:
            return
        f = 1 if facing >= 0 else -1
        # Sweep from high behind to low in front
        a0 = -1.25 + 2.5 * swing
        span = 0.55
        rect = (int(cx - radius), int(cy - radius), radius * 2, radius * 2)
        if f > 0:
            start, end = a0 - span, a0
        else:
            start, end = math.pi - a0, math.pi - a0 + span
        # Fade the trail out as the swing completes
        fade = 1.0 - 0.6 * swing
        col = tuple(int(c * fade) for c in color)
        pygame.draw.arc(surface, col, rect, start, end, 4)
        pygame.draw.arc(surface, (255, 255, 255), rect, start, end, 1)

    def update(self):
        # Update battle effects
        for effect in self.battle_effects[:]:
            if not effect.update():
                self.battle_effects.remove(effect)
        
        # Tick fighting animations on the player and every enemy
        for ent in ([self.player] if self.player else []) + list(self.enemies or []):
            if getattr(ent, 'atk_anim', 0) > 0:
                ent.atk_anim -= 1
            if getattr(ent, 'hurt_anim', 0) > 0:
                ent.hurt_anim -= 1
        if self.current_enemy is not None and self.current_enemy not in (self.enemies or []):
            if getattr(self.current_enemy, 'atk_anim', 0) > 0:
                self.current_enemy.atk_anim -= 1
            if getattr(self.current_enemy, 'hurt_anim', 0) > 0:
                self.current_enemy.hurt_anim -= 1

        # Update screen effects
        if self.screen_shake > 0:
            self.screen_shake -= 1
        
        if self.flash_screen > 0:
            self.flash_screen -= 1
        
        if self.log_timer > 0:
            self.log_timer -= 1

        # Surface any pending level-up upgrade messages and flash on level up
        msgs = getattr(self.player, 'pending_level_messages', None) if self.player else None
        if msgs:
            for msg in msgs:
                self.add_combat_log(msg)
            self.add_screen_flash(YELLOW, 12)
            self.player.pending_level_messages = []

        # Tick down ability cooldowns (state-agnostic so they recover in menus too)
        if self.player and getattr(self.player, 'ultimate_cd', 0) > 0:
            self.player.ultimate_cd -= 1
        if self.player and getattr(self.player, 'skill_cd', 0) > 0:
            self.player.skill_cd -= 1

        # Alderine regeneration: 1 HP every 10 frames (~6 HP/sec) at a steady rate
        if self.player and getattr(self.player, 'alderine_heal_left', 0) > 0:
            self.player.alderine_tick_cd -= 1
            if self.player.alderine_tick_cd <= 0:
                self.player.alderine_tick_cd = 10
                if self.player.hp < self.player.max_hp:
                    self.player.hp = min(self.player.max_hp, self.player.hp + 1)
                self.player.alderine_heal_left -= 1

        # Shops slowly restock over time, and fully restock the moment you die
        if self.state == "playing":
            self.shop_restock_timer += 1
            if self.shop_restock_timer >= self.shop_restock_interval:
                self.shop_restock_timer = 0
                self.restock_shop(full=False)
                self.add_combat_log("The shops have restocked.")
        if self.state == "game_over" and not self._was_game_over:
            self.restock_shop(full=True)
        self._was_game_over = (self.state == "game_over")

        # MP regeneration - 1 point per second
        # Hard/Nightmare deny MP recovery while browsing the inventory
        inv_mp_blocked = self.state == "inventory" and getattr(self, 'selected_difficulty', 1) >= 2
        if self.player and not inv_mp_blocked:
            self.mp_regen_timer += 1
            if self.mp_regen_timer >= self.mp_regen_rate:
                self.mp_regen_timer = 0
                if self.player.mp < self.player.max_mp:
                    self.player.mp += 1
                    # Optional: Add a small visual effect for MP regen
                    if self.state == "playing":
                        self.add_battle_effect(self.player.x + 16, self.player.y, "mp_regen")
        
        if self.state == "playing":
            # Update world loading first to ensure chunks are loaded
            if self.player:
                self.update_world_loading()
            
            # Handle player movement
            keys = pygame.key.get_pressed()
            dx = dy = 0
            if keys[pygame.K_LEFT] or keys[pygame.K_a]:
                dx = -1
            if keys[pygame.K_RIGHT] or keys[pygame.K_d]:
                dx = 1
            if keys[pygame.K_UP] or keys[pygame.K_w]:
                dy = -1
            if keys[pygame.K_DOWN] or keys[pygame.K_s]:
                dy = 1

            # Remember the last horizontal heading so the sprite stays in profile
            if dx != 0:
                self.player.facing = dx

            # Always normalise to base walking speed every frame so dimension
            # transitions / interrupted sprints can never leave it in a weird state.
            base = getattr(self.player, 'base_speed', 3)
            self.player.speed = base

            # Sprinting — hold Shift, costs stamina; also lets you dash across water
            shift_held = keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]
            sprinting = shift_held and self.player.stamina > 0

            if dx != 0 or dy != 0:
                if sprinting:
                    drain = 1.5 * self.difficulty['stamina_drain']
                    self.player.stamina = max(0, self.player.stamina - drain)
                    self.player.speed = base * 2

                old_x, old_y = self.player.move(dx, dy)

                if sprinting:
                    self.player.speed = base

                # Water can be crossed while sprinting; all other terrain rules still apply
                blocked = self.is_position_blocked_infinite(self.player.x, self.player.y,
                                                            ignore_water=sprinting)
                if blocked:
                    self.player.x = old_x
                    self.player.y = old_y

                # Dashing across lava burns you (spawn shield protects)
                if sprinting and not blocked:
                    shielded = getattr(self.player, 'spawn_shield', 0) > 0
                    if not shielded and self.terrain_type_at(self.player.x, self.player.y) == 'lava':
                        burn = 2
                        self.player.hp = max(0, self.player.hp - burn)
                        if getattr(self, '_lava_burn_msg_cd', 0) <= 0:
                            self.add_combat_log("The lava scorches you! (-2 HP)")
                            self._lava_burn_msg_cd = 30
                        if self.player.hp <= 0:
                            self.state = "game_over"

                if getattr(self, '_lava_burn_msg_cd', 0) > 0:
                    self._lava_burn_msg_cd -= 1

                self.update_world_loading()
                nearby_object = self.check_interactions_infinite()
                if nearby_object and nearby_object not in getattr(self, '_last_nearby', []):
                    print(f"Now near: {nearby_object['type']} - {nearby_object.get('building_type', 'unknown')}")
                self._last_nearby = [nearby_object] if nearby_object else []

            # Stamina regens only when Shift is released
            if not shift_held:
                regen = self.player.stamina_regen * (1.5 if dx == 0 and dy == 0 else 1.0)
                self.player.stamina = min(self.player.max_stamina, self.player.stamina + regen)
            
            # Tick down spawn shield
            if hasattr(self.player, 'spawn_shield') and self.player.spawn_shield > 0:
                self.player.spawn_shield -= 1

            # Update enemies
            shielded = hasattr(self.player, 'spawn_shield') and self.player.spawn_shield > 0
            for enemy in self.enemies:
                if enemy.alive and self.is_enemy_visible(enemy):
                    # Slow them to a crawl while wading through water
                    if self.terrain_type_at(enemy.x, enemy.y) == 'water':
                        original_speed = enemy.speed
                        enemy.speed = original_speed * 0.4
                        enemy.update(self.player)
                        enemy.speed = original_speed
                    else:
                        enemy.update(self.player)

                    # Check for combat (blocked while spawn shield is active)
                    if not shielded:
                        distance = math.sqrt((enemy.x - self.player.x)**2 + (enemy.y - self.player.y)**2)
                        if distance <= enemy.attack_range:
                            # Auto-save right before the fight begins (still in "playing")
                            self.save_game(auto=True)
                            self.current_enemy = enemy
                            if getattr(enemy, 'is_final_boss', False):
                                # Pulled into a real-time sealed battlefield — no escape
                                self.in_boss_fight = True
                                self.start_boss_arena()
                                self.add_combat_log(f"{enemy.name} drags you into a sealed battlefield!")
                                self.add_combat_log("There is no escape. Fight or die!")
                                self.add_screen_flash((180, 60, 220), 18)
                            else:
                                self.state = "combat"
                                self.add_combat_log(f"Encountered {enemy.name}!")
                            break
        
        elif self.state == "combat":
            # Combat is turn-based, handled by events
            pass

        elif self.state == "boss_arena":
            self.update_boss_arena()

        elif self.state == "dungeon":
            self.update_dungeon()

        elif self.state == "inventory":
            # Easy/Normal let you catch your breath in the menu; Hard/Nightmare do not
            if self.player and getattr(self, 'selected_difficulty', 1) <= 1:
                self.player.stamina = min(self.player.max_stamina,
                                          self.player.stamina + self.player.stamina_regen)

    ARENA_SIZE = (2200, 1500)   # the sealed battlefield is much larger than the screen
    # Dungeon dimensions are computed per-instance now (each room = full screen)

    # Class ultimates unlocked at level 10. Each entry: (name, cooldown_frames, mp_cost)
    ULTIMATES = {
        'Warrior': ('Whirlwind',     480, 0),
        'Mage':    ('Meteor',        600, 30),
        'Rogue':   ('Shadow Strike', 360, 0),
    }

    # Class-specific skill on the B key. Each entry: (name, cooldown_frames, mp_cost, stamina_cost)
    CLASS_SKILLS = {
        'Warrior': ('Shield Block', 240, 0, 10),
        'Rogue':   ('Super Dash',   180, 0, 25),
    }

    def _skill_ready(self):
        if not self.player:
            return False
        spec = self.CLASS_SKILLS.get(self.player.char_class)
        if not spec:
            return False
        _, _, mp, sta = spec
        return (self.player.skill_cd <= 0 and self.player.mp >= mp
                and self.player.stamina >= sta)

    def _use_class_skill(self, arena_state, bounds):
        """Apply the Warrior/Rogue skill to a real-time arena state dict."""
        if not self._skill_ready():
            if not self.player or self.player.char_class not in self.CLASS_SKILLS:
                self.add_combat_log("Your class has no special skill.")
                return False
            if self.player.skill_cd > 0:
                self.add_combat_log(f"Skill recharging ({self.player.skill_cd // 60 + 1}s)")
            else:
                self.add_combat_log("Not enough stamina!")
            return False

        name, cd, mp, sta = self.CLASS_SKILLS[self.player.char_class]
        self.player.mp -= mp
        self.player.stamina = max(0, self.player.stamina - sta)
        self.player.skill_cd = cd

        if self.player.char_class == "Warrior":
            # Shield Block: 60 frames of damage immunity
            arena_state['p_iframes'] = max(arena_state.get('p_iframes', 0), 60)
            self.add_screen_flash((100, 180, 255), 6)
            self.add_combat_log(f"{name}! Damage blocked for 1 second.")
        elif self.player.char_class == "Rogue":
            # Super Dash: long teleport-style dash + brief i-frames
            left, top, right, bottom = bounds
            fxd, fyd = arena_state.get('face', (0.0, -1.0))
            mag = math.hypot(fxd, fyd) or 1
            fxd, fyd = fxd / mag, fyd / mag
            dash = 220
            arena_state['px'] = max(left, min(right, arena_state['px'] + fxd * dash))
            arena_state['py'] = max(top, min(bottom, arena_state['py'] + fyd * dash))
            arena_state['p_iframes'] = max(arena_state.get('p_iframes', 0), 30)
            self.add_screen_flash((140, 100, 220), 6)
            self.add_combat_log(f"{name}! You dart across the battlefield.")
        return True

    def _boss_arena_skill(self):
        if self.state != "boss_arena" or not getattr(self, 'arena', None):
            return
        W, H = self.ARENA_SIZE
        self._use_class_skill(self.arena, (16, 16, W - 16, H - 16))

    def _dungeon_skill(self):
        if self.state != "dungeon" or not getattr(self, 'dungeon', None):
            return
        d = self.dungeon
        self._use_class_skill(d, (16, 16, d['world_w'] - 16, d['world_h'] - 16))

    def _skill_status(self):
        if not self.player:
            return None
        spec = self.CLASS_SKILLS.get(self.player.char_class)
        if not spec:
            return None
        name, _, mp, sta = spec
        if self.player.skill_cd > 0:
            return (f"B {name}: {self.player.skill_cd // 60 + 1}s", (200, 180, 140))
        if self.player.stamina < sta:
            return (f"B {name}: need {sta} SP", (220, 160, 80))
        return (f"B {name}: READY", (120, 200, 255))

    def _ultimate_ready(self):
        """True if the player meets the requirements to cast their ultimate."""
        if not self.player or self.player.level < 10:
            return False
        spec = self.ULTIMATES.get(self.player.char_class)
        if not spec:
            return False
        _, _, mp = spec
        return self.player.ultimate_cd <= 0 and self.player.mp >= mp

    def _cast_ultimate(self, targets, on_kill_cb, player_pos):
        """
        Apply the class ultimate to a list of (enemy, x, y) targets around player_pos.
        Returns True if the ultimate fired. on_kill_cb(enemy) is called for each kill.
        """
        if not self._ultimate_ready():
            if self.player and self.player.level < 10:
                self.add_combat_log("Ultimate locked until Level 10.")
            elif self.player and self.player.ultimate_cd > 0:
                self.add_combat_log(f"Ultimate recharging ({self.player.ultimate_cd // 60 + 1}s)")
            else:
                self.add_combat_log("Not enough MP for ultimate.")
            return False

        name, cd, mp_cost = self.ULTIMATES[self.player.char_class]
        self.player.mp -= mp_cost
        self.player.ultimate_cd = cd
        px, py = player_pos

        kills = 0; hit_count = 0
        if self.player.char_class == "Warrior":
            # Whirlwind: AOE swing in a 110px radius, 2x melee damage
            radius = 110
            for enemy, ex, ey in targets:
                if not enemy.alive:
                    continue
                if math.hypot(ex - px, ey - py) <= radius:
                    dmg = random.randint(2, 12) + self.player.get_attack_bonus() * 2
                    enemy.take_damage(dmg)
                    self.add_battle_effect(ex, ey, "sword_slash")
                    self.add_battle_effect(ex, ey, "hit")
                    hit_count += 1
                    if not enemy.alive:
                        on_kill_cb(enemy); kills += 1
            self.add_screen_shake(10, 14)
            self.add_screen_flash((255, 230, 120), 10)

        elif self.player.char_class == "Mage":
            # Meteor: huge AOE blast in a 170px radius
            radius = 170
            for enemy, ex, ey in targets:
                if not enemy.alive:
                    continue
                if math.hypot(ex - px, ey - py) <= radius:
                    dmg = random.randint(30, 50) + self.player.intelligence
                    enemy.take_damage(dmg)
                    self.add_battle_effect(ex, ey, "fireball")
                    self.add_battle_effect(ex, ey, "hit")
                    hit_count += 1
                    if not enemy.alive:
                        on_kill_cb(enemy); kills += 1
            self.add_screen_shake(14, 18)
            self.add_screen_flash((255, 140, 40), 18)

        elif self.player.char_class == "Rogue":
            # Shadow Strike: massive single-target hit on closest enemy in range
            radius = 110
            best = None; best_d = radius
            for enemy, ex, ey in targets:
                if not enemy.alive:
                    continue
                d = math.hypot(ex - px, ey - py)
                if d <= best_d:
                    best, best_d = (enemy, ex, ey), d
            if best:
                enemy, ex, ey = best
                dmg = random.randint(40, 60) + self.player.dexterity * 2
                enemy.take_damage(dmg)
                self.add_battle_effect(ex, ey, "sword_slash")
                self.add_battle_effect(ex, ey, "hit")
                hit_count += 1
                if not enemy.alive:
                    on_kill_cb(enemy); kills += 1
            self.add_screen_shake(8, 10)
            self.add_screen_flash((180, 80, 220), 8)

        msg = f"{name}!"
        if hit_count:
            msg += f"  {hit_count} hit" + ("s" if hit_count != 1 else "")
        if kills:
            msg += f", {kills} slain"
        self.add_combat_log(msg)
        return True

    def _ultimate_status(self):
        """Return (label, color) describing the ultimate's current state, or None."""
        if not self.player:
            return None
        spec = self.ULTIMATES.get(self.player.char_class)
        if not spec:
            return None
        name, _, mp = spec
        if self.player.level < 10:
            return (f"Q {name}: unlocks at Lv 10", (150, 150, 150))
        if self.player.ultimate_cd > 0:
            return (f"Q {name}: {self.player.ultimate_cd // 60 + 1}s", (220, 180, 140))
        if self.player.mp < mp:
            return (f"Q {name}: need {mp} MP", (220, 160, 80))
        return (f"Q {name}: READY", (120, 230, 160))

    def _boss_arena_ultimate(self):
        if self.state != "boss_arena" or not getattr(self, 'arena', None):
            return
        a = self.arena
        boss = self.current_enemy
        if not boss:
            return
        targets = [(boss, a['bx'], a['by'])]
        fired = self._cast_ultimate(targets, lambda _e: None, (a['px'], a['py']))
        # Rogue dashes in facing direction and gets brief i-frames
        if fired and self.player.char_class == "Rogue":
            fx, fy = a['face']
            W, H = self.ARENA_SIZE
            a['px'] = max(16, min(W - 16, a['px'] + fx * 110))
            a['py'] = max(16, min(H - 16, a['py'] + fy * 110))
            a['p_iframes'] = max(a['p_iframes'], 60)

    def _dungeon_ultimate(self):
        if self.state != "dungeon" or not getattr(self, 'dungeon', None):
            return
        d = self.dungeon
        targets = [(m['enemy'], m['enemy'].x, m['enemy'].y) for m in d['mobs']]
        fired = self._cast_ultimate(
            targets,
            lambda e: self._dungeon_kill_reward(e),
            (d['px'], d['py']),
        )
        if fired and self.player.char_class == "Rogue":
            fx, fy = d['face']
            d['px'] = max(16, min(d['world_w'] - 16, d['px'] + fx * 110))
            d['py'] = max(16, min(d['world_h'] - 16, d['py'] + fy * 110))
            d['p_iframes'] = max(d['p_iframes'], 60)

    def start_dungeon(self, entry):
        """Spawn a 2-room themed dungeon (each room = full screen size, camera scrolls)."""
        tier = entry.get('tier', 0)
        dim = entry.get('dimension', self.current_dimension)
        dtype = entry.get('dtype', 'kingdom')
        theme = DUNGEON_TYPES.get(dtype, DUNGEON_TYPES['kingdom'])
        mob_pool = theme['mobs']

        # Internal wall + opening: each room is full-screen sized
        wall_thick = 24
        opening = 130
        layout = random.choice(['horizontal', 'vertical'])
        wall_mid = 0
        if layout == 'horizontal':
            # Top + bottom rooms, each SCREEN_HEIGHT tall
            world_w = SCREEN_WIDTH
            world_h = SCREEN_HEIGHT * 2 + wall_thick
        else:
            # Left + right rooms, each SCREEN_WIDTH wide
            world_w = SCREEN_WIDTH * 2 + wall_thick
            world_h = SCREEN_HEIGHT
        left, top, right, bottom = 0, 0, world_w, world_h
        bw, bh = world_w, world_h

        # Cover obstacles — placed in the bottom/right room only so the spawn room
        # is open; theme fractions remap into that room's local coordinates
        if layout == 'horizontal':
            # bottom room spans [wall_mid + wall_thick, bottom]
            mid_y = SCREEN_HEIGHT
            wall_mid = mid_y
            obstacles = [(int(fx * SCREEN_WIDTH),
                          mid_y + wall_thick + int(fy * SCREEN_HEIGHT), w, h)
                         for (fx, fy, w, h) in theme['obstacles']]
            op_x = random.randint(left + 160, right - 160 - opening)
            obstacles.append((left, mid_y, op_x - left, wall_thick))
            obstacles.append((op_x + opening, mid_y,
                              right - (op_x + opening), wall_thick))
            # Spawn in bottom room, exit at the top of the top room
            player_spawn = (SCREEN_WIDTH // 2, world_h - 60)
            exit_pos = (SCREEN_WIDTH // 2, top + 28)
        else:  # vertical
            mid_x = SCREEN_WIDTH
            wall_mid = mid_x
            obstacles = [(mid_x + wall_thick + int(fx * SCREEN_WIDTH),
                          int(fy * SCREEN_HEIGHT), w, h)
                         for (fx, fy, w, h) in theme['obstacles']]
            op_y = random.randint(top + 130, bottom - 130 - opening)
            obstacles.append((mid_x, top, wall_thick, op_y - top))
            obstacles.append((mid_x, op_y + opening, wall_thick,
                              bottom - (op_y + opening)))
            # Spawn in left room, exit at far right of right room
            player_spawn = (140, SCREEN_HEIGHT // 2)
            exit_pos = (world_w - 140, SCREEN_HEIGHT // 2)

        def overlaps_obstacle(x, y, pad=24):
            for ox, oy, ow, oh in obstacles:
                if ox - pad <= x <= ox + ow + pad and oy - pad <= y <= oy + oh + pad:
                    return True
            return False

        # Spawn mobs across the whole dungeon so they populate both rooms
        mobs = []
        n = random.randint(4, 6 + tier)
        for _ in range(n):
            mx = my = 0
            for _attempt in range(30):
                tx = random.randint(left + 80, right - 80)
                ty = random.randint(top + 40, bottom - 80)
                # Keep mobs out of the immediate spawn zone
                if (tx - player_spawn[0]) ** 2 + (ty - player_spawn[1]) ** 2 < 140 ** 2:
                    continue
                if not overlaps_obstacle(tx, ty):
                    mx, my = tx, ty
                    break
            else:
                mx = random.randint(left + 80, right - 80)
                my = random.randint(top + 40, top + 280)
            level = random.randint(2, 4 + tier * 2)
            mob_name = random.choice(mob_pool)
            e = create_mob(mob_name, level, mx, my)
            e.max_hp = max(1, int(e.max_hp * self.difficulty['enemy_hp']))
            e.hp = e.max_hp
            e.damage = max(1, int(e.damage * self.difficulty['enemy_dmg']))
            e.dimension = dim
            mobs.append({'enemy': e, 'atk_cd': random.randint(20, 60)})

        # Chests in corners; skip any spot that an obstacle covers
        candidate_spots = [
            (left + 80, top + 60), (right - 80, top + 60),
            (left + 80, bottom - 80), (right - 80, bottom - 80),
        ]
        chest_spots = [s for s in candidate_spots if not overlaps_obstacle(*s, pad=16)]
        random.shuffle(chest_spots)
        cmin, cmax = theme['chest_range']
        n_chests = min(len(chest_spots), random.randint(cmin, cmax))
        chests = [{'x': cx, 'y': cy, 'opened': False,
                   'rarity': random.choice(theme['rarity_bias']),
                   'width': 24, 'height': 16}
                  for cx, cy in chest_spots[:n_chests]]

        self.dungeon = {
            'theme': theme,
            'dtype': dtype,
            'layout': layout,
            'world_w': world_w,
            'world_h': world_h,
            'wall_mid': wall_mid,
            'wall_thick': wall_thick,
            'px': player_spawn[0],
            'py': player_spawn[1],
            'mobs': mobs,
            'chests': chests,
            'obstacles': obstacles,
            'projectiles': [],      # Mage fireballs
            'exit_x': exit_pos[0],
            'exit_y': exit_pos[1],
            'cleared': False,
            'face': (0.0, -1.0),
            'p_atk_cd': 0,
            'p_iframes': 90,        # brief grace period on entry
            'p_swing': 0,
            'cam_x': 0.0,
            'cam_y': 0.0,
            'entry_pos': (self.player.x, self.player.y),
            'entry_dim': self.current_dimension,
            'tier': tier,
        }
        self.state = "dungeon"
        self.add_combat_log(f"You enter the {theme['name']}! ({n} foes spread across 2 rooms)")

    def _dungeon_obstacle_hit(self, x, y, r):
        for ox, oy, ow, oh in self.dungeon['obstacles']:
            if ox - r <= x <= ox + ow + r and oy - r <= y <= oy + oh + r:
                return True
        return False

    def update_dungeon(self):
        if not self.player or not getattr(self, 'dungeon', None):
            self.state = "playing"
            return
        d = self.dungeon
        # World bounds: dungeon is bigger than the screen now
        left, top = 0, 0
        right = d['world_w']
        bottom = d['world_h']
        keys = pygame.key.get_pressed()
        p_radius, m_radius = 16, 16

        if d['p_atk_cd'] > 0: d['p_atk_cd'] -= 1
        if d['p_iframes'] > 0: d['p_iframes'] -= 1
        if d['p_swing'] > 0: d['p_swing'] -= 1
        for m in d['mobs']:
            if m['atk_cd'] > 0: m['atk_cd'] -= 1

        # --- Player movement ---
        dx = dy = 0.0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:  dx -= 1
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]: dx += 1
        if keys[pygame.K_UP] or keys[pygame.K_w]:    dy -= 1
        if keys[pygame.K_DOWN] or keys[pygame.K_s]:  dy += 1
        if dx or dy:
            mag = math.hypot(dx, dy)
            dx, dy = dx / mag, dy / mag
            d['face'] = (dx, dy)
            speed = 4.0
            nx = max(left + p_radius, min(right - p_radius, d['px'] + dx * speed))
            if not self._dungeon_obstacle_hit(nx, d['py'], p_radius):
                d['px'] = nx
            ny = max(top + p_radius, min(bottom - p_radius, d['py'] + dy * speed))
            if not self._dungeon_obstacle_hit(d['px'], ny, p_radius):
                d['py'] = ny

        # --- Camera follows the player, clamped to the dungeon bounds ---
        cam_x = max(0, min(right - SCREEN_WIDTH, d['px'] - SCREEN_WIDTH / 2))
        cam_y = max(0, min(bottom - SCREEN_HEIGHT, d['py'] - SCREEN_HEIGHT / 2))
        d['cam_x'], d['cam_y'] = cam_x, cam_y

        # --- Melee attack (SPACE) ---
        if keys[pygame.K_SPACE] and d['p_atk_cd'] == 0:
            d['p_atk_cd'] = 24
            d['p_swing'] = 10
            melee_range = m_radius + 36
            for m in d['mobs']:
                e = m['enemy']
                if not e.alive:
                    continue
                if math.hypot(e.x - d['px'], e.y - d['py']) <= melee_range:
                    self.player.attack(e)
                    self.add_battle_effect(e.x, e.y, "sword_slash")
                    self.add_battle_effect(e.x, e.y, "hit")
                    self.add_screen_shake(3, 5)
                    if not e.alive:
                        self._dungeon_kill_reward(e)
                    break

        # --- Mage ranged fireball (F) ---
        if (keys[pygame.K_f] and self.player.char_class == "Mage"
                and d['p_atk_cd'] == 0 and self.player.mp >= 15):
            self.player.mp -= 15
            d['p_atk_cd'] = 22
            vx, vy = d['face']
            if vx == 0 and vy == 0:
                vx, vy = 0, -1
            d['projectiles'].append({'x': d['px'], 'y': d['py'],
                                     'vx': vx * 8, 'vy': vy * 8, 'life': 100})

        # --- Update fireballs: move, hit a mob, expire on bounds/time ---
        for pr in d['projectiles'][:]:
            pr['x'] += pr['vx']; pr['y'] += pr['vy']; pr['life'] -= 1
            consumed = False
            for m in d['mobs']:
                e = m['enemy']
                if not e.alive:
                    continue
                if math.hypot(e.x - pr['x'], e.y - pr['y']) <= m_radius + 6:
                    fdmg = random.randint(10, 18) + self.player.intelligence // 2
                    e.take_damage(fdmg)
                    self.add_battle_effect(e.x, e.y, "hit")
                    self.add_screen_shake(2, 4)
                    if not e.alive:
                        self._dungeon_kill_reward(e)
                    d['projectiles'].remove(pr)
                    consumed = True
                    break
            if consumed:
                continue
            if (pr['life'] <= 0
                    or self._dungeon_obstacle_hit(pr['x'], pr['y'], 4)
                    or not (left <= pr['x'] <= right and top <= pr['y'] <= bottom)):
                d['projectiles'].remove(pr)

        # --- Mob AI: chase + contact damage (slide around obstacles) ---
        alive = 0
        for m in d['mobs']:
            e = m['enemy']
            if not e.alive:
                continue
            alive += 1
            mdx, mdy = d['px'] - e.x, d['py'] - e.y
            mmag = math.hypot(mdx, mdy) or 1
            if abs(mdx) > 1:
                e.facing = 1 if mdx > 0 else -1
            spd = max(0.8, e.speed)
            nx = max(left + m_radius, min(right - m_radius, e.x + (mdx / mmag) * spd))
            if not self._dungeon_obstacle_hit(nx, e.y, m_radius):
                e.x = nx
            ny = max(top + m_radius, min(bottom - m_radius, e.y + (mdy / mmag) * spd))
            if not self._dungeon_obstacle_hit(e.x, ny, m_radius):
                e.y = ny
            mmag = math.hypot(d['px'] - e.x, d['py'] - e.y) or 1
            if mmag <= p_radius + m_radius + 4 and m['atk_cd'] == 0 and d['p_iframes'] == 0:
                m['atk_cd'] = 50
                d['p_iframes'] = 40
                edmg = e.attack(self.player)
                self.add_battle_effect(d['px'], d['py'], "hit")
                self.add_screen_shake(6, 10)
                self.add_screen_flash(RED, 5)
                self.add_combat_log(f"{e.name} hits you for {edmg}!")
                if self.player.hp <= 0:
                    self.state = "game_over"
                    return

        if alive == 0 and not d['cleared']:
            d['cleared'] = True
            bonus = 1 + d['tier']
            self.player.shards += bonus
            self.add_combat_log(f"Dungeon cleared! Exit unlocked. +{bonus} Soul Shards")

    def _dungeon_kill_reward(self, e):
        self.player.gain_exp(e.exp_reward)
        self.player.gold += e.gold_reward
        self.add_combat_log(f"{e.name} defeated! +{e.exp_reward} EXP, +{e.gold_reward} gold")
        item, rarity = self.roll_item_drop(e.level)
        if item:
            self.add_item_to_inventory(item)
            self.add_combat_log(f"Dropped: {item.name}")
        # Vampire signature drop
        if e.name == 'Vampire' and random.random() < 0.40:
            fang = Weapon("Vampire Fang", 4, 220, lifesteal=0.20)
            if self.add_item_to_inventory(fang, silent=True):
                self.add_combat_log("*** Vampire Fang dropped! ***")
                self.add_screen_flash(YELLOW, 12)
        self._quest_progress_event('kill')

    def _dungeon_interact(self):
        d = self.dungeon
        # Try chest first
        for chest in d['chests']:
            if not chest['opened']:
                if math.hypot(chest['x'] - d['px'], chest['y'] - d['py']) <= 42:
                    self.open_chest(chest)
                    return
        # Exit if cleared
        if d['cleared'] and math.hypot(d['exit_x'] - d['px'], d['exit_y'] - d['py']) <= 48:
            self._leave_dungeon()
            return
        if not d['cleared']:
            self.add_combat_log("Defeat all foes to unlock the exit!")
        else:
            self.add_combat_log("Nothing to interact with here.")

    def _leave_dungeon(self):
        d = self.dungeon
        self.player.x, self.player.y = d['entry_pos']
        self.current_dimension = d.get('entry_dim', self.current_dimension)
        self.world_chunks = self.all_dimension_chunks[self.current_dimension]
        self.loaded_chunks = self.all_loaded_chunks[self.current_dimension]
        self.camera_x = self.player.x - SCREEN_WIDTH // 2
        self.camera_y = self.player.y - SCREEN_HEIGHT // 2
        self.player.spawn_shield = 90
        self.state = "playing"
        self.dungeon = None
        self.add_combat_log("You leave the dungeon.")

    def draw_dungeon_on_surface(self, surface):
        t = pygame.time.get_ticks()
        d = self.dungeon
        theme = d.get('theme', DUNGEON_TYPES['kingdom'])
        floor_col = theme['floor']
        wall_col = theme['wall']
        accent_col = theme['accent']
        left, top = 0, 0
        right = d['world_w']
        bottom = d['world_h']
        cam_x, cam_y = d.get('cam_x', 0.0), d.get('cam_y', 0.0)

        def sx(x): return int(x - cam_x)
        def sy(y): return int(y - cam_y)

        # Stone backdrop (whole screen)
        bg = (max(0, floor_col[0] // 3), max(0, floor_col[1] // 3), max(0, floor_col[2] // 3))
        surface.fill(bg)
        # Floor (world-space rect, camera-shifted)
        pygame.draw.rect(surface, floor_col, (sx(left), sy(top), right - left, bottom - top))
        # Grid lines (only the visible window)
        grid_col = (wall_col[0] // 3, wall_col[1] // 3, wall_col[2] // 3)
        first_gx = int(cam_x // 48) * 48
        for gx in range(first_gx, int(cam_x) + SCREEN_WIDTH + 48, 48):
            if left <= gx <= right:
                pygame.draw.line(surface, grid_col, (sx(gx), max(0, sy(top))),
                                 (sx(gx), min(SCREEN_HEIGHT, sy(bottom))), 1)
        first_gy = int(cam_y // 48) * 48
        for gy in range(first_gy, int(cam_y) + SCREEN_HEIGHT + 48, 48):
            if top <= gy <= bottom:
                pygame.draw.line(surface, grid_col, (max(0, sx(left)), sy(gy)),
                                 (min(SCREEN_WIDTH, sx(right)), sy(gy)), 1)
        # Wall border (perimeter of the dungeon)
        pygame.draw.rect(surface, wall_col, (sx(left), sy(top), right - left, bottom - top), 4)
        # Sconces in dungeon corners
        for sx_, sy_ in [(left, top), (right, top), (left, bottom), (right, bottom)]:
            flicker = int(8 + 4 * math.sin(t * 0.008 + sx_))
            pygame.draw.circle(surface, accent_col, (sx(sx_), sy(sy_)), flicker, 0)

        # Cover obstacles (themed pillars, shadowed with accent edge)
        for ox, oy, ow, oh in d['obstacles']:
            pygame.draw.rect(surface, (0, 0, 0), (sx(ox) + 4, sy(oy) + 6, ow, oh))
            ob_col = (min(255, wall_col[0] - 30), min(255, wall_col[1] - 30), min(255, wall_col[2] - 30))
            pygame.draw.rect(surface, ob_col, (sx(ox), sy(oy), ow, oh))
            pygame.draw.rect(surface, accent_col, (sx(ox), sy(oy), ow, oh), 2)

        # Chests
        for chest in d['chests']:
            col = (110, 75, 30) if not chest['opened'] else DARK_GRAY
            pygame.draw.rect(surface, col, (sx(chest['x']) - 12, sy(chest['y']) - 8, 24, 16))
            pygame.draw.rect(surface, YELLOW, (sx(chest['x']) - 12, sy(chest['y']) - 8, 24, 16), 2)
            if not chest['opened']:
                pygame.draw.circle(surface, YELLOW, (sx(chest['x']), sy(chest['y'])), 3)

        # Exit door
        door_col = (80, 200, 80) if d['cleared'] else (60, 60, 60)
        pygame.draw.rect(surface, door_col, (sx(d['exit_x']) - 16, sy(d['exit_y']) - 20, 32, 40))
        pygame.draw.rect(surface, WHITE, (sx(d['exit_x']) - 16, sy(d['exit_y']) - 20, 32, 40), 2)
        label = self.small_font.render("EXIT" if d['cleared'] else "LOCKED", True, WHITE)
        surface.blit(label, (sx(d['exit_x']) - label.get_width() // 2, sy(d['exit_y']) - 40))

        # Fireballs (Mage)
        for pr in d['projectiles']:
            glow = pygame.Surface((20, 20), pygame.SRCALPHA)
            pygame.draw.circle(glow, (255, 160, 40, 120), (10, 10), 9)
            surface.blit(glow, (sx(pr['x']) - 10, sy(pr['y']) - 10))
            pygame.draw.circle(surface, (255, 230, 120), (sx(pr['x']), sy(pr['y'])), 4)

        # Mobs (shadow, threat ring, HP bar)
        size = 18
        for m in d['mobs']:
            e = m['enemy']
            if not e.alive:
                continue
            ex, ey = sx(e.x), sy(e.y)
            pygame.draw.ellipse(surface, (16, 12, 18),
                                (ex - size - 4, ey + size - 4, size * 2 + 8, size + 6))
            ring_col = (255, 80, 80) if e.level >= 5 else (255, 200, 80) if e.level >= 3 else (235, 235, 235)
            pygame.draw.circle(surface, ring_col, (ex, ey), size + 4, 2)
            efacing = getattr(e, 'facing', 1)
            eax, eay = self.anim_offset(e, efacing, reach=size)
            self._draw_mob_shape(surface, e.name, ex + eax, ey + eay, size, efacing,
                                 tint=self.hurt_phase(e))
            self.draw_swing_arc(surface, e, ex + eax, ey + eay,
                                int(size * 1.7), efacing, color=(255, 190, 150))
            bw, bh = 40, 5
            ratio = max(0.0, e.hp / e.max_hp)
            bx = ex - bw // 2
            by = ey - size - 12
            pygame.draw.rect(surface, (0, 0, 0), (bx - 1, by - 1, bw + 2, bh + 2))
            pygame.draw.rect(surface, (50, 0, 0), (bx, by, bw, bh))
            hp_col = (60, 200, 60) if ratio > 0.5 else (220, 180, 40) if ratio > 0.25 else (220, 60, 40)
            pygame.draw.rect(surface, hp_col, (bx, by, int(bw * ratio), bh))
            self._draw_nametag(surface, f"{e.name} Lv{e.level}", ex, by - 15, ring_col)

        # Player (blink during i-frames)
        if not (d['p_iframes'] > 0 and (t // 80) % 2 == 0):
            pfacing = 1 if d['face'][0] >= 0 else -1
            self.draw_character_in_combat(surface, self.player,
                                          sx(d['px']), sy(d['py']), True, 16, pfacing)
            # Aiming arc along the exact facing vector, on top of the swing animation
            if d['p_swing'] > 0:
                fxd, fyd = d['face']
                ang = math.atan2(fyd, fxd)
                pygame.draw.arc(surface, (255, 255, 200),
                                (sx(d['px']) - 44, sy(d['py']) - 44, 88, 88), ang - 0.7, ang + 0.7, 2)

        # ---- Fog of war: hide whichever room you are NOT in (world rect → screen) ----
        layout = d.get('layout', 'horizontal')
        wall_mid = d.get('wall_mid', 0)
        wall_thick = d.get('wall_thick', 24)
        fog_col = (8, 6, 12)
        if layout == 'horizontal':
            if d['py'] < wall_mid:
                other = (left, wall_mid + wall_thick, right - left, bottom - (wall_mid + wall_thick))
            else:
                other = (left, top, right - left, wall_mid - top)
        else:
            if d['px'] < wall_mid:
                other = (wall_mid + wall_thick, top, right - (wall_mid + wall_thick), bottom - top)
            else:
                other = (left, top, wall_mid - left, bottom - top)
        pygame.draw.rect(surface, fog_col, (sx(other[0]), sy(other[1]), other[2], other[3]))

        # Title and remaining count
        title = self.font.render(theme['name'].upper(), True, accent_col)
        surface.blit(title, (SCREEN_WIDTH // 2 - title.get_width() // 2, 20))
        alive = sum(1 for m in d['mobs'] if m['enemy'].alive)
        info = self.small_font.render(
            f"Mobs remaining: {alive}/{len(d['mobs'])}    Tier {d['tier']}", True, WHITE)
        surface.blit(info, (SCREEN_WIDTH // 2 - info.get_width() // 2, 56))

        # HP / MP bars (bottom-left)
        php = max(0.0, self.player.hp / self.player.max_hp)
        pygame.draw.rect(surface, (50, 0, 0), (20, SCREEN_HEIGHT - 60, 240, 16))
        pygame.draw.rect(surface, (60, 200, 60), (20, SCREEN_HEIGHT - 60, int(240 * php), 16))
        pygame.draw.rect(surface, WHITE, (20, SCREEN_HEIGHT - 60, 240, 16), 1)
        hp_t = self.small_font.render(f"HP {self.player.hp}/{self.player.max_hp}", True, WHITE)
        surface.blit(hp_t, (24, SCREEN_HEIGHT - 58))
        if self.player.max_mp:
            pmp = max(0.0, self.player.mp / self.player.max_mp)
            pygame.draw.rect(surface, (0, 0, 50), (20, SCREEN_HEIGHT - 40, 240, 12))
            pygame.draw.rect(surface, (80, 140, 255), (20, SCREEN_HEIGHT - 40, int(240 * pmp), 12))
            pygame.draw.rect(surface, WHITE, (20, SCREEN_HEIGHT - 40, 240, 12), 1)

        # Controls hint
        hint = "WASD Move  SPACE Attack  H Heal  I Items  E Loot/Exit" + (
            "  F Fireball" if self.player.char_class == "Mage" else "")
        ht = self.small_font.render(hint, True, (255, 200, 120))
        surface.blit(ht, (SCREEN_WIDTH - ht.get_width() - 20, SCREEN_HEIGHT - 44))
        ult = self._ultimate_status()
        if ult:
            txt = self.small_font.render(ult[0], True, ult[1])
            surface.blit(txt, (SCREEN_WIDTH - txt.get_width() - 20, SCREEN_HEIGHT - 64))
        sk = self._skill_status()
        if sk:
            txt = self.small_font.render(sk[0], True, sk[1])
            surface.blit(txt, (SCREEN_WIDTH - txt.get_width() - 20, SCREEN_HEIGHT - 84))

    def start_boss_arena(self):
        """Initialise the real-time sealed battlefield against the final boss."""
        W, H = self.ARENA_SIZE
        # Harder difficulties make the boss faster, hit more often, and use ranged blasts
        diff_idx = getattr(self, 'selected_difficulty', 1)
        # Cover pillars appear on Hard/Nightmare so you can break line-of-sight on fireballs
        obstacles = []
        if diff_idx >= 2:
            layout = [
                (0.22, 0.30, 90, 240), (0.70, 0.30, 90, 240),
                (0.34, 0.55, 240, 90), (0.55, 0.55, 240, 90),
                (0.15, 0.68, 110, 110), (0.78, 0.68, 110, 110),
                (0.45, 0.40, 110, 110),
            ]
            obstacles = [(int(fxr * W), int(fyr * H), w, h) for (fxr, fyr, w, h) in layout]
        self.arena = {
            'px': W / 2,
            'py': H - 220,
            'bx': W / 2,
            'by': 220,
            'p_atk_cd': 0,
            'b_atk_cd': 40,
            'p_iframes': 90,        # brief grace period on arrival
            'face': (0.0, -1.0),
            'p_swing': 0,
            'projectiles': [],
            'boss_proj': [],
            'obstacles': obstacles,
            'cam_x': 0.0,
            'cam_y': 0.0,
            'b_speed': 2.4 + diff_idx * 0.5,                 # 2.4 / 2.9 / 3.4 / 3.9
            'b_atk_cd_max': max(20, 54 - diff_idx * 9),      # 54 / 45 / 36 / 27
            # Every special-move cooldown is floored at 5 seconds (300 frames).
            'b_shoot_interval': max(300, {0: 360, 1: 330, 2: 310, 3: 300}.get(diff_idx, 330)),
            'b_shoot_spread': 5 if diff_idx >= 3 else (3 if diff_idx == 2 else 1),
            'b_shoot_cd': 300,
            'half_hp_saved': False,   # one-time auto-save when boss drops below half HP
            # Extra move-set timers (all >= 5s)
            'nova_cd':    max(300, 360 - diff_idx * 15),    # 8-way burst nova
            'tp_cd':      max(300, 420 - diff_idx * 30),    # teleport-blink toward player
            'charge_cd':  max(300, 360),                    # wind-up + charge dash
            'charge_phase': 0,    # 0=idle, 1=telegraph, 2=dashing
            'charge_timer': 0,
            'charge_dir': (0.0, 0.0),
        }
        self.state = "boss_arena"

    def _arena_obstacle_hit(self, x, y, r):
        """True if a circle (x, y, r) overlaps any arena cover pillar."""
        for ox, oy, ow, oh in self.arena['obstacles']:
            if ox - r <= x <= ox + ow + r and oy - r <= y <= oy + oh + r:
                return True
        return False

    def update_boss_arena(self):
        if not self.player or not self.current_enemy:
            self.state = "playing"
            return

        a = self.arena
        boss = self.current_enemy
        W, H = self.ARENA_SIZE
        p_radius, b_radius = 16, 34
        keys = pygame.key.get_pressed()

        # --- Cooldowns / timers ---
        if a['p_atk_cd'] > 0: a['p_atk_cd'] -= 1
        if a['b_atk_cd'] > 0: a['b_atk_cd'] -= 1
        if a['p_iframes'] > 0: a['p_iframes'] -= 1
        if a['p_swing'] > 0: a['p_swing'] -= 1

        # --- Player movement ---
        dx = dy = 0.0
        if keys[pygame.K_LEFT] or keys[pygame.K_a]:  dx -= 1
        if keys[pygame.K_RIGHT] or keys[pygame.K_d]: dx += 1
        if keys[pygame.K_UP] or keys[pygame.K_w]:    dy -= 1
        if keys[pygame.K_DOWN] or keys[pygame.K_s]:  dy += 1
        if dx or dy:
            mag = math.hypot(dx, dy)
            dx, dy = dx / mag, dy / mag
            a['face'] = (dx, dy)
            speed = 4.6
            nx = max(p_radius, min(W - p_radius, a['px'] + dx * speed))
            if not self._arena_obstacle_hit(nx, a['py'], p_radius):
                a['px'] = nx
            ny = max(p_radius, min(H - p_radius, a['py'] + dy * speed))
            if not self._arena_obstacle_hit(a['px'], ny, p_radius):
                a['py'] = ny

        # --- Camera follows the player, clamped to the arena ---
        cam_x = max(0, min(W - SCREEN_WIDTH, a['px'] - SCREEN_WIDTH / 2))
        cam_y = max(0, min(H - SCREEN_HEIGHT, a['py'] - SCREEN_HEIGHT / 2))
        a['cam_x'], a['cam_y'] = cam_x, cam_y

        def fx(x, y):  # arena -> screen, for battle effects
            return (x - cam_x, y - cam_y)

        # --- Player melee attack (SPACE) ---
        melee_range = b_radius + 34
        dist = math.hypot(a['bx'] - a['px'], a['by'] - a['py'])
        if keys[pygame.K_SPACE] and a['p_atk_cd'] == 0:
            a['p_atk_cd'] = 28
            a['p_swing'] = 10
            if dist <= melee_range:
                self.player.attack(boss)
                ex, ey = fx(a['bx'], a['by'])
                self.add_battle_effect(ex, ey, "sword_slash")
                self.add_battle_effect(ex, ey, "hit")
                self.add_screen_shake(4, 6)

        # --- Mage ranged fireball (F) ---
        if keys[pygame.K_f] and self.player.char_class == "Mage" and a['p_atk_cd'] == 0:
            if self.player.mp >= 15:
                self.player.mp -= 15
                a['p_atk_cd'] = 22
                vx, vy = a['face']
                if vx == 0 and vy == 0:
                    vx, vy = 0, -1
                a['projectiles'].append({'x': a['px'], 'y': a['py'], 'vx': vx * 8, 'vy': vy * 8, 'life': 130})

        # --- Update projectiles ---
        for pr in a['projectiles'][:]:
            pr['x'] += pr['vx']; pr['y'] += pr['vy']; pr['life'] -= 1
            if math.hypot(a['bx'] - pr['x'], a['by'] - pr['y']) <= b_radius + 6:
                fdmg = random.randint(10, 18) + self.player.intelligence // 2
                boss.take_damage(fdmg)
                ex, ey = fx(a['bx'], a['by'])
                self.add_battle_effect(ex, ey, "hit")
                self.add_screen_shake(3, 5)
                a['projectiles'].remove(pr)
                continue
            if (pr['life'] <= 0 or self._arena_obstacle_hit(pr['x'], pr['y'], 4)
                    or not (-20 <= pr['x'] <= W + 20 and -20 <= pr['y'] <= H + 20)):
                a['projectiles'].remove(pr)

        # --- Update boss projectiles (ranged blasts on harder difficulties) ---
        for pr in a['boss_proj'][:]:
            pr['x'] += pr['vx']; pr['y'] += pr['vy']; pr['life'] -= 1
            if a['p_iframes'] == 0 and math.hypot(a['px'] - pr['x'], a['py'] - pr['y']) <= p_radius + 7:
                edmg = max(1, int(boss.damage * 0.7))
                self.player.take_damage(edmg)
                a['p_iframes'] = 40
                self.add_battle_effect(*fx(a['px'], a['py']), "hit")
                self.add_screen_shake(6, 10)
                self.add_screen_flash(RED, 5)
                self.add_combat_log(f"{boss.name}'s blast hits you for {edmg}!")
                a['boss_proj'].remove(pr)
                if self.player.hp <= 0:
                    self.in_boss_fight = False
                    self.state = "game_over"
                    return
                continue
            if (pr['life'] <= 0 or self._arena_obstacle_hit(pr['x'], pr['y'], 4)
                    or not (-20 <= pr['x'] <= W + 20 and -20 <= pr['y'] <= H + 20)):
                a['boss_proj'].remove(pr)

        # --- Auto-save once when the boss first drops below half health ---
        if not a['half_hp_saved'] and boss.alive and boss.hp < boss.max_hp * 0.5:
            a['half_hp_saved'] = True
            if self.save_game(auto=True, force=True):
                self.add_combat_log(f"{boss.name} is wounded — battle auto-saved!")

        # --- Boss death check ---
        if not boss.alive:
            a['projectiles'].clear()
            a['boss_proj'].clear()
            self.handle_enemy_defeat(*fx(a['bx'], a['by']))
            return

        # --- Boss charge dash: telegraph then high-speed dash; suspends chase ---
        if a['charge_phase'] == 0:
            a['charge_cd'] -= 1
            if a['charge_cd'] <= 0:
                a['charge_phase'] = 1
                a['charge_timer'] = 28   # telegraph frames
                dirx = a['px'] - a['bx']
                diry = a['py'] - a['by']
                mag = math.hypot(dirx, diry) or 1
                a['charge_dir'] = (dirx / mag, diry / mag)
                self.add_combat_log(f"{boss.name} winds up a charge!")
        elif a['charge_phase'] == 1:
            a['charge_timer'] -= 1
            if a['charge_timer'] <= 0:
                a['charge_phase'] = 2
                a['charge_timer'] = 28   # dash frames
        elif a['charge_phase'] == 2:
            a['charge_timer'] -= 1
            fxd, fyd = a['charge_dir']
            a['bx'] = max(b_radius, min(W - b_radius, a['bx'] + fxd * 11))
            a['by'] = max(b_radius, min(H - b_radius, a['by'] + fyd * 11))
            if a['charge_timer'] <= 0:
                a['charge_phase'] = 0
                a['charge_cd'] = max(300, 360)

        # --- Boss AI: chase the player (sliding around cover) — paused mid-charge ---
        if a['charge_phase'] == 0:
            bdx, bdy = a['px'] - a['bx'], a['py'] - a['by']
            bmag = math.hypot(bdx, bdy)
            if bmag > 1:
                nbx = max(b_radius, min(W - b_radius, a['bx'] + (bdx / bmag) * a['b_speed']))
                if not self._arena_obstacle_hit(nbx, a['by'], b_radius):
                    a['bx'] = nbx
                nby = max(b_radius, min(H - b_radius, a['by'] + (bdy / bmag) * a['b_speed']))
                if not self._arena_obstacle_hit(a['bx'], nby, b_radius):
                    a['by'] = nby

        # --- Nova: 8-direction projectile burst ---
        a['nova_cd'] -= 1
        if a['nova_cd'] <= 0:
            a['nova_cd'] = max(300, 360 - 20 * (1 if boss.hp < boss.max_hp * 0.5 else 0))
            for k in range(8):
                ang = k * (math.pi / 4)
                a['boss_proj'].append({'x': a['bx'], 'y': a['by'],
                                       'vx': math.cos(ang) * 4.5, 'vy': math.sin(ang) * 4.5,
                                       'life': 180})
            self.add_screen_flash((220, 80, 220), 8)
            self.add_combat_log(f"{boss.name} unleashes a nova burst!")

        # --- Teleport: blink to within ~190px of the player to break kiting ---
        a['tp_cd'] -= 1
        if a['tp_cd'] <= 0:
            a['tp_cd'] = max(300, 380)
            for _ in range(8):
                ang = random.uniform(0, 2 * math.pi)
                tx = a['px'] + math.cos(ang) * 190
                ty = a['py'] + math.sin(ang) * 190
                tx = max(b_radius, min(W - b_radius, tx))
                ty = max(b_radius, min(H - b_radius, ty))
                if not self._arena_obstacle_hit(tx, ty, b_radius):
                    a['bx'], a['by'] = tx, ty
                    break
            self.add_screen_flash((180, 60, 220), 6)
            self.add_combat_log(f"{boss.name} blinks behind you!")

        # --- Boss ranged blasts (Hard / Nightmare) ---
        if a['b_shoot_interval'] > 0:
            if a['b_shoot_cd'] > 0:
                a['b_shoot_cd'] -= 1
            else:
                a['b_shoot_cd'] = a['b_shoot_interval']
                base_ang = math.atan2(a['py'] - a['by'], a['px'] - a['bx'])
                n = a['b_shoot_spread']
                offsets = [0.0] if n <= 1 else [(-0.4 + 0.8 * i / (n - 1)) for i in range(n)]
                for off in offsets:
                    ang = base_ang + off
                    a['boss_proj'].append({'x': a['bx'], 'y': a['by'],
                                           'vx': math.cos(ang) * 5.5, 'vy': math.sin(ang) * 5.5, 'life': 170})

        # --- Boss contact attack ---
        if dist <= b_radius + p_radius + 6 and a['b_atk_cd'] == 0 and a['p_iframes'] == 0:
            a['b_atk_cd'] = a['b_atk_cd_max']
            a['p_iframes'] = 45
            edmg = boss.attack(self.player)
            self.add_battle_effect(*fx(a['px'], a['py']), "hit")
            self.add_screen_shake(8, 12)
            self.add_screen_flash(RED, 6)
            self.add_combat_log(f"{boss.name} hits you for {edmg}!")
            if self.player.hp <= 0:
                self.in_boss_fight = False
                self.state = "game_over"

    def combat_attack(self):
        if not self.current_enemy or not self.current_enemy.alive or not self.player:
            return
            
        try:
            # Store enemy position before potential death
            enemy_x = self.current_enemy.x + 12
            enemy_y = self.current_enemy.y + 12
            
            damage = self.player.attack(self.current_enemy)
            
            # Add attack effects
            self.add_battle_effect(enemy_x, enemy_y, "sword_slash")
            self.add_battle_effect(enemy_x, enemy_y, "hit")
            self.add_screen_shake(5, 8)
            self.add_screen_flash(RED, 3)
            self.add_combat_log(f"You deal {damage} damage!")
            
            if not self.current_enemy.alive:
                # Enemy defeated - handle drops before clearing enemy reference
                self.handle_enemy_defeat(enemy_x, enemy_y)
            else:
                # Enemy attacks back
                enemy_damage = self.current_enemy.attack(self.player)
                self.add_battle_effect(400, 300, "hit")  # Center screen for player hit
                self.add_screen_shake(8, 12)
                self.add_screen_flash(RED, 5)
                self.add_combat_log(f"{self.current_enemy.name} deals {enemy_damage} damage!")
                
                if self.player.hp <= 0:
                    self.state = "game_over"
        except Exception as e:
            print(f"Combat attack error: {e}")
            # Failsafe - exit combat if something goes wrong
            self.state = "playing"
            self.current_enemy = None
    
    def combat_spell(self):
        if not self.current_enemy or not self.current_enemy.alive or not self.player:
            return
            
        if self.player.char_class != "Mage":
            self.add_combat_log("You cannot cast spells!")
            return
            
        if self.player.mp < 15:
            self.add_combat_log("Not enough MP to cast Fireball!")
            return
            
        try:
            # Store enemy position before potential death
            enemy_x = self.current_enemy.x + 12
            enemy_y = self.current_enemy.y + 12
            
            result = self.player.cast_spell("Fireball", self.current_enemy)
            
            # Add spell effects
            self.add_battle_effect(enemy_x, enemy_y, "fireball")
            self.add_screen_shake(10, 15)
            self.add_screen_flash(YELLOW, 8)
            self.add_combat_log("You cast Fireball!")
            
            if not self.current_enemy.alive:
                # Enemy defeated - handle drops before clearing enemy reference
                self.handle_enemy_defeat(enemy_x, enemy_y)
            else:
                # Enemy attacks back
                enemy_damage = self.current_enemy.attack(self.player)
                self.add_battle_effect(400, 300, "hit")  # Center screen for player hit
                self.add_screen_shake(8, 12)
                self.add_screen_flash(RED, 5)
                self.add_combat_log(f"{self.current_enemy.name} deals {enemy_damage} damage!")
                
                if self.player.hp <= 0:
                    self.state = "game_over"
        except Exception as e:
            print(f"Combat spell error: {e}")
            # Failsafe - exit combat if something goes wrong
            self.state = "playing"
            self.current_enemy = None
    
    def combat_heal(self):
        """Cast Heal in menu combat; costs a turn (the enemy strikes back)."""
        if not self.current_enemy or not self.current_enemy.alive or not self.player:
            return
        if self.player.mp < 10:
            self.add_combat_log("Not enough MP to Heal! (need 10)")
            return
        before = self.player.hp
        self.player.cast_spell("Heal")
        healed = self.player.hp - before
        self.add_battle_effect(400, 300, "heal")
        self.add_screen_flash(GREEN, 5)
        self.add_combat_log(f"You cast Heal and recover {healed} HP!")
        # Enemy gets its turn
        enemy_damage = self.current_enemy.attack(self.player)
        self.add_battle_effect(400, 300, "hit")
        self.add_screen_shake(6, 10)
        self.add_combat_log(f"{self.current_enemy.name} deals {enemy_damage} damage!")
        if self.player.hp <= 0:
            self.state = "game_over"

    def combat_run(self):
        # Calculate run success based on player's dexterity vs enemy level
        run_chance = 50 + (self.player.dexterity - self.current_enemy.level * 5)
        run_chance = max(10, min(90, run_chance))  # Clamp between 10% and 90%
        
        if random.randint(1, 100) <= run_chance:
            # Successful escape
            self.add_combat_log("You successfully escaped!")
            self.add_screen_flash(GREEN, 5)
            
            # Move player away from enemy to prevent immediate re-engagement
            dx = self.player.x - self.current_enemy.x
            dy = self.player.y - self.current_enemy.y
            distance = math.sqrt(dx**2 + dy**2)
            
            if distance > 0:
                # Normalize and multiply by escape distance
                escape_distance = 80
                dx = (dx / distance) * escape_distance
                dy = (dy / distance) * escape_distance
                
                # Move player away in world coordinates
                self.player.x += dx
                self.player.y += dy
            
            # Exit combat
            self.state = "playing"
            self.current_enemy = None
            
        else:
            # Failed to escape - enemy gets a free attack
            self.add_combat_log("Failed to escape!")
            self.add_screen_flash(RED, 5)
            
            # Enemy attacks
            enemy_damage = self.current_enemy.attack(self.player)
            self.add_battle_effect(400, 300, "hit")  # Center screen for player hit
            self.add_screen_shake(8, 12)
            self.add_screen_flash(RED, 5)
            self.add_combat_log(f"{self.current_enemy.name} attacks as you flee for {enemy_damage} damage!")
            
            if self.player.hp <= 0:
                self.state = "game_over"
    
    def draw(self):
        # Calculate screen shake offset
        shake_x = shake_y = 0
        if self.screen_shake > 0:
            shake_x = random.randint(-self.screen_shake_intensity, self.screen_shake_intensity)
            shake_y = random.randint(-self.screen_shake_intensity, self.screen_shake_intensity)
        
        # Fill background
        self.screen.fill(DARK_GREEN)
        
        # Create a surface for the main game content
        game_surface = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
        game_surface.fill(DARK_GREEN)
        
        if self.state == "menu":
            self.draw_menu_on_surface(game_surface)
        elif self.state == "save_select":
            self.draw_save_select_on_surface(game_surface)
        elif self.state == "char_creation":
            self.draw_char_creation_on_surface(game_surface)
        elif self.state == "difficulty":
            self.draw_difficulty_on_surface(game_surface)
        elif self.state == "playing":
            self.draw_game_on_surface(game_surface)
        elif self.state == "inside_building":
            self.draw_building_interior(game_surface)
        elif self.state == "combat":
            self.draw_combat_on_surface(game_surface)
        elif self.state == "boss_arena":
            self.draw_boss_arena_on_surface(game_surface)
        elif self.state == "dungeon":
            self.draw_dungeon_on_surface(game_surface)
        elif self.state == "inventory":
            self.draw_inventory_on_surface(game_surface)
        elif self.state == "quest_log":
            self.draw_quest_log_on_surface(game_surface)
        elif self.state == "game_over":
            self.draw_game_over_on_surface(game_surface)
        elif self.state == "victory":
            self.draw_victory_on_surface(game_surface)

        # Draw battle effects
        for effect in self.battle_effects:
            effect.draw(game_surface)
        
        # Apply screen shake
        self.screen.blit(game_surface, (shake_x, shake_y))
        
        # Apply screen flash
        if self.flash_screen > 0:
            flash_surface = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT))
            flash_surface.fill(self.flash_color)
            flash_surface.set_alpha(self.flash_screen * 20)
            self.screen.blit(flash_surface, (0, 0))
        
        # Draw combat log
        if self.log_timer > 0 and self.combat_log:
            log_y = 50
            for message in self.combat_log[-3:]:  # Show last 3 messages
                log_surface = self.small_font.render(message, True, WHITE)
                log_bg = pygame.Surface((log_surface.get_width() + 20, log_surface.get_height() + 10))
                log_bg.fill(BLACK)
                log_bg.set_alpha(150)
                self.screen.blit(log_bg, (SCREEN_WIDTH//2 - log_surface.get_width()//2 - 10, log_y - 5))
                self.screen.blit(log_surface, (SCREEN_WIDTH//2 - log_surface.get_width()//2, log_y))
                log_y += 25
        
        pygame.display.flip()
    
    def draw_save_select_on_surface(self, surface):
        cx = SCREEN_WIDTH // 2
        title = self.font.render("Select a Save Slot", True, (255, 230, 150))
        surface.blit(title, (cx - title.get_width() // 2, 90))

        card_w, card_h = 700, 130
        card_x = cx - card_w // 2
        for i in range(SAVE_SLOTS):
            slot = i + 1
            y = 180 + i * (card_h + 12)
            summary = self.read_slot_summary(slot)
            selected = (i == self.save_select_index)

            # Card background
            if summary:
                bg = (30, 30, 45) if not selected else (60, 60, 90)
            else:
                bg = (24, 24, 30) if not selected else (44, 44, 54)
            pygame.draw.rect(surface, bg, (card_x, y, card_w, card_h))
            border = (255, 220, 120) if selected else (140, 130, 90)
            pygame.draw.rect(surface, border, (card_x, y, card_w, card_h), 3)

            # Slot number
            num = self.font.render(f"[{slot}]", True, border)
            surface.blit(num, (card_x + 18, y + 18))

            if summary:
                name_line = self.font.render(
                    f"{summary['name']} the {summary['class']}", True, WHITE)
                surface.blit(name_line, (card_x + 90, y + 18))
                meta = self.small_font.render(
                    f"Level {summary['level']}   Dimension: {summary['dimension'].title()}   "
                    f"Difficulty: {summary['difficulty']}",
                    True, (200, 220, 240))
                surface.blit(meta, (card_x + 90, y + 62))
                hint = self.small_font.render(
                    "Press number to load, ENTER on highlight, D to delete", True, (150, 200, 150))
                surface.blit(hint, (card_x + 90, y + 92))
            else:
                empty = self.font.render("Empty Slot", True, (140, 140, 140))
                surface.blit(empty, (card_x + 90, y + 44))

        footer = self.small_font.render(
            "1/2/3 or ENTER: Load slot   -   Up/Down: Highlight   -   D: Delete   -   ESC: Back",
            True, (200, 200, 220))
        surface.blit(footer, (cx - footer.get_width() // 2, SCREEN_HEIGHT - 34))

    def draw_menu_on_surface(self, surface):
        title = self.font.render("D&D RPG Adventure", True, WHITE)
        surface.blit(title, (SCREEN_WIDTH//2 - title.get_width()//2, 200))

        y = 290
        if self.has_save():
            # Top option: pick a save slot
            cont = self.font.render("[ C ] Load Game", True, (180, 240, 200))
            surface.blit(cont, (SCREEN_WIDTH//2 - cont.get_width()//2, y))
            sub = self.small_font.render("Choose a save slot to continue an adventure", True, (150, 200, 170))
            surface.blit(sub, (SCREEN_WIDTH//2 - sub.get_width()//2, y + 38))
            y += 80

        start_text = self.small_font.render("Press ENTER for New Game", True, WHITE)
        surface.blit(start_text, (SCREEN_WIDTH//2 - start_text.get_width()//2, y))

        if not IS_WEB:
            quit_text = self.small_font.render("Press Q to quit", True, WHITE)
            surface.blit(quit_text, (SCREEN_WIDTH//2 - quit_text.get_width()//2, y + 28))
    
    def draw_char_creation_on_surface(self, surface):
        title = self.font.render("Create Character", True, WHITE)
        surface.blit(title, (SCREEN_WIDTH//2 - title.get_width()//2, 100))
        
        name_text = self.small_font.render(f"Name: {self.player_name}", True, WHITE)
        surface.blit(name_text, (SCREEN_WIDTH//2 - name_text.get_width()//2, 200))
        
        class_text = self.small_font.render("Choose Class (Up/Down arrows):", True, WHITE)
        surface.blit(class_text, (SCREEN_WIDTH//2 - class_text.get_width()//2, 250))
        
        for i, char_class in enumerate(self.classes):
            color = YELLOW if i == self.selected_class else WHITE
            class_option = self.small_font.render(char_class, True, color)
            surface.blit(class_option, (SCREEN_WIDTH//2 - class_option.get_width()//2, 280 + i * 30))
        
        if self.player_name:
            continue_text = self.small_font.render("Press ENTER to continue", True, GREEN)
            surface.blit(continue_text, (SCREEN_WIDTH//2 - continue_text.get_width()//2, 400))

        back_text = self.small_font.render("ESC - Back to title", True, (180, 180, 180))
        surface.blit(back_text, (SCREEN_WIDTH//2 - back_text.get_width()//2, 440))

    def draw_difficulty_on_surface(self, surface):
        title = self.font.render("Select Difficulty", True, WHITE)
        surface.blit(title, (SCREEN_WIDTH//2 - title.get_width()//2, 80))

        for i, diff in enumerate(DIFFICULTIES):
            selected = (i == self.selected_difficulty)
            col = diff['color']
            y = 200 + i * 110

            # Panel
            panel = pygame.Surface((500, 90), pygame.SRCALPHA)
            panel.fill((0, 0, 0, 160) if not selected else (40, 40, 40, 200))
            surface.blit(panel, (SCREEN_WIDTH//2 - 250, y - 10))
            if selected:
                pygame.draw.rect(surface, col, (SCREEN_WIDTH//2 - 250, y - 10, 500, 90), 2)

            name_surf = self.font.render(diff['name'], True, col if selected else (150, 150, 150))
            surface.blit(name_surf, (SCREEN_WIDTH//2 - 230, y + 5))

            desc_surf = self.small_font.render(diff['desc'], True, WHITE if selected else GRAY)
            surface.blit(desc_surf, (SCREEN_WIDTH//2 - 230, y + 45))

        hint = self.small_font.render("Up/Down to select   ENTER to start   ESC to go back", True, LIGHT_GRAY)
        surface.blit(hint, (SCREEN_WIDTH//2 - hint.get_width()//2, SCREEN_HEIGHT - 40))

    def draw_game_on_surface(self, surface):
        # Dimension sky colour
        surface.fill(DIMENSION_SKY.get(self.current_dimension, (30, 30, 30)))
        # Draw terrain first (background layer) - only visible tiles
        visible_terrain = self.get_visible_objects('terrain')
        for tile in visible_terrain:
            # Calculate screen position
            screen_x = tile['x'] - self.camera_x
            screen_y = tile['y'] - self.camera_y
            
            # Only draw if on screen
            if -32 <= screen_x <= SCREEN_WIDTH and -32 <= screen_y <= SCREEN_HEIGHT:
                self.draw_terrain_tile(surface, tile, screen_x, screen_y)
        
        # Draw buildings (middle layer)
        visible_buildings = self.get_visible_objects('buildings')
        for building in visible_buildings:
            screen_x = building['x'] - self.camera_x
            screen_y = building['y'] - self.camera_y
            
            if (-building['width'] <= screen_x <= SCREEN_WIDTH and 
                -building['height'] <= screen_y <= SCREEN_HEIGHT):
                self.draw_building(surface, building, screen_x, screen_y)
        
        # Draw vegetation (middle layer)
        visible_vegetation = self.get_visible_objects('vegetation')
        for veg in visible_vegetation:
            screen_x = veg['x'] - self.camera_x
            screen_y = veg['y'] - self.camera_y
            
            if (-veg['size'] <= screen_x <= SCREEN_WIDTH and 
                -veg['size'] <= screen_y <= SCREEN_HEIGHT):
                self.draw_vegetation_item(surface, veg, screen_x, screen_y)
        
        # Draw player at center of screen (relative to camera)
        player_screen_x = self.player.x - self.camera_x
        player_screen_y = self.player.y - self.camera_y
        self.draw_player(surface, player_screen_x, player_screen_y)
        
        # Draw enemies (only visible ones)
        visible_enemies = [enemy for enemy in self.enemies if self.is_enemy_visible(enemy)]
        for enemy in visible_enemies:
            if enemy.alive:
                enemy_screen_x = enemy.x - self.camera_x
                enemy_screen_y = enemy.y - self.camera_y
                self.draw_enemy(surface, enemy, enemy_screen_x, enemy_screen_y)
        
        # Draw interaction prompts and interactable objects
        self.draw_interactables_infinite(surface)

        # Dimension-themed ambient particles drifting across the screen
        self._draw_overworld_particles(surface)

        # Draw UI on top so it stays crisp
        self.draw_ui_on_surface(surface)

    def _draw_overworld_particles(self, surface):
        """Drift atmospheric particles with parallax — they scroll slower than the world."""
        t = pygame.time.get_ticks()
        dim = self.current_dimension
        # Parallax factor: 0 = locked to camera, 1 = locked to world.
        # Between them gives the illusion of distance / depth.
        # We render two layers: a slow far layer (small dim) and a faster near layer.
        cam_x = getattr(self, 'camera_x', 0) or 0
        cam_y = getattr(self, 'camera_y', 0) or 0

        def px_at(seed, base_x, base_y, factor):
            # Offset by parallax-scaled camera so particles drift as the player walks
            wx = base_x - int(cam_x * factor)
            wy = base_y - int(cam_y * factor)
            return wx % SCREEN_WIDTH, wy % SCREEN_HEIGHT

        # Far layer — dimmer, smaller, moves very slowly with the camera
        for i in range(12):
            seed = i * 131 + 5
            base_x = (seed * 53) % SCREEN_WIDTH
            base_y = (seed * 29) % SCREEN_HEIGHT
            if dim == 'nether':
                base = (200, 90, 20)
            elif dim == 'void':
                base = (140, 120, 200)
            elif dim == 'celestial':
                base = (240, 220, 170)
            else:
                base = (220, 220, 180)
            fx, fy = px_at(seed, base_x, base_y, 0.15)
            pygame.draw.circle(surface, base, (fx, fy), 1)

        # Near layer — brighter, larger, animates + moves faster
        for i in range(18):
            seed = i * 71 + 13
            if dim == 'nether':
                base = (255, 110 + (i * 9) % 110, 30)
                base_x = (seed * 53 + int(math.sin(t * 0.001 + i) * 18)) % SCREEN_WIDTH
                base_y = SCREEN_HEIGHT - ((int(t * 0.06) + seed * 17) % (SCREEN_HEIGHT + 60))
                r = 1 + (i % 2)
            elif dim == 'void':
                base = (200, 180, 255)
                base_x = (seed * 53 + int(t * 0.01) + i * 3) % SCREEN_WIDTH
                base_y = (seed * 29) % SCREEN_HEIGHT
                r = 1 + (i % 2)
            elif dim == 'celestial':
                base = (255, 240, 200)
                base_x = (seed * 53) % SCREEN_WIDTH
                base_y = (int(t * 0.04) + seed * 11) % SCREEN_HEIGHT
                r = 1 + (i % 2)
            else:
                base = (255, 255, 200)
                base_x = (seed * 53 + int(t * 0.008)) % SCREEN_WIDTH
                base_y = int((seed * 23) % SCREEN_HEIGHT + 16 * math.sin(t * 0.002 + i))
                r = 1
            px, py = px_at(seed, base_x, base_y, 0.45)
            b = 0.4 + 0.3 + 0.3 * math.sin(t * 0.004 + i)
            col = (max(0, min(255, int(base[0] * b))),
                   max(0, min(255, int(base[1] * b))),
                   max(0, min(255, int(base[2] * b))))
            pygame.draw.circle(surface, col, (px, py), r)

    def _get_vignette(self):
        """Build (once) a soft black edge vignette overlay."""
        v = getattr(self, '_vignette', None)
        if v is not None:
            return v
        v = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
        depth = 140
        max_a = 70
        for i in range(depth):
            a = int(max_a * (1 - i / depth) ** 1.6)
            pygame.draw.line(v, (0, 0, 0, a), (0, i), (SCREEN_WIDTH, i))
            pygame.draw.line(v, (0, 0, 0, a), (0, SCREEN_HEIGHT - i - 1), (SCREEN_WIDTH, SCREEN_HEIGHT - i - 1))
            pygame.draw.line(v, (0, 0, 0, a), (i, 0), (i, SCREEN_HEIGHT))
            pygame.draw.line(v, (0, 0, 0, a), (SCREEN_WIDTH - i - 1, 0), (SCREEN_WIDTH - i - 1, SCREEN_HEIGHT))
        self._vignette = v
        return v

    def draw_interactables(self, surface):
        """Draw interactable objects like chests and interaction prompts"""
        for obj in self.interactable_objects:
            if obj['type'] == 'chest':
                # Draw treasure chest
                chest_color = BROWN if not obj['opened'] else DARK_GRAY
                pygame.draw.rect(surface, chest_color, (obj['x'], obj['y'], obj['width'], obj['height']))
                pygame.draw.rect(surface, YELLOW, (obj['x'], obj['y'], obj['width'], obj['height']), 2)
                
                if not obj['opened']:
                    # Draw lock
                    pygame.draw.circle(surface, YELLOW, (obj['x'] + 8, obj['y'] + 6), 3)
                else:
                    # Draw open chest
                    pygame.draw.line(surface, DARK_GRAY, (obj['x'], obj['y']), (obj['x'] + obj['width'], obj['y']), 2)
            
            elif obj['type'] == 'door':
                # Just draw a simple door outline (optional - can be removed entirely)
                pygame.draw.rect(surface, GREEN, (obj['x'], obj['y'], obj['width'], obj['height']), 1)
                
                # Draw building type label centered above door
                door_label = self.small_font.render(f"{obj['building_type'].title()}", True, WHITE)
                label_x = obj['x'] + obj['width']//2 - door_label.get_width()//2  # Center horizontally
                label_y = obj['y'] - 30  # Position above door with more spacing
                
                # Draw text background for better readability
                label_bg = pygame.Surface((door_label.get_width() + 6, door_label.get_height() + 4))
                label_bg.fill(BLACK)
                label_bg.set_alpha(180)  # Semi-transparent background
                surface.blit(label_bg, (label_x - 3, label_y - 2))
                
                # Draw the text
                surface.blit(door_label, (label_x, label_y))
    
    def draw_building_interior(self, surface):
        """Draw the interior of a building"""
        # Fill with indoor color
        surface.fill((101, 67, 33))  # Brown wooden floor
        
        # Draw room based on building type
        if self.current_building['type'] == 'shipwreck':
            self.draw_shipwreck_interior(surface)
            return
        if self.current_building['type'] == 'shop':
            self.draw_shop_interior(surface)
        elif self.current_building['type'] == 'inn':
            self.draw_inn_interior(surface)
        elif self.current_building['type'] == 'house':
            self.draw_house_interior(surface)
        elif self.current_building['type'] == 'blacksmith':
            self.draw_blacksmith_interior(surface)
        elif self.current_building['type'] == 'temple':
            self.draw_temple_interior(surface)
        elif self.current_building['type'] == 'library':
            self.draw_library_interior(surface)
        elif self.current_building['type'] == 'tower':
            self.draw_tower_interior(surface)
        else:
            # Generic interior for any other building types
            self.draw_generic_interior(surface)
        
        # Draw player in interior
        player_center_x = SCREEN_WIDTH // 2
        player_center_y = SCREEN_HEIGHT // 2 + 100
        
        if self.player.char_class == "Warrior":
            points = [
                (player_center_x, player_center_y - 16),
                (player_center_x + 16, player_center_y),
                (player_center_x, player_center_y + 16),
                (player_center_x - 16, player_center_y)
            ]
            pygame.draw.polygon(surface, BLUE, points)
            pygame.draw.polygon(surface, WHITE, points, 2)
        elif self.player.char_class == "Mage":
            points = []
            for i in range(10):
                angle = i * math.pi / 5
                radius = 16 if i % 2 == 0 else 8
                x = player_center_x + radius * math.cos(angle - math.pi/2)
                y = player_center_y + radius * math.sin(angle - math.pi/2)
                points.append((x, y))
            pygame.draw.polygon(surface, BLUE, points)
            pygame.draw.polygon(surface, YELLOW, points, 2)
        elif self.player.char_class == "Rogue":
            points = [
                (player_center_x, player_center_y - 16),
                (player_center_x + 14, player_center_y + 14),
                (player_center_x - 14, player_center_y + 14)
            ]
            pygame.draw.polygon(surface, BLUE, points)
            pygame.draw.polygon(surface, DARK_GRAY, points, 2)
        
        # Draw exit instruction
        exit_text = self.small_font.render("Press ESC to exit", True, WHITE)
        surface.blit(exit_text, (10, 10))
    
    def draw_shop_interior(self, surface):
        """Draw shop interior with items for sale and trading interface"""
        # Shop counter
        pygame.draw.rect(surface, BROWN, (SCREEN_WIDTH//2 - 100, 150, 200, 40))
        pygame.draw.rect(surface, DARK_GRAY, (SCREEN_WIDTH//2 - 100, 150, 200, 40), 3)
        
        # Shopkeeper
        pygame.draw.circle(surface, (255, 220, 177), (SCREEN_WIDTH//2, 130), 15)  # Head
        pygame.draw.rect(surface, GREEN, (SCREEN_WIDTH//2 - 10, 145, 20, 30))  # Body
        
        # Shop title
        shop_title = self.font.render("Ye Olde Shop", True, YELLOW)
        surface.blit(shop_title, (SCREEN_WIDTH//2 - shop_title.get_width()//2, 50))
        
        # Player's gold
        gold_text = self.small_font.render(f"Your Gold: {self.player.gold}", True, WHITE)
        surface.blit(gold_text, (50, 100))
        
        # Shop inventory display
        inventory_title = self.small_font.render("Items for Sale:", True, WHITE)
        surface.blit(inventory_title, (50, 200))
        
        # List shop items
        for i, shop_item in enumerate(self.shop_inventory):
            item = shop_item['item']
            quantity = shop_item['quantity']
            
            # Highlight selected item
            if i == self.selected_item_index:
                highlight_rect = pygame.Rect(45, 225 + i * 25, SCREEN_WIDTH - 90, 20)
                pygame.draw.rect(surface, DARK_GRAY, highlight_rect)
                pygame.draw.rect(surface, YELLOW, highlight_rect, 2)
            
            # Item info text
            if isinstance(item, Weapon):
                item_text = f"⚔ {item.name} (Dmg: +{item.damage}) - {item.value}g (Stock: {quantity})"
            elif isinstance(item, Armor):
                item_text = f"🛡 {item.name} (Def: +{item.defense}) - {item.value}g (Stock: {quantity})"
            else:
                item_text = f"📦 {item.name} - {item.value}g (Stock: {quantity})"
            
            # Color based on affordability and stock
            if quantity <= 0:
                color = GRAY
            elif self.player.gold >= item.value:
                color = WHITE
            else:
                color = RED
            
            item_surface = self.small_font.render(item_text, True, color)
            surface.blit(item_surface, (50, 230 + i * 25))
        
        # Controls
        controls_y = SCREEN_HEIGHT - 150
        controls = [
            "↑↓ - Navigate Items",
            "ENTER/B - Buy Item", 
            "S - Sell Last Item",
            "ESC - Exit Shop"
        ]
        
        for i, control in enumerate(controls):
            control_surface = self.small_font.render(control, True, LIGHT_GRAY)
            surface.blit(control_surface, (50, controls_y + i * 20))
        
        # Selected item details
        if self.shop_inventory and self.selected_item_index < len(self.shop_inventory):
            selected_item = self.shop_inventory[self.selected_item_index]['item']
            
            detail_y = controls_y - 80
            detail_title = self.small_font.render("Selected Item:", True, YELLOW)
            surface.blit(detail_title, (SCREEN_WIDTH//2 + 50, detail_y))
            
            detail_name = self.small_font.render(selected_item.name, True, WHITE)
            surface.blit(detail_name, (SCREEN_WIDTH//2 + 50, detail_y + 25))
            
            detail_price = self.small_font.render(f"Price: {selected_item.value} gold", True, WHITE)
            surface.blit(detail_price, (SCREEN_WIDTH//2 + 50, detail_y + 45))
            
            if isinstance(selected_item, Weapon):
                detail_stats = self.small_font.render(f"Damage: +{selected_item.damage}", True, WHITE)
                surface.blit(detail_stats, (SCREEN_WIDTH//2 + 50, detail_y + 65))
            elif isinstance(selected_item, Armor):
                detail_stats = self.small_font.render(f"Defense: +{selected_item.defense}", True, WHITE)
                surface.blit(detail_stats, (SCREEN_WIDTH//2 + 50, detail_y + 65))
    
    def draw_inn_interior(self, surface):
        """Draw inn interior with beds and fireplace"""
        abandoned = self.current_building.get('abandoned', False)

        # Fireplace (cold and dark once abandoned)
        pygame.draw.rect(surface, DARK_GRAY, (50, 200, 80, 60))
        if abandoned:
            pygame.draw.rect(surface, (40, 30, 30), (60, 210, 60, 40))
        else:
            pygame.draw.rect(surface, RED, (60, 210, 60, 40))
            # Fire effect
            for i in range(5):
                flame_x = 70 + random.randint(-10, 40)
                flame_y = 220 + random.randint(-10, 20)
                pygame.draw.circle(surface, YELLOW, (flame_x, flame_y), random.randint(3, 8))

        # Beds (stripped bare if abandoned)
        for i in range(2):
            bed_x = SCREEN_WIDTH//2 + i * 120 - 60
            bed_y = 300
            pygame.draw.rect(surface, BROWN, (bed_x, bed_y, 100, 60))  # Bed frame
            if not abandoned:
                pygame.draw.rect(surface, WHITE, (bed_x + 10, bed_y + 10, 80, 40))  # Mattress

        if not abandoned:
            # Innkeeper present
            pygame.draw.circle(surface, (255, 220, 177), (SCREEN_WIDTH//2, 180), 15)
            pygame.draw.rect(surface, BLUE, (SCREEN_WIDTH//2 - 10, 195, 20, 30))
            inn_text = self.small_font.render("Rest here to recover! Press R to rest (one stay only)", True, WHITE)
        else:
            inn_text = self.small_font.render("Abandoned. The innkeeper is long gone.", True, (180, 170, 160))
        surface.blit(inn_text, (SCREEN_WIDTH//2 - inn_text.get_width()//2, 100))
    
    def draw_house_interior(self, surface):
        """Draw house interior with furniture and any stash you found"""
        # Table
        pygame.draw.rect(surface, BROWN, (SCREEN_WIDTH//2 - 40, 250, 80, 60))

        # Chairs
        for i in range(2):
            chair_x = SCREEN_WIDTH//2 - 60 + i * 120
            pygame.draw.rect(surface, BROWN, (chair_x, 280, 20, 40))
            pygame.draw.rect(surface, BROWN, (chair_x, 280, 20, 20))

        # Bookshelf
        pygame.draw.rect(surface, BROWN, (100, 180, 40, 120))
        for i in range(4):
            book_y = 190 + i * 25
            pygame.draw.rect(surface, random.choice([RED, BLUE, GREEN]), (110, book_y, 20, 15))

        # Storage chest on the table (open once searched)
        chest_x = SCREEN_WIDTH // 2 - 16
        searched = self.current_building.get('searched', False)
        pygame.draw.rect(surface, (110, 75, 30) if not searched else DARK_GRAY, (chest_x, 226, 32, 22))
        pygame.draw.rect(surface, YELLOW, (chest_x, 226, 32, 22), 2)
        if not searched:
            pygame.draw.circle(surface, YELLOW, (chest_x + 16, 237), 3)

        house_text = self.small_font.render("A cozy home", True, WHITE)
        surface.blit(house_text, (SCREEN_WIDTH//2 - house_text.get_width()//2, 40))
        summary = self.current_building.get('loot_summary', [])
        for i, line in enumerate(summary):
            col = (255, 240, 160) if line.startswith(("Gold", "Item", "Loot")) else WHITE
            txt = self.small_font.render(line, True, col)
            surface.blit(txt, (SCREEN_WIDTH//2 - txt.get_width()//2, 90 + i * 26))
    
    def draw_blacksmith_interior(self, surface):
        """Draw blacksmith interior with forge and anvil"""
        # Forge with fire
        pygame.draw.rect(surface, DARK_GRAY, (100, 200, 100, 80))
        pygame.draw.rect(surface, RED, (110, 210, 80, 60))
        
        # Animated fire
        for i in range(8):
            flame_x = 120 + random.randint(-20, 50)
            flame_y = 230 + random.randint(-15, 25)
            pygame.draw.circle(surface, YELLOW, (flame_x, flame_y), random.randint(4, 12))
        
        # Anvil
        pygame.draw.rect(surface, GRAY, (SCREEN_WIDTH//2, 280, 40, 20))
        pygame.draw.rect(surface, GRAY, (SCREEN_WIDTH//2 + 10, 260, 20, 20))
        
        # Tools on wall
        for i in range(3):
            tool_x = 300 + i * 30
            pygame.draw.line(surface, BROWN, (tool_x, 150), (tool_x, 180), 3)
            pygame.draw.circle(surface, GRAY, (tool_x, 150), 5)
        
        # Blacksmith
        pygame.draw.circle(surface, (255, 220, 177), (SCREEN_WIDTH//2, 180), 15)
        pygame.draw.rect(surface, BROWN, (SCREEN_WIDTH//2 - 10, 195, 20, 30))
        
        blacksmith_text = self.small_font.render("Welcome to my forge!", True, WHITE)
        surface.blit(blacksmith_text, (SCREEN_WIDTH//2 - blacksmith_text.get_width()//2, 60))

        gold_text = self.small_font.render(f"Your gold: {self.player.gold}", True, (255, 220, 120))
        surface.blit(gold_text, (SCREEN_WIDTH//2 - gold_text.get_width()//2, 88))

        # Upgrade menu: show each equipped slot, its current stat, and the cost
        slots = [
            ('1', 'Weapon', 'equipped_weapon', 'damage', 'dmg'),
            ('2', 'Armor',  'equipped_armor',  'defense', 'def'),
            ('3', 'Helmet', 'equipped_helmet', 'defense', 'def'),
            ('4', 'Shield', 'equipped_shield', 'defense', 'def'),
            ('5', 'Boots',  'equipped_boots',  'defense', 'def'),
        ]
        y = 400
        for key, label, attr, stat_attr, stat_lbl in slots:
            gear = getattr(self.player, attr, None)
            if gear:
                cur = getattr(gear, stat_attr, 0)
                cost = 80 + cur * 40
                afford = self.player.gold >= cost
                line = f"[{key}] Upgrade {label}: {gear.name} (+{cur} {stat_lbl}) -> +{cur + 1}  ({cost} gold)"
                col = (255, 240, 160) if afford else (130, 130, 130)
            else:
                line = f"[{key}] {label}: nothing equipped"
                col = (110, 110, 110)
            txt = self.small_font.render(line, True, col)
            surface.blit(txt, (SCREEN_WIDTH//2 - txt.get_width()//2, y))
            y += 26
    
    def draw_temple_interior(self, surface):
        """Draw temple interior with altar and religious symbols"""
        # Altar
        pygame.draw.rect(surface, GRAY, (SCREEN_WIDTH//2 - 40, 200, 80, 40))
        pygame.draw.rect(surface, YELLOW, (SCREEN_WIDTH//2 - 35, 205, 70, 30))
        
        # Candles on altar
        for i in range(3):
            candle_x = SCREEN_WIDTH//2 - 20 + i * 20
            pygame.draw.rect(surface, WHITE, (candle_x, 190, 4, 15))
            pygame.draw.circle(surface, YELLOW, (candle_x + 2, 188), 3)
        
        # Stained glass windows (simplified)
        for i in range(2):
            win_x = 150 + i * 400
            pygame.draw.rect(surface, BLUE, (win_x, 150, 20, 40))
            pygame.draw.rect(surface, RED, (win_x + 20, 150, 20, 40))
            pygame.draw.rect(surface, GREEN, (win_x + 40, 150, 20, 40))
        
        # Priest
        pygame.draw.circle(surface, (255, 220, 177), (SCREEN_WIDTH//2, 160), 15)
        pygame.draw.rect(surface, WHITE, (SCREEN_WIDTH//2 - 12, 175, 24, 35))
        
        temple_text = self.small_font.render("Blessings upon you, traveler", True, WHITE)
        surface.blit(temple_text, (SCREEN_WIDTH//2 - temple_text.get_width()//2, 60))

        shard_text = self.small_font.render(f"Your Soul Shards: {self.player.shards}", True, (150, 220, 255))
        surface.blit(shard_text, (SCREEN_WIDTH//2 - shard_text.get_width()//2, 90))

        y = 300
        for key, (name, cost, desc) in self.TEMPLE_BLESSINGS.items():
            line = f"[{key}] {name} ({cost} shards) - {desc}"
            afford = self.player.shards >= cost
            col = (255, 240, 160) if afford else (130, 130, 130)
            txt = self.small_font.render(line, True, col)
            surface.blit(txt, (SCREEN_WIDTH//2 - txt.get_width()//2, y))
            y += 30
    
    def draw_library_interior(self, surface):
        """Draw library interior with bookshelves and reading area"""
        # Bookshelves
        for shelf_num in range(4):
            shelf_x = 100 + shelf_num * 150
            pygame.draw.rect(surface, BROWN, (shelf_x, 180, 30, 120))
            
            # Books on shelves
            for row in range(5):
                for book in range(3):
                    book_x = shelf_x + 5 + book * 8
                    book_y = 190 + row * 20
                    book_color = random.choice([RED, BLUE, GREEN, YELLOW, (128, 0, 128)])
                    pygame.draw.rect(surface, book_color, (book_x, book_y, 6, 15))
        
        # Reading table
        pygame.draw.rect(surface, BROWN, (SCREEN_WIDTH//2 - 50, 320, 100, 60))
        
        # Open book on table
        pygame.draw.rect(surface, WHITE, (SCREEN_WIDTH//2 - 20, 330, 40, 25))
        pygame.draw.line(surface, BLACK, (SCREEN_WIDTH//2, 330), (SCREEN_WIDTH//2, 355), 1)
        
        # Librarian
        pygame.draw.circle(surface, (255, 220, 177), (SCREEN_WIDTH//2, 180), 15)
        pygame.draw.rect(surface, GRAY, (SCREEN_WIDTH//2 - 10, 195, 20, 30))
        
        library_text = self.small_font.render("Shh... Welcome to the library", True, WHITE)
        surface.blit(library_text, (SCREEN_WIDTH//2 - library_text.get_width()//2, 60))
        summary = self.current_building.get('loot_summary', [])
        for i, line in enumerate(summary):
            col = (170, 200, 255) if line.startswith(("Knowledge", "Intelligence", "Focus")) else WHITE
            txt = self.small_font.render(line, True, col)
            surface.blit(txt, (SCREEN_WIDTH//2 - txt.get_width()//2, 95 + i * 26))
    
    def draw_tower_interior(self, surface):
        """Draw tower interior with spiral stairs and magical elements"""
        # Spiral staircase
        for i in range(8):
            step_angle = i * math.pi / 4
            step_x = SCREEN_WIDTH//2 + int(40 * math.cos(step_angle))
            step_y = 200 + i * 15
            pygame.draw.rect(surface, GRAY, (step_x - 15, step_y, 30, 8))
        
        # Central pillar
        pygame.draw.circle(surface, GRAY, (SCREEN_WIDTH//2, 250), 20)
        
        # Magical orb at top
        pygame.draw.circle(surface, BLUE, (SCREEN_WIDTH//2, 180), 15)
        pygame.draw.circle(surface, WHITE, (SCREEN_WIDTH//2, 180), 15, 2)
        
        # Magical sparkles
        for i in range(6):
            spark_x = SCREEN_WIDTH//2 + random.randint(-30, 30)
            spark_y = 180 + random.randint(-30, 30)
            pygame.draw.circle(surface, YELLOW, (spark_x, spark_y), 2)
        
        # Wizard
        pygame.draw.circle(surface, (255, 220, 177), (200, 300), 15)
        pygame.draw.rect(surface, BLUE, (190, 315, 20, 30))
        # Wizard hat
        pygame.draw.polygon(surface, BLUE, [(200, 285), (210, 300), (190, 300)])
        
        tower_text = self.small_font.render("Welcome to my tower of magic!", True, WHITE)
        surface.blit(tower_text, (SCREEN_WIDTH//2 - tower_text.get_width()//2, 60))
        summary = self.current_building.get('loot_summary', [])
        for i, line in enumerate(summary):
            col = (170, 200, 255) if line.startswith(("Max MP", "Mana", "Item", "Loot")) else WHITE
            txt = self.small_font.render(line, True, col)
            surface.blit(txt, (SCREEN_WIDTH//2 - txt.get_width()//2, 95 + i * 26))
    
    def draw_generic_interior(self, surface):
        """Draw generic interior for unknown building types"""
        # Simple room with basic furniture
        pygame.draw.rect(surface, BROWN, (SCREEN_WIDTH//2 - 30, 250, 60, 40))

        generic_text = self.small_font.render("A mysterious place...", True, WHITE)
        surface.blit(generic_text, (SCREEN_WIDTH//2 - generic_text.get_width()//2, 100))

    def draw_shipwreck_interior(self, surface):
        """Show the inside of a plundered shipwreck and its loot summary."""
        t = pygame.time.get_ticks()
        cx = SCREEN_WIDTH // 2
        # Dim, damp hold
        for y in range(0, SCREEN_HEIGHT, 4):
            f = y / SCREEN_HEIGHT
            surface.fill((int(30 + 20 * f), int(28 + 22 * f), int(40 + 30 * f)), (0, y, SCREEN_WIDTH, 4))
        # Hull ribs
        for i in range(-4, 5):
            xrib = cx + i * 110
            pygame.draw.line(surface, (60, 45, 28), (xrib, 60), (cx + i * 60, SCREEN_HEIGHT - 60), 6)
        # Shimmering water at the bottom of the hold
        for wx in range(0, SCREEN_WIDTH, 12):
            wy = SCREEN_HEIGHT - 70 + int(5 * math.sin(t * 0.004 + wx * 0.05))
            pygame.draw.line(surface, (40, 90, 130), (wx, wy), (wx + 12, wy), 3)

        title = self.font.render("Shipwreck Hold", True, (230, 220, 170))
        surface.blit(title, (cx - title.get_width() // 2, 70))

        summary = self.current_building.get('loot_summary', ["Press ESC to leave"])
        for i, line in enumerate(summary):
            col = (255, 240, 160) if line.startswith(("Gold", "Loot", "EXP", "Shards")) else WHITE
            txt = self.small_font.render(line, True, col)
            surface.blit(txt, (cx - txt.get_width() // 2, 150 + i * 34))

        # Treasure pile decoration
        for i in range(8):
            gx = cx - 80 + (i * 23) % 160
            gy = SCREEN_HEIGHT - 130 + (i % 3) * 8
            pygame.draw.circle(surface, (235, 200, 70), (gx, gy), 6)
            pygame.draw.circle(surface, (255, 230, 130), (gx - 2, gy - 2), 2)
    
    def draw_terrain(self, surface):
        """Draw terrain tiles as background"""
        for tile in self.terrain_tiles:
            if tile['type'] == "water":
                # Animated water effect
                wave_offset = int(5 * math.sin(pygame.time.get_ticks() * 0.005 + tile['x'] * 0.01))
                color = (0, 100 + wave_offset, 200 + wave_offset)
            elif tile['type'] == "grass":
                color = (34, 139, 34)
            elif tile['type'] == "forest":
                color = (0, 100, 0)
            elif tile['type'] == "hills":
                color = (139, 115, 85)
            elif tile['type'] == "mountain":
                color = (105, 105, 105)
            else:
                color = DARK_GREEN
            
            pygame.draw.rect(surface, color, (tile['x'], tile['y'], tile['size'], tile['size']))
            
            # Add texture variation
            if tile['type'] == "grass":
                # Add grass texture dots
                for i in range(3):
                    dot_x = tile['x'] + random.randint(5, tile['size'] - 5)
                    dot_y = tile['y'] + random.randint(5, tile['size'] - 5)
                    pygame.draw.circle(surface, (20, 120, 20), (dot_x, dot_y), 1)
            elif tile['type'] == "mountain":
                # Add mountain texture lines
                pygame.draw.line(surface, (80, 80, 80), 
                               (tile['x'], tile['y'] + 10), 
                               (tile['x'] + tile['size'], tile['y'] + 10), 2)
    
    def draw_buildings(self, surface):
        """Draw buildings and structures"""
        for building in self.buildings:
            x, y = building['x'], building['y']
            width, height = building['width'], building['height']
            color = building['color']
            
            if building['type'] == 'house':
                # Draw house with roof
                pygame.draw.rect(surface, color, (x, y + 16, width, height - 16))
                # Roof triangle
                roof_points = [
                    (x, y + 16),
                    (x + width, y + 16),
                    (x + width//2, y)
                ]
                pygame.draw.polygon(surface, (139, 35, 35), roof_points)
                # Door
                pygame.draw.rect(surface, (101, 67, 33), (x + width//2 - 6, y + height - 12, 12, 12))
                # Windows
                pygame.draw.rect(surface, YELLOW, (x + 8, y + 24, 8, 8))
                pygame.draw.rect(surface, YELLOW, (x + width - 16, y + 24, 8, 8))
            
            elif building['type'] == 'tower':
                # Draw cylindrical tower
                pygame.draw.rect(surface, color, (x, y + 16, width, height - 16))
                # Tower top
                pygame.draw.circle(surface, (169, 169, 169), (x + width//2, y + 16), width//2)
                # Flag
                pygame.draw.line(surface, BLACK, (x + width//2, y), (x + width//2, y + 16), 2)
                pygame.draw.polygon(surface, RED, [(x + width//2, y), (x + width//2 + 12, y + 6), (x + width//2, y + 12)])
            
            elif building['type'] == 'shop':
                # Draw shop with sign
                pygame.draw.rect(surface, color, (x, y + 8, width, height - 8))
                # Sign
                pygame.draw.rect(surface, BROWN, (x + 4, y, width - 8, 12))
                pygame.draw.rect(surface, WHITE, (x + 8, y + 2, width - 16, 8))
                # Door
                pygame.draw.rect(surface, (101, 67, 33), (x + width//2 - 8, y + height - 16, 16, 16))
            
            elif building['type'] == 'inn':
                # Draw inn with multiple windows
                pygame.draw.rect(surface, color, (x, y + 12, width, height - 12))
                # Roof
                pygame.draw.polygon(surface, (139, 35, 35), [
                    (x, y + 12), (x + width, y + 12), (x + width//2, y)
                ])
                # Multiple windows
                for i in range(3):
                    win_x = x + 8 + i * (width - 16) // 2
                    pygame.draw.rect(surface, YELLOW, (win_x, y + 20, 8, 8))
                # Door
                pygame.draw.rect(surface, (101, 67, 33), (x + width//2 - 8, y + height - 16, 16, 16))
            
            elif building['type'] == 'blacksmith':
                # Draw blacksmith with chimney
                pygame.draw.rect(surface, color, (x, y + 8, width, height - 8))
                # Chimney
                pygame.draw.rect(surface, DARK_GRAY, (x + width - 12, y, 8, 20))
                # Smoke effect
                for i in range(3):
                    smoke_y = y - 5 - i * 8
                    pygame.draw.circle(surface, GRAY, (x + width - 8, smoke_y), 3 + i)
                # Forge window (glowing)
                pygame.draw.rect(surface, RED, (x + 8, y + 16, 12, 8))
                # Door
                pygame.draw.rect(surface, (101, 67, 33), (x + width//2 - 8, y + height - 16, 16, 16))
            
            elif building['type'] == 'temple':
                # Draw temple with columns
                pygame.draw.rect(surface, color, (x, y + 16, width, height - 16))
                # Triangular pediment
                pygame.draw.polygon(surface, WHITE, [
                    (x, y + 16), (x + width, y + 16), (x + width//2, y)
                ])
                # Columns
                for i in range(4):
                    col_x = x + 8 + i * (width - 16) // 3
                    pygame.draw.rect(surface, WHITE, (col_x, y + 16, 4, height - 16))
                # Door
                pygame.draw.rect(surface, (101, 67, 33), (x + width//2 - 8, y + height - 16, 16, 16))
            
            elif building['type'] == 'library':
                # Draw library with books visible in windows
                pygame.draw.rect(surface, color, (x, y + 12, width, height - 12))
                # Roof
                pygame.draw.polygon(surface, (139, 35, 35), [
                    (x, y + 12), (x + width, y + 12), (x + width//2, y)
                ])
                # Windows with books
                for i in range(2):
                    win_x = x + 12 + i * (width - 24)
                    pygame.draw.rect(surface, YELLOW, (win_x, y + 20, 12, 12))
                    # Books in window
                    for j in range(3):
                        book_color = random.choice([RED, BLUE, GREEN])
                        pygame.draw.rect(surface, book_color, (win_x + 2 + j * 3, y + 22, 2, 8))
                # Door
                pygame.draw.rect(surface, (101, 67, 33), (x + width//2 - 8, y + height - 16, 16, 16))
            
            elif building['type'] == 'tower':
                # Draw cylindrical tower (enterable now)
                pygame.draw.rect(surface, color, (x, y + 16, width, height - 16))
                # Tower top
                pygame.draw.circle(surface, (169, 169, 169), (x + width//2, y + 16), width//2)
                # Flag
                pygame.draw.line(surface, BLACK, (x + width//2, y), (x + width//2, y + 16), 2)
                pygame.draw.polygon(surface, RED, [(x + width//2, y), (x + width//2 + 12, y + 6), (x + width//2, y + 12)])
                # Door (now has one!)
                pygame.draw.rect(surface, (101, 67, 33), (x + width//2 - 8, y + height - 16, 16, 16))
    
    def draw_vegetation(self, surface):
        """Draw trees, bushes, and other vegetation"""
        for veg in self.vegetation:
            x, y = veg['x'], veg['y']
            size = veg['size']
            color = veg['color']
            
            if veg['type'] == 'tree':
                # Draw tree trunk
                trunk_width = size // 4
                pygame.draw.rect(surface, BROWN, (x - trunk_width//2, y, trunk_width, size))
                # Draw tree canopy
                pygame.draw.circle(surface, color, (x, y - size//2), size//2)
                pygame.draw.circle(surface, (0, 80, 0), (x, y - size//2), size//2, 2)
            
            elif veg['type'] == 'bush':
                # Draw bush as multiple small circles
                pygame.draw.circle(surface, color, (x, y), size)
                pygame.draw.circle(surface, (20, 100, 20), (x - size//2, y), size//2)
                pygame.draw.circle(surface, (40, 120, 40), (x + size//2, y), size//2)
            
            elif veg['type'] == 'flower':
                # Draw flower stem
                pygame.draw.line(surface, GREEN, (x, y), (x, y + size * 2), 2)
                # Draw flower petals
                for i in range(6):
                    angle = i * math.pi / 3
                    petal_x = x + int(size * math.cos(angle))
                    petal_y = y + int(size * math.sin(angle))
                    pygame.draw.circle(surface, color, (petal_x, petal_y), size//2)
                # Center
                pygame.draw.circle(surface, YELLOW, (x, y), size//3)
            
            elif veg['type'] == 'rock':
                # Draw rock as irregular polygon
                points = []
                for i in range(6):
                    angle = i * math.pi / 3
                    radius = size + random.randint(-size//3, size//3)
                    rock_x = x + int(radius * math.cos(angle))
                    rock_y = y + int(radius * math.sin(angle))
                    points.append((rock_x, rock_y))
                pygame.draw.polygon(surface, color, points)
                pygame.draw.polygon(surface, DARK_GRAY, points, 2)
    
    def draw_boss_arena_on_surface(self, surface):
        t = pygame.time.get_ticks()
        a = self.arena
        boss = self.current_enemy
        W, H = self.ARENA_SIZE
        cam_x, cam_y = a['cam_x'], a['cam_y']
        cx = SCREEN_WIDTH // 2

        def sx(x): return int(x - cam_x)
        def sy(y): return int(y - cam_y)

        # Dark backdrop gradient
        for y in range(0, SCREEN_HEIGHT, 3):
            f = y / SCREEN_HEIGHT
            surface.fill((int(14 + 30 * f), int(4 + 8 * f), int(20 + 34 * f)), (0, y, SCREEN_WIDTH, 3))

        # Arena floor (only the visible portion)
        fl = max(0, sx(0)); ft = max(0, sy(0))
        fr = min(SCREEN_WIDTH, sx(W)); fb = min(SCREEN_HEIGHT, sy(H))
        if fr > fl and fb > ft:
            floor = pygame.Surface((fr - fl, fb - ft), pygame.SRCALPHA)
            floor.fill((50, 18, 48, 120))
            surface.blit(floor, (fl, ft))

        # Floor grid lines relative to camera
        first_gx = int(cam_x // 64) * 64
        for gx in range(first_gx, int(cam_x) + SCREEN_WIDTH + 64, 64):
            if 0 <= gx <= W:
                pygame.draw.line(surface, (80, 40, 90), (sx(gx), max(0, sy(0))), (sx(gx), min(SCREEN_HEIGHT, sy(H))), 1)
        first_gy = int(cam_y // 64) * 64
        for gy in range(first_gy, int(cam_y) + SCREEN_HEIGHT + 64, 64):
            if 0 <= gy <= H:
                pygame.draw.line(surface, (80, 40, 90), (max(0, sx(0)), sy(gy)), (min(SCREEN_WIDTH, sx(W)), sy(gy)), 1)

        # Pulsing sealed barrier walls (at arena edges)
        pulse = int(120 + 80 * math.sin(t * 0.006))
        for k in range(6):
            col = (210, 70, 230, max(0, pulse - k * 30))
            rect = pygame.Rect(sx(0) - k, sy(0) - k, W + 2 * k, H + 2 * k)
            pygame.draw.rect(surface, col, rect, 2)

        # Corner runes
        for rx, ry in [(0, 0), (W, 0), (0, H), (W, H)]:
            rr = int(13 + 4 * math.sin(t * 0.008))
            rune = pygame.Surface((rr * 2 + 4, rr * 2 + 4), pygame.SRCALPHA)
            pygame.draw.circle(rune, (255, 130, 50, 180), (rr + 2, rr + 2), rr, 2)
            surface.blit(rune, (sx(rx) - rr, sy(ry) - rr))

        # Cover pillars (block fireballs and movement)
        for ox, oy, ow, oh in a['obstacles']:
            pygame.draw.rect(surface, (0, 0, 0), (sx(ox) + 4, sy(oy) + 6, ow, oh))   # shadow
            pygame.draw.rect(surface, (70, 64, 90), (sx(ox), sy(oy), ow, oh))         # body
            pygame.draw.rect(surface, (120, 110, 150), (sx(ox), sy(oy), ow, max(6, oh // 5)))  # top edge
            pygame.draw.rect(surface, (200, 180, 230), (sx(ox), sy(oy), ow, oh), 2)   # outline

        # Player projectiles
        for pr in a['projectiles']:
            glow = pygame.Surface((20, 20), pygame.SRCALPHA)
            pygame.draw.circle(glow, (255, 160, 40, 120), (10, 10), 9)
            surface.blit(glow, (sx(pr['x']) - 10, sy(pr['y']) - 10))
            pygame.draw.circle(surface, (255, 230, 120), (sx(pr['x']), sy(pr['y'])), 4)

        # Boss projectiles (ranged blasts)
        for pr in a['boss_proj']:
            glow = pygame.Surface((26, 26), pygame.SRCALPHA)
            pygame.draw.circle(glow, (220, 40, 200, 130), (13, 13), 12)
            surface.blit(glow, (sx(pr['x']) - 13, sy(pr['y']) - 13))
            pygame.draw.circle(surface, (255, 120, 230), (sx(pr['x']), sy(pr['y'])), 6)
            pygame.draw.circle(surface, (255, 220, 255), (sx(pr['x']), sy(pr['y'])), 3)

        # Charge telegraph: red line showing the dash path during wind-up
        if a.get('charge_phase') == 1:
            fxd, fyd = a['charge_dir']
            tx = sx(a['bx'] + fxd * 480)
            ty = sy(a['by'] + fyd * 480)
            pulse = int(120 + 100 * math.sin(t * 0.03))
            tele = pygame.Surface((SCREEN_WIDTH, SCREEN_HEIGHT), pygame.SRCALPHA)
            pygame.draw.line(tele, (255, 60, 60, pulse), (sx(a['bx']), sy(a['by'])), (tx, ty), 6)
            surface.blit(tele, (0, 0))

        # Boss shadow + body (slight scaling tells you what phase the boss is in)
        boss_size = 32
        if a.get('charge_phase') == 1:
            boss_size = 30   # crouched, winding up
        elif a.get('charge_phase') == 2:
            boss_size = 36   # mid-dash, larger
        pygame.draw.ellipse(surface, (0, 0, 0, 120), (sx(a['bx']) - 36, sy(a['by']) + 24, 72, 22))
        bfacing = 1 if a['px'] >= a['bx'] else -1
        bax, bay = self.anim_offset(boss, bfacing, reach=boss_size)
        self._draw_mob_shape(surface, boss.name,
                             sx(a['bx']) + bax, sy(a['by']) + bay, boss_size, bfacing,
                             tint=self.hurt_phase(boss))
        self.draw_swing_arc(surface, boss, sx(a['bx']) + bax, sy(a['by']) + bay,
                            int(boss_size * 1.7), bfacing, color=(255, 150, 220))
        self._draw_nametag(surface, boss.name, sx(a['bx']),
                           sy(a['by']) - boss_size - 20, (255, 220, 120))

        # Boss health bar (top)
        bw, bh = 600, 18
        bx = cx - bw // 2
        ratio = max(0.0, boss.hp / boss.max_hp)
        pygame.draw.rect(surface, (40, 0, 0), (bx, 28, bw, bh))
        pygame.draw.rect(surface, (220, 40, 40), (bx, 28, int(bw * ratio), bh))
        pygame.draw.rect(surface, (255, 210, 120), (bx, 28, bw, bh), 2)
        nm = self.font.render(boss.name, True, (255, 220, 140))
        surface.blit(nm, (cx - nm.get_width() // 2, 4))

        # Player shadow + body (blink during i-frames)
        if not (a['p_iframes'] > 0 and (t // 80) % 2 == 0):
            pygame.draw.ellipse(surface, (0, 0, 0, 120), (sx(a['px']) - 16, sy(a['py']) + 14, 32, 12))
            pfacing = 1 if a['face'][0] >= 0 else -1
            self.draw_character_in_combat(surface, self.player,
                                          sx(a['px']), sy(a['py']), True, 16, pfacing)
            # Aiming arc along the exact facing vector, on top of the swing animation
            if a['p_swing'] > 0:
                fxd, fyd = a['face']
                ang = math.atan2(fyd, fxd)
                pygame.draw.arc(surface, (255, 255, 200),
                                (sx(a['px']) - 44, sy(a['py']) - 44, 88, 88), ang - 0.7, ang + 0.7, 2)

        # Minimap (top-right) showing the whole arena
        mm_w, mm_h = 180, int(180 * H / W)
        mm_x, mm_y = SCREEN_WIDTH - mm_w - 16, 56
        mm = pygame.Surface((mm_w, mm_h), pygame.SRCALPHA)
        mm.fill((20, 10, 26, 180))
        for ox, oy, ow, oh in a['obstacles']:
            pygame.draw.rect(mm, (140, 130, 170),
                             (int(ox / W * mm_w), int(oy / H * mm_h),
                              max(2, int(ow / W * mm_w)), max(2, int(oh / H * mm_h))))
        pygame.draw.rect(mm, (210, 70, 230), mm.get_rect(), 1)
        surface.blit(mm, (mm_x, mm_y))
        px_m = mm_x + int(a['px'] / W * mm_w); py_m = mm_y + int(a['py'] / H * mm_h)
        bx_m = mm_x + int(a['bx'] / W * mm_w); by_m = mm_y + int(a['by'] / H * mm_h)
        pygame.draw.circle(surface, (255, 60, 60), (bx_m, by_m), 4)
        pygame.draw.circle(surface, (90, 200, 255), (px_m, py_m), 3)

        # Player HP / MP bars (bottom-left)
        php = max(0.0, self.player.hp / self.player.max_hp)
        pygame.draw.rect(surface, (50, 0, 0), (20, SCREEN_HEIGHT - 60, 240, 16))
        pygame.draw.rect(surface, (60, 200, 60), (20, SCREEN_HEIGHT - 60, int(240 * php), 16))
        pygame.draw.rect(surface, WHITE, (20, SCREEN_HEIGHT - 60, 240, 16), 1)
        hp_t = self.small_font.render(f"HP {self.player.hp}/{self.player.max_hp}", True, WHITE)
        surface.blit(hp_t, (24, SCREEN_HEIGHT - 58))
        if self.player.max_mp:
            pmp = max(0.0, self.player.mp / self.player.max_mp)
            pygame.draw.rect(surface, (0, 0, 50), (20, SCREEN_HEIGHT - 40, 240, 12))
            pygame.draw.rect(surface, (80, 140, 255), (20, SCREEN_HEIGHT - 40, int(240 * pmp), 12))
            pygame.draw.rect(surface, WHITE, (20, SCREEN_HEIGHT - 40, 240, 12), 1)

        # Controls hint (bottom-right)
        hint = "WASD Move  SPACE Attack  H Heal  I Items" + ("  F Fireball" if self.player.char_class == "Mage" else "")
        ht = self.small_font.render(hint, True, (255, 200, 120))
        surface.blit(ht, (SCREEN_WIDTH - ht.get_width() - 20, SCREEN_HEIGHT - 44))
        seal = self.small_font.render("THE BATTLEFIELD IS SEALED — NO ESCAPE", True, (255, 90, 90))
        surface.blit(seal, (SCREEN_WIDTH - seal.get_width() - 20, SCREEN_HEIGHT - 64))
        ult = self._ultimate_status()
        if ult:
            txt = self.small_font.render(ult[0], True, ult[1])
            surface.blit(txt, (SCREEN_WIDTH - txt.get_width() - 20, SCREEN_HEIGHT - 84))
        sk = self._skill_status()
        if sk:
            txt = self.small_font.render(sk[0], True, sk[1])
            surface.blit(txt, (SCREEN_WIDTH - txt.get_width() - 20, SCREEN_HEIGHT - 104))

    def draw_combat_on_surface(self, surface):
        t = pygame.time.get_ticks()
        dim = self.current_dimension
        cx = SCREEN_WIDTH // 2

        palettes = {
            'overworld': {'sky_top': (58, 102, 168), 'sky_bot': (172, 206, 226),
                          'ground': (74, 128, 60),  'ground_dark': (38, 76, 36),  'accent': (255, 240, 170)},
            'nether':    {'sky_top': (48, 8, 4),    'sky_bot': (152, 46, 12),
                          'ground': (54, 18, 14),   'ground_dark': (24, 9, 7),    'accent': (255, 130, 40)},
            'void':      {'sky_top': (6, 0, 22),    'sky_bot': (34, 12, 66),
                          'ground': (28, 14, 50),   'ground_dark': (12, 6, 26),   'accent': (175, 125, 255)},
            'celestial': {'sky_top': (150, 182, 240),'sky_bot': (240, 232, 200),
                          'ground': (205, 188, 122),'ground_dark': (166, 146, 90),'accent': (255, 250, 205)},
        }
        pal = palettes.get(dim, palettes['overworld'])

        def lerp(a, b, f):
            return (max(0, min(255, int(a[0] + (b[0] - a[0]) * f))),
                    max(0, min(255, int(a[1] + (b[1] - a[1]) * f))),
                    max(0, min(255, int(a[2] + (b[2] - a[2]) * f))))

        horizon = 420

        # --- Sky gradient ---
        for y in range(0, horizon, 3):
            surface.fill(lerp(pal['sky_top'], pal['sky_bot'], y / horizon), (0, y, SCREEN_WIDTH, 3))
        # --- Ground gradient ---
        for y in range(horizon, SCREEN_HEIGHT, 3):
            f = (y - horizon) / (SCREEN_HEIGHT - horizon)
            surface.fill(lerp(pal['ground_dark'], pal['ground'], f), (0, y, SCREEN_WIDTH, 3))

        # Horizon glow (sun / moon / portal light)
        glow = pygame.Surface((360, 360), pygame.SRCALPHA)
        pygame.draw.circle(glow, (*pal['accent'], 55), (180, 180), 180)
        pygame.draw.circle(glow, (*pal['accent'], 45), (180, 180), 110)
        pygame.draw.circle(glow, (*pal['accent'], 60), (180, 180), 55)
        surface.blit(glow, (cx - 180, horizon - 220))

        # Perspective ground lines converging to the horizon centre
        grid_col = lerp(pal['ground'], pal['ground_dark'], 0.55)
        for i in range(-8, 9):
            pygame.draw.line(surface, grid_col, (cx, horizon), (cx + i * 150, SCREEN_HEIGHT), 1)
        for j in range(1, 6):
            yy = horizon + int((SCREEN_HEIGHT - horizon) * (j / 6) ** 1.7)
            pygame.draw.line(surface, grid_col, (0, yy), (SCREEN_WIDTH, yy), 1)

        # --- Atmosphere particles ---
        for i in range(46):
            seed = i * 131 + 7
            base_x = (seed * 53) % SCREEN_WIDTH
            if dim == 'nether':
                pcol = (255, 110 + (i * 9) % 110, 30)         # rising embers
                px = (base_x + int(math.sin(t * 0.001 + i) * 14)) % SCREEN_WIDTH
                py = (horizon + 60) - ((int(t * 0.06) + seed * 17) % (horizon + 60))
                r = 1 + (i % 2)
            elif dim == 'void':
                pcol = (200, 180, 255)                         # drifting stars
                px = (base_x + int(t * 0.01) + i * 3) % SCREEN_WIDTH
                py = (seed * 29) % horizon
                r = 1 + (i % 2)
            elif dim == 'celestial':
                pcol = (255, 255, 240)                         # falling light motes
                px = base_x
                py = (int(t * 0.04) + seed * 11) % horizon
                r = 1 + (i % 2)
            else:
                pcol = (255, 255, 255)                         # pollen / dust
                px = (base_x + int(t * 0.008)) % SCREEN_WIDTH
                py = int((seed * 23) % horizon + 16 * math.sin(t * 0.002 + i))
                r = 1
            a = 110 + int(90 * math.sin(t * 0.004 + i))
            ps = pygame.Surface((r * 2 + 2, r * 2 + 2), pygame.SRCALPHA)
            pygame.draw.circle(ps, (*pcol, max(0, min(255, a))), (r + 1, r + 1), r)
            surface.blit(ps, (px, py))

        # --- Combatants ---
        player_x = cx - 230
        enemy_x = cx + 230
        feet_y = horizon + 34
        csize = 46
        p_cy = int(feet_y - csize + math.sin(t * 0.005) * 5)
        e_cy = int(feet_y - csize + math.sin(t * 0.005 + 1.2) * 5)

        # Platform shadows + discs at the feet
        for ex in (player_x, enemy_x):
            shadow = pygame.Surface((csize * 3, csize), pygame.SRCALPHA)
            pygame.draw.ellipse(shadow, (0, 0, 0, 90), shadow.get_rect())
            surface.blit(shadow, (ex - csize * 3 // 2, feet_y - csize // 2 + 4))
            disc = (ex - csize, feet_y - csize // 3, csize * 2, csize * 2 // 3)
            pygame.draw.ellipse(surface, lerp(pal['ground'], (255, 255, 255), 0.18), disc)
            pygame.draw.ellipse(surface, pal['accent'], disc, 2)

        # Aura glows behind each combatant
        for ex, ey, col in ((player_x, p_cy, (80, 160, 255)), (enemy_x, e_cy, (255, 80, 80))):
            pulse = int(18 + 12 * math.sin(t * 0.006))
            aura = pygame.Surface((csize * 4, csize * 4), pygame.SRCALPHA)
            pygame.draw.circle(aura, (*col, 45), (csize * 2, csize * 2), csize + pulse)
            pygame.draw.circle(aura, (*col, 28), (csize * 2, csize * 2), csize + pulse + 12)
            surface.blit(aura, (ex - csize * 2, ey - csize * 2))

        self.draw_character_in_combat(surface, self.player, player_x, p_cy, True, csize)
        self.draw_enemy_in_combat(surface, self.current_enemy, enemy_x, e_cy, csize)

        # Floating nameplates above heads
        self._draw_combat_nameplate(surface, self.player, player_x, p_cy - csize - 62, True)
        self._draw_combat_nameplate(surface, self.current_enemy, enemy_x, e_cy - csize - 62, False)

        # VS clash emblem between them
        vs_scale = 1.0 + 0.15 * math.sin(t * 0.008)
        vs_font = pygame.font.Font(None, int(66 * vs_scale))
        vs_shadow = vs_font.render("VS", True, (0, 0, 0))
        vs_text = vs_font.render("VS", True, (255, 70, 70))
        vsx = cx - vs_text.get_width() // 2
        vsy = (p_cy + e_cy) // 2 - vs_text.get_height() // 2
        surface.blit(vs_shadow, (vsx + 3, vsy + 3))
        surface.blit(vs_text, (vsx, vsy))

        # --- Title banner ---
        title = f"Battle:  {self.current_enemy.name}   (Lv {self.current_enemy.level})"
        tsurf = self.font.render(title, True, WHITE)
        banner = pygame.Surface((tsurf.get_width() + 48, tsurf.get_height() + 18), pygame.SRCALPHA)
        banner.fill((0, 0, 0, 150))
        pygame.draw.rect(banner, pal['accent'], banner.get_rect(), 2)
        surface.blit(banner, (cx - banner.get_width() // 2, 22))
        surface.blit(tsurf, (cx - tsurf.get_width() // 2, 22 + 9))

        # --- Bottom action bar ---
        bar_h = 120
        bar_y = SCREEN_HEIGHT - bar_h
        bar = pygame.Surface((SCREEN_WIDTH, bar_h), pygame.SRCALPHA)
        bar.fill((10, 10, 18, 225))
        surface.blit(bar, (0, bar_y))
        pygame.draw.line(surface, pal['accent'], (0, bar_y), (SCREEN_WIDTH, bar_y), 2)

        options = [("A", "Attack", (120, 200, 120))]
        if self.player.char_class == "Mage":
            options.append(("S", "Fireball", (255, 200, 80)))
        options.append(("H", "Heal", (120, 210, 210)))
        options.append(("I", "Items", (200, 180, 255)))
        options.append(("R", "Run", (230, 110, 110)))

        n = len(options)
        gap, btn_h, margin = 16, 58, 30
        btn_w = min(190, (SCREEN_WIDTH - 2 * margin - (n - 1) * gap) // n)
        total = n * btn_w + (n - 1) * gap
        sx = (SCREEN_WIDTH - total) // 2
        by = bar_y + (bar_h - btn_h) // 2
        for i, (key, label, col) in enumerate(options):
            bx = sx + i * (btn_w + gap)
            btn = pygame.Surface((btn_w, btn_h), pygame.SRCALPHA)
            btn.fill((30, 30, 45, 240))
            surface.blit(btn, (bx, by))
            pygame.draw.rect(surface, col, (bx, by, btn_w, btn_h), 2)
            chip = self.font.render(key, True, col)
            surface.blit(chip, (bx + 12, by + (btn_h - chip.get_height()) // 2))
            lbl = self.small_font.render(label, True, WHITE)
            surface.blit(lbl, (bx + 44, by + (btn_h - lbl.get_height()) // 2))
    
    def draw_character_in_combat(self, surface, character, x, y, is_player, size=20,
                                 facing=1):
        # Larger, scaled class representation with shadow + outline
        s = size
        # Fighting animation: lunge on the strike, recoil when hit
        ax, ay = self.anim_offset(character, facing, reach=size)
        x, y = x + ax, y + ay
        body = self.hurt_tint(character, (60, 120, 230))
        if character.char_class == "Warrior":
            points = [(x, y - s), (x + s, y), (x, y + s), (x - s, y)]
            outline = WHITE
        elif character.char_class == "Mage":
            points = []
            for i in range(10):
                angle = i * math.pi / 5
                radius = s if i % 2 == 0 else s // 2
                points.append((x + radius * math.cos(angle - math.pi / 2),
                               y + radius * math.sin(angle - math.pi / 2)))
            outline = YELLOW
        elif character.char_class == "Rogue":
            points = [(x, y - s), (x + int(s * 0.9), y + int(s * 0.9)), (x - int(s * 0.9), y + int(s * 0.9))]
            outline = DARK_GRAY
        else:
            points = [(x, y - s), (x + s, y), (x, y + s), (x - s, y)]
            outline = WHITE

        # darker shadow shape behind for depth
        shadow_pts = [(px + 3, py + 3) for px, py in points]
        pygame.draw.polygon(surface, (20, 30, 70), shadow_pts)
        pygame.draw.polygon(surface, body, points)
        # inner highlight
        hi_pts = [(x + (px - x) * 0.5, y + (py - y) * 0.5) for px, py in points]
        pygame.draw.polygon(surface, self.hurt_tint(character, (120, 180, 255)), hi_pts)
        pygame.draw.polygon(surface, outline, points, 3)
        # Weapon arc sweeping through the swing
        self.draw_swing_arc(surface, character, x, y, int(size * 1.6), facing)

    def draw_enemy_in_combat(self, surface, enemy, x, y, size=20):
        # Player is drawn on the left in combat, so the enemy squares up facing left
        ax, ay = self.anim_offset(enemy, -1, reach=size)
        self._draw_mob_shape(surface, enemy.name, x + ax, y + ay, size, -1,
                             tint=self.hurt_phase(enemy))
        self.draw_swing_arc(surface, enemy, x + ax, y + ay, int(size * 1.6), -1,
                            color=(255, 190, 150))
        self._draw_nametag(surface, f"{enemy.name} Lv{enemy.level}",
                           x, y - size - 22, (255, 200, 200))

    def _draw_combat_nameplate(self, surface, character, cx, top_y, is_player):
        bar_w, bar_h = 160, 12
        bx = cx - bar_w // 2

        # Name
        name_s = self.small_font.render(character.name, True, WHITE)
        ns = pygame.Surface((name_s.get_width() + 12, name_s.get_height() + 4), pygame.SRCALPHA)
        ns.fill((0, 0, 0, 130))
        surface.blit(ns, (cx - ns.get_width() // 2, top_y))
        surface.blit(name_s, (cx - name_s.get_width() // 2, top_y + 2))

        # HP bar
        by = top_y + name_s.get_height() + 8
        ratio = max(0.0, min(1.0, character.hp / character.max_hp))
        hp_col = (60, 200, 60) if ratio > 0.5 else (220, 180, 40) if ratio > 0.25 else (220, 60, 40)
        pygame.draw.rect(surface, (50, 0, 0), (bx, by, bar_w, bar_h))
        pygame.draw.rect(surface, hp_col, (bx, by, int(bar_w * ratio), bar_h))
        pygame.draw.rect(surface, WHITE, (bx, by, bar_w, bar_h), 1)
        hp_txt = self.small_font.render(f"{character.hp}/{character.max_hp}", True, WHITE)
        surface.blit(hp_txt, (cx - hp_txt.get_width() // 2, by + bar_h + 1))

        # MP bar (player only)
        if is_player and getattr(character, 'max_mp', 0):
            by2 = by + bar_h + 20
            mratio = max(0.0, min(1.0, character.mp / character.max_mp))
            pygame.draw.rect(surface, (0, 0, 50), (bx, by2, bar_w, 8))
            pygame.draw.rect(surface, (80, 140, 255), (bx, by2, int(bar_w * mratio), 8))
            pygame.draw.rect(surface, WHITE, (bx, by2, bar_w, 8), 1)
    
    def draw_quest_log_on_surface(self, surface):
        """Dedicated quest journal screen (J in the playing state)."""
        # Frame
        pygame.draw.rect(surface, (18, 18, 28), (50, 50, 924, 668))
        pygame.draw.rect(surface, (200, 180, 80), (50, 50, 924, 668), 3)

        title = self.font.render("Quest Journal", True, (255, 220, 120))
        surface.blit(title, (SCREEN_WIDTH // 2 - title.get_width() // 2, 70))

        quests = getattr(self.player, 'active_quests', None) or [] if self.player else []
        if not quests:
            empty = self.small_font.render(
                "No active quests. Talk to townsfolk marked with ! to pick one up.",
                True, (180, 180, 180))
            surface.blit(empty, (SCREEN_WIDTH // 2 - empty.get_width() // 2, 200))
        else:
            y = 140
            for i, q in enumerate(quests):
                col_state = (140, 230, 140) if q.get('state') == 'complete' else (220, 220, 220)
                # Card background
                card_h = 110
                pygame.draw.rect(surface, (30, 30, 40), (90, y, 844, card_h))
                pygame.draw.rect(surface, col_state, (90, y, 844, card_h), 2)

                # Name
                nm = self.font.render(q.get('name', 'Quest'), True, (255, 220, 120))
                surface.blit(nm, (105, y + 8))

                # Status badge
                if q.get('state') == 'complete':
                    badge = self.small_font.render("READY TO TURN IN", True, (140, 240, 140))
                else:
                    badge = self.small_font.render("ACTIVE", True, (200, 200, 200))
                surface.blit(badge, (840 - badge.get_width(), y + 12))

                # Description
                desc = self.small_font.render(q.get('desc', ''), True, (220, 220, 220))
                surface.blit(desc, (105, y + 40))

                # Progress / target
                qtype = q.get('type')
                if qtype in ('kill', 'shipwreck'):
                    prog_txt = f"Progress: {q['progress']}/{q['target']}"
                elif qtype == 'reach_dim':
                    prog_txt = f"Travel to: the {q['target']}"
                else:
                    prog_txt = ""
                if prog_txt:
                    prog = self.small_font.render(prog_txt, True, (180, 220, 255))
                    surface.blit(prog, (105, y + 62))

                # Progress bar (kill/shipwreck only)
                if qtype in ('kill', 'shipwreck'):
                    ratio = max(0.0, min(1.0, q['progress'] / max(1, q['target'])))
                    bx, by_, bw, bh = 350, y + 64, 320, 8
                    pygame.draw.rect(surface, (30, 30, 30), (bx, by_, bw, bh))
                    fill_col = (140, 230, 140) if ratio >= 1.0 else (120, 180, 255)
                    pygame.draw.rect(surface, fill_col, (bx, by_, int(bw * ratio), bh))
                    pygame.draw.rect(surface, (160, 160, 160), (bx, by_, bw, bh), 1)

                # Rewards
                parts = []
                if q.get('reward_gold'):   parts.append(f"+{q['reward_gold']} gold")
                if q.get('reward_exp'):    parts.append(f"+{q['reward_exp']} EXP")
                if q.get('reward_shards'): parts.append(f"+{q['reward_shards']} shards")
                rew = self.small_font.render("Rewards: " + ", ".join(parts), True, (255, 220, 120))
                surface.blit(rew, (105, y + 82))

                # Giver
                giver = q.get('giver', '')
                if giver:
                    gv = self.small_font.render(f"From: {giver}", True, (180, 180, 200))
                    surface.blit(gv, (840 - gv.get_width(), y + 82))

                y += card_h + 14
                if y > 640:
                    break

        # Footer
        footer = self.small_font.render(
            "Press J or ESC to close   -   Talk to NPCs to accept and turn in quests",
            True, (180, 180, 180))
        surface.blit(footer, (SCREEN_WIDTH // 2 - footer.get_width() // 2, 690))

    def draw_inventory_on_surface(self, surface):
        # Outer frame with a parchment-style gradient and a golden border
        for y in range(50, 718, 4):
            f = (y - 50) / 668
            col = (int(34 + 8 * (1 - f)), int(30 + 6 * (1 - f)), int(40 + 10 * (1 - f)))
            surface.fill(col, (50, y, 924, 4))
        # Outer & inner decorative borders
        pygame.draw.rect(surface, (200, 170, 80), (50, 50, 924, 668), 3)
        pygame.draw.rect(surface, (90, 75, 40), (54, 54, 916, 660), 1)

        # Header bar with title
        pygame.draw.rect(surface, (20, 18, 28), (54, 54, 916, 56))
        pygame.draw.line(surface, (200, 170, 80), (54, 110), (970, 110), 2)
        inv_text = self.font.render("Character & Inventory", True, (255, 230, 150))
        surface.blit(inv_text, (74, 64))

        # Capacity indicator with colored backdrop
        count = len(getattr(self.player, 'inventory', []) or [])
        cap = getattr(self.player, 'max_inventory', 24)
        if count >= cap:
            cap_col = (240, 90, 90)
        elif count >= cap - 2:
            cap_col = (230, 190, 90)
        else:
            cap_col = (180, 230, 200)
        cap_text = self.small_font.render(f"Bag: {count}/{cap}", True, cap_col)
        cap_x = 950 - cap_text.get_width() - 10
        pygame.draw.rect(surface, (20, 18, 28), (cap_x - 10, 72, cap_text.get_width() + 20, 28))
        pygame.draw.rect(surface, cap_col, (cap_x - 10, 72, cap_text.get_width() + 20, 28), 1)
        surface.blit(cap_text, (cap_x, 78))

        # Three-column section header bars: Equipment | Stats & Quests | Inventory
        def _section_header(label, x, w, color):
            pygame.draw.rect(surface, (24, 22, 32), (x, 120, w, 26))
            pygame.draw.rect(surface, color, (x, 120, w, 26), 1)
            txt = self.small_font.render(label, True, color)
            surface.blit(txt, (x + 10, 124))

        _section_header("EQUIPMENT", 70, 230, (170, 200, 255))
        _section_header("STATS & QUESTS", 310, 220, (255, 220, 140))
        _section_header("INVENTORY", 540, 410, (160, 240, 180))
        
        # Draw paper doll (left side)
        self.draw_paper_doll(surface, 70, 156)

        # Player stats (middle)
        stats_x = 320
        stats_y = 156
        stats_title = self.small_font.render("Character Stats:", True, YELLOW)
        surface.blit(stats_title, (stats_x, stats_y))
        
        stats = [
            f"Level: {self.player.level}",
            f"HP: {self.player.hp}/{self.player.max_hp}",
            f"MP: {self.player.mp}/{self.player.max_mp}",
            f"Gold: {self.player.gold}",
            f"Soul Shards: {self.player.shards}",
            f"Celestial Essence: {getattr(self.player, 'essence', 0)}",
            f"EXP: {self.player.exp}/{self.player.exp_to_next}",
            "",
            f"Strength: {self.player.strength}",
            f"Dexterity: {self.player.dexterity}",
            f"Intelligence: {self.player.intelligence}",
            f"Constitution: {self.player.constitution}",
            "",
            f"Attack Bonus: +{self.player.get_attack_bonus()}",
            f"Defense: {self.player.get_defense()}"
        ]
        
        for i, stat in enumerate(stats):
            if stat:  # Skip empty strings
                stat_text = self.small_font.render(stat, True, WHITE)
                surface.blit(stat_text, (stats_x, stats_y + 25 + i * 20))

        # Active quest log (below stats, middle column)
        q_title = self.small_font.render("Active Quests:", True, YELLOW)
        surface.blit(q_title, (stats_x, 480))
        quests = getattr(self.player, 'active_quests', None) or []
        if not quests:
            none_txt = self.small_font.render("  (none — talk to townsfolk)", True, GRAY)
            surface.blit(none_txt, (stats_x, 504))
        else:
            for i, q in enumerate(quests[:6]):
                qtype = q.get('type')
                if qtype in ('kill', 'shipwreck'):
                    prog = f"  ({q['progress']}/{q['target']})"
                elif qtype == 'reach_dim':
                    prog = f"  (-> {q['target']})"
                else:
                    prog = ""
                col = (180, 240, 180) if q.get('state') == 'complete' else WHITE
                txt = self.small_font.render(f"- {q['name']}{prog}", True, col)
                surface.blit(txt, (stats_x, 504 + i * 22))

        # Inventory items (right side, two columns to avoid overflow)
        inv_x = 550
        inv_y = 156
        col_w = 200          # column width per item slot
        row_h = 28           # taller rows so they breathe
        rows_per_col = 12    # 12 + 12 = 24-slot default fits comfortably
        inv_title = self.small_font.render("Inventory:", True, YELLOW)
        surface.blit(inv_title, (inv_x, inv_y))

        if hasattr(self.player, 'inventory') and self.player.inventory:
            for i, item in enumerate(self.player.inventory):
                col = i // rows_per_col
                row = i % rows_per_col
                rx = inv_x + col * col_w
                row_y = inv_y + 30 + row * row_h

                # Highlight selected item with a soft colored bar + golden left accent
                if i == self.inventory_selected_index:
                    highlight_rect = pygame.Rect(rx - 6, row_y - 4, col_w - 4, row_h - 2)
                    pygame.draw.rect(surface, (44, 38, 28), highlight_rect)
                    pygame.draw.rect(surface, (255, 220, 120),
                                     (highlight_rect.x, highlight_rect.y, 4, highlight_rect.height))
                    pygame.draw.rect(surface, (200, 170, 80), highlight_rect, 1)

                # Item icon
                self.draw_item_icon(surface, item, rx, row_y, 18)

                # Label is just the item name now — full stats live in the detail panel
                if isinstance(item, Weapon):
                    color = (255, 200, 200)
                elif isinstance(item, (Helmet, Shield, Boots, Armor)):
                    color = (200, 200, 255)
                elif isinstance(item, Ring):
                    color = (255, 220, 150)
                elif isinstance(item, Backpack):
                    color = (210, 170, 100)
                elif getattr(item, 'type', None) == "consumable":
                    color = (200, 255, 200)
                else:
                    color = WHITE
                item_text = self.small_font.render(item.name, True, color)
                surface.blit(item_text, (rx + 24, row_y))

        # ---- Selected-item detail panel (under the inventory grid) ----
        det_x, det_y, det_w, det_h = 540, 510, 410, 84
        pygame.draw.rect(surface, (20, 18, 28), (det_x, det_y, det_w, det_h))
        pygame.draw.rect(surface, (200, 170, 80), (det_x, det_y, det_w, det_h), 1)
        det_title = self.small_font.render("ITEM DETAILS", True, (200, 170, 80))
        surface.blit(det_title, (det_x + 10, det_y + 6))

        inv_list = getattr(self.player, 'inventory', []) or []
        if 0 <= self.inventory_selected_index < len(inv_list):
            item = inv_list[self.inventory_selected_index]
            self.draw_item_icon(surface, item, det_x + 12, det_y + 30, 18)
            name_txt = self.small_font.render(item.name, True, WHITE)
            surface.blit(name_txt, (det_x + 38, det_y + 30))

            # Stat lines specific to the type
            stat_lines = []
            if isinstance(item, Weapon):
                stat_lines.append(("Type:", "Weapon"))
                stat_lines.append(("Damage:", f"+{item.damage}"))
                if getattr(item, 'lifesteal', 0) > 0:
                    stat_lines.append(("Lifesteal:", f"{int(item.lifesteal * 100)}%"))
            elif isinstance(item, Helmet):
                stat_lines.append(("Type:", "Helmet")); stat_lines.append(("Defense:", f"+{item.defense}"))
            elif isinstance(item, Shield):
                stat_lines.append(("Type:", "Shield")); stat_lines.append(("Defense:", f"+{item.defense}"))
            elif isinstance(item, Boots):
                stat_lines.append(("Type:", "Boots"));  stat_lines.append(("Defense:", f"+{item.defense}"))
            elif isinstance(item, Armor):
                stat_lines.append(("Type:", "Armor"));  stat_lines.append(("Defense:", f"+{item.defense}"))
            elif isinstance(item, Ring):
                stat_lines.append(("Type:", "Ring"));   stat_lines.append(("Strength:", f"+{item.bonus}"))
            elif isinstance(item, Backpack):
                stat_lines.append(("Type:", "Backpack")); stat_lines.append(("Slots:", f"+{item.bonus}"))
            elif getattr(item, 'type', None) == 'consumable':
                stat_lines.append(("Type:", "Consumable"))
            stat_lines.append(("Value:", f"{getattr(item, 'value', 0)}g"))

            # Render the stat lines in two columns inside the detail panel
            col_x = [det_x + 12, det_x + 215]
            for i, (k, v) in enumerate(stat_lines):
                cx = col_x[i // 2]
                cy = det_y + 52 + (i % 2) * 18
                k_t = self.small_font.render(k, True, (180, 180, 200))
                v_t = self.small_font.render(v, True, (255, 230, 140))
                surface.blit(k_t, (cx, cy))
                surface.blit(v_t, (cx + 90, cy))
        else:
            empty = self.small_font.render("Select an item to inspect", True, (140, 140, 140))
            surface.blit(empty, (det_x + 12, det_y + 36))
        
        # Controls panel at the bottom
        controls_y = 600
        pygame.draw.rect(surface, (20, 18, 28), (60, controls_y - 4, 904, 110))
        pygame.draw.rect(surface, (200, 170, 80), (60, controls_y - 4, 904, 110), 1)
        ctrl_title = self.small_font.render("CONTROLS", True, (200, 170, 80))
        surface.blit(ctrl_title, (74, controls_y))
        controls = [
            "↑↓  Navigate Items",
            "ENTER/E  Use/Equip",
            "U  Unequip All",
            "D  Discard Item",
            "I/ESC  Close",
        ]
        # Lay out controls in two columns
        col_w = 440
        for i, control in enumerate(controls):
            col = i // 3
            row = i % 3
            control_text = self.small_font.render(control, True, (220, 220, 220))
            surface.blit(control_text, (74 + col * col_w, controls_y + 24 + row * 22))
    
    def draw_item_icon(self, surface, item, x, y, size=18):
        """Draw a small primitive-based icon representing the item at top-left (x, y)."""
        s = size
        cx, cy = x + s // 2, y + s // 2
        if isinstance(item, Weapon):
            pygame.draw.line(surface, (220, 220, 230), (cx, y), (cx, y + s - 5), 3)
            pygame.draw.rect(surface, (200, 160, 60), (x + 3, y + s - 8, s - 6, 3))
            pygame.draw.rect(surface, (100, 60, 30), (cx - 2, y + s - 5, 4, 5))
        elif isinstance(item, Helmet):
            pygame.draw.ellipse(surface, (130, 130, 140), (x + 2, y + 2, s - 4, s - 4))
            pygame.draw.arc(surface, (200, 200, 220), (x + 2, y + 2, s - 4, s - 4), 0, math.pi, 2)
            pygame.draw.line(surface, (60, 60, 70), (cx, y + 2), (cx, y + s // 2), 1)
        elif isinstance(item, Shield):
            pygame.draw.circle(surface, (140, 100, 60), (cx, cy), s // 2 - 1)
            pygame.draw.circle(surface, (220, 180, 80), (cx, cy), s // 2 - 1, 2)
            pygame.draw.line(surface, (60, 40, 20), (cx, y + 3), (cx, y + s - 3), 2)
            pygame.draw.line(surface, (60, 40, 20), (x + 3, cy), (x + s - 3, cy), 2)
        elif isinstance(item, Boots):
            pygame.draw.rect(surface, (90, 50, 30), (x + 3, y + 3, s - 8, s - 7))
            pygame.draw.rect(surface, (90, 50, 30), (x + 3, y + s - 4, s - 4, 3))
            pygame.draw.rect(surface, (60, 30, 15), (x + 3, y + 3, s - 8, s - 7), 1)
        elif isinstance(item, Ring):
            pygame.draw.circle(surface, (220, 200, 80), (cx, cy + 1), s // 2 - 2, 3)
            pygame.draw.circle(surface, (180, 80, 200), (cx, cy - 3), 3)
        elif isinstance(item, Backpack):
            pygame.draw.rect(surface, (80, 50, 25), (x + 3, y + 5, s - 6, s - 7))
            pygame.draw.rect(surface, (140, 90, 40), (x + 3, y + 5, s - 6, s - 7), 1)
            pygame.draw.rect(surface, (60, 30, 15), (x + s // 2 - 3, y + 2, 6, 4))
        elif isinstance(item, Armor):
            pts = [(x + 4, y + 5), (x + s - 4, y + 5), (x + s - 3, y + s - 3),
                   (cx, y + s - 5), (x + 3, y + s - 3)]
            pygame.draw.polygon(surface, (140, 130, 110), pts)
            pygame.draw.polygon(surface, (220, 200, 150), pts, 1)
        elif getattr(item, 'type', None) == "consumable":
            name = (getattr(item, 'name', '') or '').lower()
            if "mana" in name:
                liquid = (80, 140, 255)
            elif "stamina" in name:
                liquid = (220, 180, 30)
            elif "alderine" in name:
                liquid = (220, 80, 180)
            else:
                liquid = (220, 60, 60)
            pygame.draw.rect(surface, (220, 220, 230), (x + 5, y + 3, s - 10, 4))
            pygame.draw.ellipse(surface, (180, 180, 200), (x + 3, y + 6, s - 6, s - 9))
            pygame.draw.ellipse(surface, liquid, (x + 5, y + 8, s - 10, s - 12))
            pygame.draw.rect(surface, (120, 80, 40), (x + 6, y + 1, s - 12, 3))
        elif getattr(item, 'type', None) == "currency":
            pygame.draw.circle(surface, (240, 200, 60), (cx, cy), s // 2 - 2)
            pygame.draw.circle(surface, (180, 140, 30), (cx, cy), s // 2 - 2, 1)
        else:
            # Centered chest icon
            ch_w, ch_h = s - 4, s - 7
            ch_x = x + (s - ch_w) // 2
            ch_y = y + (s - ch_h) // 2
            pygame.draw.rect(surface, (110, 75, 30), (ch_x, ch_y, ch_w, ch_h))
            pygame.draw.rect(surface, (60, 30, 15), (ch_x, ch_y, ch_w, ch_h), 1)
            pygame.draw.line(surface, (60, 30, 15), (ch_x, ch_y + ch_h // 3),
                             (ch_x + ch_w, ch_y + ch_h // 3), 1)

    def draw_paper_doll(self, surface, x, y):
        """Draw the 6-slot equipment panel."""
        panel_w, panel_h = 220, 260
        pygame.draw.rect(surface, (40, 40, 40), (x, y, panel_w, panel_h))
        pygame.draw.rect(surface, WHITE, (x, y, panel_w, panel_h), 2)

        doll_title = self.small_font.render("Equipment", True, YELLOW)
        surface.blit(doll_title, (x + 10, y + 8))

        slots = [
            ("Weapon", getattr(self.player, 'equipped_weapon', None)),
            ("Helmet", getattr(self.player, 'equipped_helmet', None)),
            ("Armor",  getattr(self.player, 'equipped_armor',  None)),
            ("Shield", getattr(self.player, 'equipped_shield', None)),
            ("Boots",  getattr(self.player, 'equipped_boots',  None)),
            ("Ring",   getattr(self.player, 'equipped_ring',   None)),
        ]
        yy = y + 36
        for slot_name, item in slots:
            if item:
                self.draw_item_icon(surface, item, x + 12, yy - 2, 18)
                label = f"{slot_name}: {item.name}"
                col = WHITE
            else:
                # Empty slot indicator
                pygame.draw.rect(surface, (60, 60, 60), (x + 12, yy - 2, 18, 18), 1)
                label = f"{slot_name}: empty"
                col = GRAY
            txt = self.small_font.render(label, True, col)
            surface.blit(txt, (x + 36, yy))
            yy += 32
    
    def draw_game_over_on_surface(self, surface):
        surface.fill((12, 4, 4))
        cx = SCREEN_WIDTH // 2
        big = pygame.font.Font(None, 96)
        go = big.render("GAME OVER", True, (200, 40, 40))
        sh = big.render("GAME OVER", True, (60, 0, 0))
        surface.blit(sh, (cx - go.get_width() // 2 + 4, 220 + 4))
        surface.blit(go, (cx - go.get_width() // 2, 220))

        if self.player:
            sub = self.small_font.render(
                f"{self.player.name} the {self.player.char_class}  -  Level {self.player.level}", True, (200, 180, 180))
            surface.blit(sub, (cx - sub.get_width() // 2, 330))

        for i, line in enumerate(["ENTER - Return to Menu"] + ([] if IS_WEB else ["Q - Quit"])):
            txt = self.font.render(line, True, WHITE)
            surface.blit(txt, (cx - txt.get_width() // 2, 440 + i * 44))

    def draw_victory_on_surface(self, surface):
        t = pygame.time.get_ticks()
        cx = SCREEN_WIDTH // 2

        # Radiant golden backdrop
        for y in range(0, SCREEN_HEIGHT, 3):
            f = y / SCREEN_HEIGHT
            col = (int(40 + 120 * (1 - f)), int(30 + 90 * (1 - f)), int(10 + 30 * (1 - f)))
            surface.fill(col, (0, y, SCREEN_WIDTH, 3))

        # Bursting light rays from the centre
        for i in range(24):
            ang = i * (math.pi / 12) + t * 0.0005
            x2 = cx + math.cos(ang) * 900
            y2 = 250 + math.sin(ang) * 900
            pygame.draw.line(surface, (255, 240, 180), (cx, 250), (x2, y2), 1)

        # Falling sparkles
        for i in range(60):
            seed = i * 71 + 13
            px = (seed * 37) % SCREEN_WIDTH
            py = (int(t * 0.05) + seed * 9) % SCREEN_HEIGHT
            r = 1 + (i % 3)
            a = 120 + int(120 * math.sin(t * 0.004 + i))
            ps = pygame.Surface((r * 2 + 2, r * 2 + 2), pygame.SRCALPHA)
            pygame.draw.circle(ps, (255, 255, 220, max(0, min(255, a))), (r + 1, r + 1), r)
            surface.blit(ps, (px, py))

        # Title with glow and pulse
        scale = 1.0 + 0.05 * math.sin(t * 0.005)
        big = pygame.font.Font(None, int(110 * scale))
        title = "VICTORY!"
        glow = big.render(title, True, (120, 80, 0))
        main = big.render(title, True, (255, 230, 120))
        surface.blit(glow, (cx - glow.get_width() // 2 + 4, 170 + 4))
        surface.blit(main, (cx - main.get_width() // 2, 170))

        # Flavor text
        lines = [
            f"You have vanquished {FINAL_BOSS_NAME}!",
            "The realms are saved, and your legend is eternal.",
        ]
        for i, line in enumerate(lines):
            txt = self.font.render(line, True, WHITE)
            surface.blit(txt, (cx - txt.get_width() // 2, 320 + i * 40))

        # Run summary
        if self.player:
            summary = self.small_font.render(
                f"{self.player.name} the {self.player.char_class}   Level {self.player.level}   Gold {self.player.gold}",
                True, (255, 245, 210))
            surface.blit(summary, (cx - summary.get_width() // 2, 430))

        choices = [
            ("C - Continue Adventuring", (180, 240, 200)),
            ("ENTER - Play Again",       (255, 255, 240)),
        ] + ([] if IS_WEB else [("Q - Quit", (255, 255, 240))])
        for i, (line, col) in enumerate(choices):
            txt = self.font.render(line, True, col)
            surface.blit(txt, (cx - txt.get_width() // 2, 500 + i * 44))
    
    def draw_ui_on_surface(self, surface):
        # Semi-transparent panel behind stats
        panel = pygame.Surface((220, 200), pygame.SRCALPHA)
        panel.fill((0, 0, 0, 140))
        surface.blit(panel, (5, 5))

        # Health bar
        bar_width = 200
        bar_height = 16
        health_ratio = self.player.hp / self.player.max_hp
        pygame.draw.rect(surface, (80, 0, 0), (10, 10, bar_width, bar_height))
        pygame.draw.rect(surface, (220, 50, 50), (10, 10, int(bar_width * health_ratio), bar_height))
        pygame.draw.rect(surface, WHITE, (10, 10, bar_width, bar_height), 1)

        hp_text = self.small_font.render(f"HP: {self.player.hp}/{self.player.max_hp}", True, WHITE)
        surface.blit(hp_text, (10, 30))
        
        # Mana bar
        mana_ratio = self.player.mp / self.player.max_mp
        pygame.draw.rect(surface, (0, 0, 80), (10, 50, bar_width, bar_height))
        pygame.draw.rect(surface, (60, 100, 220), (10, 50, int(bar_width * mana_ratio), bar_height))
        pygame.draw.rect(surface, WHITE, (10, 50, bar_width, bar_height), 1)

        mp_text = self.small_font.render(f"MP: {self.player.mp}/{self.player.max_mp}", True, WHITE)
        surface.blit(mp_text, (10, 70))

        # Stamina bar
        stamina_ratio = self.player.stamina / self.player.max_stamina
        stamina_color = (220, 190, 0) if self.player.stamina > 30 else (200, 80, 0)
        pygame.draw.rect(surface, (60, 50, 0), (10, 90, bar_width, bar_height))
        pygame.draw.rect(surface, stamina_color, (10, 90, int(bar_width * stamina_ratio), bar_height))
        pygame.draw.rect(surface, WHITE, (10, 90, bar_width, bar_height), 1)

        sta_text = self.small_font.render(f"SP: {int(self.player.stamina)}/{self.player.max_stamina}", True, WHITE)
        surface.blit(sta_text, (10, 110))

        # Level and gold
        level_text = self.small_font.render(f"Level: {self.player.level}", True, WHITE)
        surface.blit(level_text, (10, 135))

        gold_text = self.small_font.render(f"Gold: {self.player.gold}", True, YELLOW)
        surface.blit(gold_text, (10, 155))

        shard_text = self.small_font.render(f"Shards: {self.player.shards}", True, (150, 220, 255))
        surface.blit(shard_text, (115, 155))

        essence = getattr(self.player, 'essence', 0)
        if essence:
            ess_text = self.small_font.render(f"Essence: {essence}", True, (255, 220, 140))
            surface.blit(ess_text, (10, 175))

        dim_colors = {'overworld': (80, 200, 80), 'nether': (220, 80, 20),
                      'void': (160, 80, 255), 'celestial': (200, 220, 255)}
        dim_label = self.current_dimension.replace('_', ' ').title()
        dim_surf = self.small_font.render(f"[ {dim_label} ]", True,
                                          dim_colors.get(self.current_dimension, WHITE))
        # Shift down if Essence is being displayed
        dim_y = 195 if getattr(self.player, 'essence', 0) else 175
        surface.blit(dim_surf, (10, dim_y))

        # Controls
        controls = [
            "WASD/Arrows - Move",
            "Shift - Sprint",
            "I - Inventory",
            "J - Quest Journal",
            "H - Heal (10 MP)",
            "E - Interact",
            "F5 - Save Game",
            "F11 - Fullscreen"
        ]

        for i, control in enumerate(controls):
            control_text = self.small_font.render(control, True, WHITE)
            surface.blit(control_text, (SCREEN_WIDTH - 200, 10 + i * 20))
    
    def draw_terrain_tile(self, surface, tile, screen_x, screen_y):
        """Draw a single terrain tile"""
        t = pygame.time.get_ticks()
        sz = tile['size']
        tx, ty = tile['x'], tile['y']
        # Deterministic hash for stable per-tile decoration (no per-frame flicker)
        th = (tx * 374761393 ^ ty * 668265263) & 0xFFFFFFFF

        if tile['type'] == "water":
            wave = math.sin(t * 0.002 + tx * 0.015 + ty * 0.01)
            r = int(20 + wave * 8)
            g = int(80 + wave * 15)
            b = int(180 + wave * 20)
            pygame.draw.rect(surface, (r, g, b), (screen_x, screen_y, sz, sz))
            # Static shimmer line (no SRCALPHA surface per tile)
            highlight_y = screen_y + int(sz * 0.3 + wave * sz * 0.1)
            pygame.draw.line(surface, (140, 200, 240), (screen_x, highlight_y), (screen_x + sz, highlight_y), 2)
        elif tile['type'] == "grass":
            shade = (th >> 4) % 20
            color = (30 + shade // 3, 130 + shade, 30 + shade // 3)
            pygame.draw.rect(surface, color, (screen_x, screen_y, sz, sz))
            # Deterministic grass blades + occasional flower dots
            for i in range(4):
                bx = screen_x + ((th >> (i * 7)) % (sz - 4)) + 2
                by = screen_y + ((th >> (i * 5 + 3)) % (sz - 4)) + 2
                pygame.draw.line(surface, (20, 110, 20), (bx, by + 2), (bx, by - 1), 1)
            if (th & 0xFF) < 10:
                fx = screen_x + ((th >> 12) % (sz - 6)) + 3
                fy = screen_y + ((th >> 18) % (sz - 6)) + 3
                pygame.draw.circle(surface, (255, 190, 60), (fx, fy), 1)
        elif tile['type'] == "forest":
            shade = (th >> 5) % 16
            color = (0, 80 + shade, 0)
            pygame.draw.rect(surface, color, (screen_x, screen_y, sz, sz))
            # A little tree canopy — trunk + rounded canopy per tile
            tx_ = screen_x + sz // 2
            ty_ = screen_y + sz // 2
            pygame.draw.rect(surface, (60, 40, 20), (tx_ - 1, ty_ + 2, 2, 6))
            pygame.draw.circle(surface, (10, 60 + shade, 10), (tx_, ty_), 6)
            pygame.draw.circle(surface, (30, 100 + shade, 30), (tx_ - 2, ty_ - 2), 3)
        elif tile['type'] == "hills":
            shade = (th >> 6) % 25
            # Softer green-brown ridged terrain
            color = (90 + shade // 2, 130 + shade, 60 + shade // 3)
            pygame.draw.rect(surface, color, (screen_x, screen_y, sz, sz))
            # Wavy contour lines to suggest elevation
            pygame.draw.arc(surface, (60, 90, 30),
                            (screen_x - 6, screen_y + 4, sz + 12, sz), math.pi, 2 * math.pi, 2)
            pygame.draw.arc(surface, (60, 90, 30),
                            (screen_x - 6, screen_y + sz // 2, sz + 12, sz), math.pi, 2 * math.pi, 1)
        elif tile['type'] == "mountain":
            # Real relief: colour comes from the tile's own elevation and how its
            # slope faces the light, so ridges and gullies run continuously across
            # tiles instead of every tile drawing an identical little peak.
            elev = tile.get('elev')
            if elev is None:
                # Chunk from an older save without stored relief — approximate it.
                elev = 0.78 + ((th >> 3) % 20) / 200.0
                lit = 0.4 + ((th >> 11) % 20) / 33.0
            else:
                lit = tile.get('lit', 0.5)

            # 0 at the snow line, 1 at the highest peaks
            high = max(0.0, min(1.0, (elev - 0.74) / 0.22))
            # Light multiplier: sunlit faces brighten, shaded faces fall into shadow
            shade_mul = 0.55 + 0.75 * lit

            # Bare rock: cool grey that warms slightly at lower altitude
            rock = (int((92 - 22 * high) * shade_mul),
                    int((88 - 20 * high) * shade_mul),
                    int((90 - 14 * high) * shade_mul))
            rock = tuple(max(0, min(255, c)) for c in rock)
            pygame.draw.rect(surface, rock, (screen_x, screen_y, sz, sz))

            # Rock banding — subtle strata that follow the slope, not a grid
            band = (max(0, rock[0] - 14), max(0, rock[1] - 14), max(0, rock[2] - 12))
            for i in range(2):
                by = screen_y + 8 + i * 13 + ((th >> (i * 5 + 2)) % 5)
                pygame.draw.line(surface, band, (screen_x, by),
                                 (screen_x + sz, by - 3), 1)

            # Snow above the snow line, thicker the higher we go. The threshold is
            # jittered per tile so the boundary is ragged rather than a clean step.
            snow_start = 0.34 + ((th >> 7) % 12) / 100.0
            if high > snow_start:
                cover = min(1.0, (high - snow_start) / (1.0 - snow_start))
                snow_v = int(215 + 40 * lit)
                snow = (min(255, snow_v), min(255, snow_v + 4), min(255, snow_v + 12))
                if cover > 0.85:
                    # Fully buried summit
                    pygame.draw.rect(surface, snow, (screen_x, screen_y, sz, sz))
                else:
                    # Snow settles on the upper part of the tile and in patches,
                    # leaving rock exposed on the steeper lower face.
                    depth = max(2, int(sz * cover))
                    pygame.draw.rect(surface, snow, (screen_x, screen_y, sz, depth))
                    # Ragged lower edge so the snow line isn't a straight cut
                    for i in range(4):
                        px = screen_x + i * (sz // 4)
                        extra = ((th >> (i * 4 + 1)) % 5)
                        pygame.draw.rect(surface, snow,
                                         (px, screen_y + depth, sz // 4, extra))
        elif tile['type'] == "desert":
            shade = (th >> 4) % 24
            # Warm sand base
            pygame.draw.rect(surface, (230 + shade // 8, 200 + shade // 4, 120 + shade // 3),
                             (screen_x, screen_y, sz, sz))
            # Ripples / dune curves
            pygame.draw.arc(surface, (200, 170, 90),
                            (screen_x - 4, screen_y + sz // 4, sz + 8, sz // 2), math.pi, 2 * math.pi, 1)
            pygame.draw.arc(surface, (200, 170, 90),
                            (screen_x - 4, screen_y + 3 * sz // 4, sz + 8, sz // 3), math.pi, 2 * math.pi, 1)
            # Occasional cactus (deterministic per tile)
            if (th & 0xFF) < 8:
                cx = screen_x + sz // 2
                cy = screen_y + sz // 2
                pygame.draw.rect(surface, (60, 130, 60), (cx - 1, cy - 4, 2, 8))
                pygame.draw.rect(surface, (60, 130, 60), (cx - 4, cy - 2, 3, 2))
                pygame.draw.rect(surface, (60, 130, 60), (cx + 2, cy - 3, 3, 2))
        elif tile['type'] == "snow":
            shade = (th >> 4) % 12
            # Pale snowy base
            pygame.draw.rect(surface, (230 + shade // 3, 235 + shade // 3, 245),
                             (screen_x, screen_y, sz, sz))
            # Sparkles / snowdrift dots
            for i in range(3):
                sxp = screen_x + ((th >> (i * 7)) % (sz - 4)) + 2
                syp = screen_y + ((th >> (i * 5 + 3)) % (sz - 4)) + 2
                pygame.draw.circle(surface, (255, 255, 255), (sxp, syp), 1)
            # Occasional pine (dark triangle) sticking up
            if (th & 0xFF) < 10:
                cx = screen_x + sz // 2
                cy = screen_y + sz // 2
                pygame.draw.polygon(surface, (30, 70, 40),
                                    [(cx, cy - 6), (cx - 4, cy + 4), (cx + 4, cy + 4)])
                pygame.draw.rect(surface, (60, 40, 20), (cx - 1, cy + 4, 2, 3))
        elif tile['type'] == "swamp":
            shade = (th >> 5) % 18
            # Murky green-brown base
            pygame.draw.rect(surface, (60 + shade // 3, 80 + shade, 45 + shade // 4),
                             (screen_x, screen_y, sz, sz))
            # Reeds
            for i in range(3):
                rx = screen_x + ((th >> (i * 6)) % (sz - 4)) + 2
                ry = screen_y + ((th >> (i * 8 + 1)) % (sz - 6)) + 4
                pygame.draw.line(surface, (30, 60, 20), (rx, ry + 4), (rx, ry - 2), 1)
                pygame.draw.line(surface, (30, 60, 20), (rx, ry - 2), (rx - 1, ry - 4), 1)
            # Occasional lily pad or dark puddle
            if (th & 0xFF) < 30:
                pxp = screen_x + ((th >> 10) % (sz - 6)) + 3
                pyp = screen_y + ((th >> 14) % (sz - 6)) + 3
                pygame.draw.ellipse(surface, (40, 55, 30),
                                    (pxp - 3, pyp - 2, 6, 4))
                pygame.draw.line(surface, (80, 130, 60), (pxp - 3, pyp), (pxp + 3, pyp), 1)
        elif tile['type'] == 'lava':
            pulse = math.sin(t * 0.003 + tx * 0.01 + ty * 0.008)
            r = int(220 + pulse * 20)
            g = int(60 + pulse * 30)
            pygame.draw.rect(surface, (r, g, 0), (screen_x, screen_y, sz, sz))
        elif tile['type'] == 'ash':
            shade = (th >> 4) % 20
            pygame.draw.rect(surface, (60 + shade, 50 + shade, 50 + shade), (screen_x, screen_y, sz, sz))
        elif tile['type'] == 'nether_rock':
            pygame.draw.rect(surface, (80, 20, 10), (screen_x, screen_y, sz, sz))
            pygame.draw.line(surface, (50, 10, 5), (screen_x, screen_y + sz // 2), (screen_x + sz, screen_y + sz // 3), 1)
        elif tile['type'] == 'void':
            pygame.draw.rect(surface, (5, 0, 15), (screen_x, screen_y, sz, sz))
            # subtle star
            if (th & 0xFF) < 8:
                pygame.draw.circle(surface, (150, 120, 200), (screen_x + (th >> 8) % sz, screen_y + (th >> 16) % sz), 1)
        elif tile['type'] == 'void_island':
            shade = (th >> 4) % 20
            pygame.draw.rect(surface, (40 + shade, 20 + shade, 70 + shade), (screen_x, screen_y, sz, sz))
        elif tile['type'] == 'void_crystal':
            pygame.draw.rect(surface, (80, 0, 140), (screen_x, screen_y, sz, sz))
            pygame.draw.line(surface, (160, 80, 255), (screen_x + sz // 2, screen_y), (screen_x + sz // 3, screen_y + sz), 2)
        elif tile['type'] == 'cloud':
            pulse = abs(math.sin(t * 0.001 + tx * 0.005))
            col = int(200 + pulse * 40)
            pygame.draw.rect(surface, (col, col, 255), (screen_x, screen_y, sz, sz))
        elif tile['type'] == 'gold_ground':
            shade = (th >> 4) % 20
            pygame.draw.rect(surface, (180 + shade // 2, 160 + shade, 60 + shade // 4), (screen_x, screen_y, sz, sz))
        elif tile['type'] == 'celestial_peak':
            pygame.draw.rect(surface, (230, 220, 180), (screen_x, screen_y, sz, sz))
            pygame.draw.rect(surface, (255, 240, 200), (screen_x, screen_y, sz, sz // 3))
        else:
            pygame.draw.rect(surface, DARK_GREEN, (screen_x, screen_y, sz, sz))
    
    def draw_building(self, surface, building, screen_x, screen_y):
        """Draw a single building"""
        w, h = building['width'], building['height']
        color = building['color']
        btype = building['type']

        # Angled drop shadow — a parallelogram leaning southeast, like a low NW sun
        sh_offset_x, sh_offset_y = 10, 8
        shadow_pts = [
            (screen_x + sh_offset_x,     screen_y + h),
            (screen_x + w + sh_offset_x, screen_y + h),
            (screen_x + w + sh_offset_x + 4, screen_y + h + sh_offset_y),
            (screen_x + sh_offset_x + 4, screen_y + h + sh_offset_y),
        ]
        pygame.draw.polygon(surface, (18, 16, 22), shadow_pts)
        # Additional soft shadow behind the body itself
        pygame.draw.rect(surface, (24, 22, 20), (screen_x + 4, screen_y + 4, w + 4, h + 4))

        if btype == 'house':
            pygame.draw.rect(surface, color, (screen_x, screen_y + 16, w, h - 16))
            pygame.draw.rect(surface, (80, 50, 20), (screen_x, screen_y + 16, w, h - 16), 2)
            roof_pts = [(screen_x, screen_y + 16), (screen_x + w, screen_y + 16),
                        (screen_x + w // 2, screen_y)]
            pygame.draw.polygon(surface, (160, 40, 40), roof_pts)
            pygame.draw.polygon(surface, (100, 20, 20), roof_pts, 2)
            pygame.draw.rect(surface, (80, 50, 20), (screen_x + w // 2 - 6, screen_y + h - 14, 12, 14))
            pygame.draw.rect(surface, (255, 230, 120), (screen_x + 8, screen_y + 22, 8, 8))
            pygame.draw.rect(surface, (255, 230, 120), (screen_x + w - 16, screen_y + 22, 8, 8))

        elif btype == 'tower':
            pygame.draw.rect(surface, color, (screen_x, screen_y, w, h))
            pygame.draw.rect(surface, (60, 60, 60), (screen_x, screen_y, w, h), 2)
            # Battlements
            for i in range(3):
                bx = screen_x + 4 + i * (w // 3)
                pygame.draw.rect(surface, color, (bx, screen_y - 6, w // 4, 8))
            # Arrow slit windows
            pygame.draw.rect(surface, (20, 20, 20), (screen_x + w // 2 - 2, screen_y + 10, 4, 10))
            pygame.draw.rect(surface, (20, 20, 20), (screen_x + w // 2 - 2, screen_y + h // 2, 4, 10))
            pygame.draw.rect(surface, (60, 40, 20), (screen_x + w // 2 - 5, screen_y + h - 14, 10, 14))

        elif btype == 'shop':
            pygame.draw.rect(surface, color, (screen_x, screen_y, w, h))
            pygame.draw.rect(surface, (80, 55, 20), (screen_x, screen_y, w, h), 2)
            # Awning
            awning_pts = [(screen_x - 4, screen_y + 10), (screen_x + w + 4, screen_y + 10),
                          (screen_x + w, screen_y + 18), (screen_x, screen_y + 18)]
            pygame.draw.polygon(surface, (180, 60, 60), awning_pts)
            # Sign
            pygame.draw.rect(surface, (200, 160, 80), (screen_x + w // 2 - 10, screen_y - 10, 20, 10))
            pygame.draw.rect(surface, (140, 90, 30), (screen_x + w // 2 - 10, screen_y - 10, 20, 10), 1)
            # Door + window
            pygame.draw.rect(surface, (60, 35, 10), (screen_x + w // 2 - 6, screen_y + h - 14, 12, 14))
            pygame.draw.rect(surface, (255, 230, 120), (screen_x + 6, screen_y + 20, 10, 10))

        elif btype == 'inn':
            pygame.draw.rect(surface, color, (screen_x, screen_y + 12, w, h - 12))
            pygame.draw.rect(surface, (100, 55, 20), (screen_x, screen_y + 12, w, h - 12), 2)
            # Wide roof
            roof_pts = [(screen_x - 4, screen_y + 12), (screen_x + w + 4, screen_y + 12),
                        (screen_x + w // 2, screen_y)]
            pygame.draw.polygon(surface, (130, 80, 30), roof_pts)
            pygame.draw.polygon(surface, (90, 50, 10), roof_pts, 2)
            # Door (wide)
            pygame.draw.rect(surface, (70, 40, 15), (screen_x + w // 2 - 8, screen_y + h - 16, 16, 16))
            # Two windows
            pygame.draw.rect(surface, (255, 230, 120), (screen_x + 8, screen_y + 20, 10, 8))
            pygame.draw.rect(surface, (255, 230, 120), (screen_x + w - 18, screen_y + 20, 10, 8))

        elif btype == 'blacksmith':
            pygame.draw.rect(surface, color, (screen_x, screen_y, w, h))
            pygame.draw.rect(surface, (50, 50, 50), (screen_x, screen_y, w, h), 2)
            # Chimney with smoke hint
            pygame.draw.rect(surface, (80, 80, 80), (screen_x + w - 12, screen_y - 10, 8, 14))
            pygame.draw.circle(surface, (120, 120, 120), (screen_x + w - 8, screen_y - 12), 4)
            # Anvil shape in window
            pygame.draw.rect(surface, (30, 30, 30), (screen_x + 6, screen_y + 14, 12, 6))
            pygame.draw.rect(surface, (30, 30, 30), (screen_x + 8, screen_y + 20, 8, 4))
            # Door
            pygame.draw.rect(surface, (40, 25, 10), (screen_x + w // 2 - 6, screen_y + h - 14, 12, 14))

        elif btype == 'temple':
            pygame.draw.rect(surface, color, (screen_x, screen_y + 10, w, h - 10))
            pygame.draw.rect(surface, (160, 120, 10), (screen_x, screen_y + 10, w, h - 10), 2)
            # Pediment (flat roof with triangle)
            pygame.draw.rect(surface, (200, 170, 50), (screen_x - 4, screen_y + 8, w + 8, 6))
            roof_pts = [(screen_x, screen_y + 8), (screen_x + w, screen_y + 8),
                        (screen_x + w // 2, screen_y - 2)]
            pygame.draw.polygon(surface, (180, 150, 40), roof_pts)
            # Pillars
            for i in range(3):
                px = screen_x + 4 + i * (w // 3)
                pygame.draw.rect(surface, (230, 200, 100), (px, screen_y + 14, 5, h - 14))
            # Door
            pygame.draw.rect(surface, (100, 70, 10), (screen_x + w // 2 - 6, screen_y + h - 16, 12, 16))

        elif btype == 'library':
            pygame.draw.rect(surface, color, (screen_x, screen_y, w, h))
            pygame.draw.rect(surface, (80, 50, 15), (screen_x, screen_y, w, h), 2)
            # Flat roof with decorative edge
            pygame.draw.rect(surface, (100, 65, 20), (screen_x - 2, screen_y - 4, w + 4, 6))
            # Tall arched windows (approximated with rect + circle)
            for i in range(2):
                wx = screen_x + 6 + i * (w // 2)
                pygame.draw.rect(surface, (200, 220, 255), (wx, screen_y + 14, 10, 16))
                pygame.draw.circle(surface, (200, 220, 255), (wx + 5, screen_y + 14), 5)
            # Door
            pygame.draw.rect(surface, (60, 35, 10), (screen_x + w // 2 - 5, screen_y + h - 14, 10, 14))

        elif btype == 'shipwreck':
            # Tilted wrecked hull half-sunk in the water
            hull = [(screen_x, screen_y + h - 6), (screen_x + w, screen_y + h - 18),
                    (screen_x + w - 8, screen_y + h), (screen_x + 8, screen_y + h)]
            pygame.draw.polygon(surface, (80, 55, 30), hull)
            pygame.draw.polygon(surface, (50, 32, 16), hull, 2)
            # Hull planks
            for i in range(1, 4):
                yln = screen_y + h - 6 - i * 4
                pygame.draw.line(surface, (60, 40, 20), (screen_x + 6, yln), (screen_x + w - 6, yln - 3), 1)
            # Broken, leaning mast
            mast_base = (screen_x + w // 2, screen_y + h - 14)
            mast_top = (screen_x + w // 2 + 14, screen_y - 6)
            pygame.draw.line(surface, (70, 45, 22), mast_base, mast_top, 4)
            # Tattered sail
            sail = [mast_top, (screen_x + w // 2 + 2, screen_y + 6), (screen_x + w // 2 + 16, screen_y + 14)]
            pygame.draw.polygon(surface, (210, 205, 185), sail)
            pygame.draw.polygon(surface, (150, 145, 125), sail, 1)
            # Foam where it meets the water
            for fxp in range(screen_x, screen_x + w, 10):
                pygame.draw.circle(surface, (220, 240, 255), (fxp + 4, screen_y + h - 2), 3, 1)

        else:
            pygame.draw.rect(surface, color, (screen_x, screen_y, w, h))
            pygame.draw.rect(surface, (50, 50, 50), (screen_x, screen_y, w, h), 2)
    
    def draw_vegetation_item(self, surface, veg, screen_x, screen_y):
        """Draw a single vegetation item"""
        size = veg['size']
        color = veg['color']

        def shade(c, f):
            return (max(0, min(255, int(c[0] * f))),
                    max(0, min(255, int(c[1] * f))),
                    max(0, min(255, int(c[2] * f))))

        if veg['type'] == 'tree':
            trunk_w = max(2, size // 4)
            # Angled ground shadow of the canopy, stretched southeast
            pygame.draw.ellipse(surface, (16, 14, 20),
                                (screen_x - size // 2 + 4, screen_y - size // 6, size + 6, size // 3))
            pygame.draw.rect(surface, (101, 67, 33),
                             (screen_x - trunk_w // 2, screen_y, trunk_w, size))
            # Layered canopy for depth, shaded from the base color
            pygame.draw.circle(surface, shade(color, 0.7),
                               (screen_x, screen_y - size // 2), size // 2 + 2)
            pygame.draw.circle(surface, color,
                               (screen_x, screen_y - size // 2), size // 2)
            pygame.draw.circle(surface, shade(color, 1.3),
                               (screen_x - size // 5, screen_y - size // 2 - size // 6),
                               size // 4)

        elif veg['type'] == 'bush':
            pygame.draw.circle(surface, shade(color, 0.7), (screen_x, screen_y), size + 1)
            pygame.draw.circle(surface, color, (screen_x, screen_y), size)
            pygame.draw.circle(surface, shade(color, 1.3),
                               (screen_x - size // 3, screen_y - size // 3), size // 3)

        elif veg['type'] == 'flower':
            pygame.draw.circle(surface, color, (screen_x, screen_y), size)
            pygame.draw.circle(surface, YELLOW, (screen_x, screen_y), max(1, size // 2))

        elif veg['type'] == 'rock':
            pygame.draw.ellipse(surface, shade(color, 0.8),
                                (screen_x - size, screen_y - size // 2, size * 2, size))
            pygame.draw.ellipse(surface, color,
                                (screen_x - size + 2, screen_y - size // 2 + 2,
                                 size * 2 - 4, size - 4))
            # Highlight
            pygame.draw.line(surface, shade(color, 1.3),
                             (screen_x - size // 2, screen_y - size // 3),
                             (screen_x + size // 3, screen_y - size // 4), 1)

        elif veg['type'] == 'mushroom':
            # Stem + cap with white spots
            stem_w = max(2, size // 3)
            pygame.draw.rect(surface, (240, 230, 200),
                             (screen_x - stem_w // 2, screen_y, stem_w, size))
            pygame.draw.ellipse(surface, shade(color, 0.7),
                                (screen_x - size, screen_y - size + 2, size * 2, size + 2))
            pygame.draw.ellipse(surface, color,
                                (screen_x - size + 1, screen_y - size + 3, size * 2 - 2, size))
            for dx in (-size // 2, 0, size // 2):
                pygame.draw.circle(surface, (250, 245, 230),
                                   (screen_x + dx, screen_y - size // 2), max(1, size // 5))

        elif veg['type'] == 'crystal':
            # Spiky upright crystal cluster
            cx = screen_x
            base_y = screen_y + 2
            for i in (-1, 0, 1):
                tip_x = cx + i * (size // 2)
                tip_y = base_y - size - (0 if i == 0 else size // 3)
                pts = [(tip_x, tip_y),
                       (tip_x - max(2, size // 3), base_y),
                       (tip_x + max(2, size // 3), base_y)]
                pygame.draw.polygon(surface, shade(color, 0.7), pts)
                pygame.draw.polygon(surface, color, pts, 1)
                # Glint
                pygame.draw.line(surface, (255, 255, 255),
                                 (tip_x, tip_y + 1), (tip_x, tip_y + size // 3), 1)

        elif veg['type'] == 'skull':
            # Cranium + jaw + eye sockets
            r = size
            pygame.draw.circle(surface, color, (screen_x, screen_y - r // 4), r)
            pygame.draw.rect(surface, color,
                             (screen_x - r // 2, screen_y + r // 5, r, r // 2))
            pygame.draw.circle(surface, (15, 10, 10),
                               (screen_x - r // 2, screen_y - r // 4), max(1, r // 4))
            pygame.draw.circle(surface, (15, 10, 10),
                               (screen_x + r // 2, screen_y - r // 4), max(1, r // 4))
            pygame.draw.line(surface, (15, 10, 10),
                             (screen_x, screen_y + r // 8), (screen_x, screen_y + r // 3), 1)

        elif veg['type'] == 'log':
            # Fallen log: horizontal cylinder with rings on the ends
            length = size * 2
            thickness = size // 2
            pygame.draw.rect(surface, shade(color, 0.7),
                             (screen_x - length // 2, screen_y - thickness // 2, length, thickness))
            pygame.draw.rect(surface, color,
                             (screen_x - length // 2 + 1, screen_y - thickness // 2 + 1,
                              length - 2, thickness - 2))
            for end_x in (screen_x - length // 2, screen_x + length // 2 - thickness // 2):
                pygame.draw.circle(surface, shade(color, 0.5),
                                   (end_x + thickness // 4, screen_y), thickness // 2)
                pygame.draw.circle(surface, (40, 25, 12),
                                   (end_x + thickness // 4, screen_y), thickness // 4, 1)

        elif veg['type'] == 'orb':
            # Glowing celestial orb — direct circles, no alpha surface per frame
            t = pygame.time.get_ticks()
            pulse = int(2 + 2 * math.sin(t * 0.004 + screen_x * 0.01))
            pygame.draw.circle(surface, shade(color, 0.7), (screen_x, screen_y), size + pulse)
            pygame.draw.circle(surface, color, (screen_x, screen_y), size // 2 + 1)
            pygame.draw.circle(surface, (255, 255, 255), (screen_x, screen_y), max(1, size // 4))

    def draw_player(self, surface, screen_x, screen_y):
        """Draw the player character with shadow, class aura, and idle bob."""
        t = pygame.time.get_ticks()
        cls = self.player.char_class
        keys = pygame.key.get_pressed()
        sprinting = (keys[pygame.K_LSHIFT] or keys[pygame.K_RSHIFT]) and self.player.stamina > 0

        # Class palette: body, accent ring, outline
        if cls == "Warrior":
            body = (60, 110, 220); aura = (130, 180, 255); outline = WHITE
        elif cls == "Mage":
            body = (90, 60, 200); aura = (220, 180, 255); outline = YELLOW
        elif cls == "Rogue":
            body = (40, 40, 60); aura = (140, 100, 180); outline = (200, 220, 255)
        else:
            body = (80, 80, 160); aura = (180, 180, 220); outline = WHITE

        # Idle breathing bob — only when standing still
        bob = int(math.sin(t * 0.004) * 2) if not sprinting else 0

        player_center_x = screen_x + 16
        player_center_y = screen_y + 16 + bob

        # Angled drop shadow at the feet — stretched southeast (like a low NW sun)
        sh_w = 28 - abs(bob)
        pygame.draw.ellipse(surface, (16, 14, 20),
                            (player_center_x - sh_w // 2 + 4, screen_y + 28, sh_w + 6, 8))

        # Spawn shield ring (intentional alpha — only when active)
        if hasattr(self.player, 'spawn_shield') and self.player.spawn_shield > 0:
            pulse = int(3 + 2 * math.sin(t * 0.01))
            alpha = min(180, int(self.player.spawn_shield * 0.6))
            shield_surf = pygame.Surface((80, 80), pygame.SRCALPHA)
            pygame.draw.circle(shield_surf, (80, 160, 255, alpha), (40, 40), 28 + pulse, 3)
            surface.blit(shield_surf, (player_center_x - 40, player_center_y - 40))

        # Which way the sprite is turned (1 = right, -1 = left)
        f = 1 if getattr(self.player, 'facing', 1) >= 0 else -1

        # Sprint motion trail — streams out behind, opposite the way we're headed
        if sprinting:
            for i in range(3):
                offset = (i + 1) * 4
                col = (max(0, body[0] // 2 - i * 10),
                       max(0, body[1] // 2 - i * 10),
                       max(0, body[2] // 2 - i * 10))
                pygame.draw.circle(surface, col,
                                   (player_center_x - f * offset, player_center_y + offset // 2),
                                   8 - i * 2)

        # Subtle aura ring under the class shape
        pygame.draw.circle(surface, aura, (player_center_x, player_center_y), 18, 1)

        # Class-specific shape: one flat body plus an outline, kept simple
        if cls == "Warrior":
            # Shield — solid and defensive
            points = self._shape_points('shield', player_center_x, player_center_y, 16)
        elif cls == "Mage":
            # Seven-pointed star — arcane
            points = self._star_polygon(player_center_x, player_center_y, 16, 7, inner=0.5)
        elif cls == "Rogue":
            # Forward chevron — fast and pointed
            points = self._shape_points('arrow', player_center_x, player_center_y, 16)
        else:
            points = self._regular_polygon(player_center_x, player_center_y, 16, 8)

        # Mirror the silhouette to match the heading
        points = [(player_center_x + f * (px - player_center_x), py) for px, py in points]
        # Fighting animation: shift the whole sprite for the lunge / recoil
        pax, pay = self.anim_offset(self.player, f, reach=18)
        if pax or pay:
            points = [(px + pax, py + pay) for px, py in points]
        pygame.draw.polygon(surface, self.hurt_tint(self.player, body), points)
        pygame.draw.polygon(surface, outline, points, 2)
        self.draw_swing_arc(surface, self.player,
                            player_center_x + pax, player_center_y + pay, 28, f)

        # Nametag above the player
        self._draw_nametag(surface, self.player_name or "Hero",
                           player_center_x, screen_y - 12, (170, 230, 255))

    # Every mob gets its own distinct silhouette. Shape alone identifies the
    # creature, so there is no per-mob drawing code and no decoration at all.
    MOB_LOOKS = {
        # name:            (shape,          body colour)
        'Goblin':          ('triangle',     (110, 160, 60)),
        'Wolf':            ('arrow',        (150, 110, 70)),
        'Zombie':          ('square',       (110, 150, 90)),
        'Skeleton':        ('hourglass',    (235, 235, 215)),
        'Bandit':          ('trapezoid',    (190, 140, 60)),
        'Spider':          ('star8',        (60, 20, 90)),
        'Orc':             ('hexagon',      (80, 140, 55)),
        'Dark Elf':        ('star5',        (130, 60, 180)),
        'Troll':           ('circle',       (135, 100, 60)),
        'Vampire':         ('bowtie',       (70, 30, 90)),
        'Dragon':          ('star6',        (180, 40, 40)),
        'Imp':             ('inv_triangle', (220, 80, 30)),
        'Demon':           ('pentagon',     (170, 35, 35)),
        'Fire Giant':      ('octagon',      (200, 80, 20)),
        'Wraith':          ('teardrop',     (150, 135, 195)),
        'Shadow Beast':    ('x_cross',      (45, 15, 75)),
        'Void Lord':       ('star4',        (35, 10, 60)),
        'Fallen Angel':    ('shield',       (205, 205, 225)),
        'Celestial Guard': ('cross',        (230, 215, 150)),
        'Seraph':          ('star12',       (255, 240, 180)),
    }

    def _draw_nametag(self, surface, text, cx, y, color=WHITE):
        """Draw a small centred name label with a dark backing so it stays readable."""
        label = self.small_font.render(text, True, color)
        w, h = label.get_width(), label.get_height()
        x = int(cx - w // 2)
        pygame.draw.rect(surface, (0, 0, 0), (x - 3, int(y) - 1, w + 6, h + 2))
        pygame.draw.rect(surface, (70, 70, 80), (x - 3, int(y) - 1, w + 6, h + 2), 1)
        surface.blit(label, (x, int(y)))

    @staticmethod
    def _regular_polygon(cx, cy, s, n, rot=-math.pi / 2):
        """n-sided regular polygon, first vertex at angle `rot` (default: pointing up)."""
        return [(cx + s * math.cos(rot + i * 2 * math.pi / n),
                 cy + s * math.sin(rot + i * 2 * math.pi / n)) for i in range(n)]

    @staticmethod
    def _star_polygon(cx, cy, s, points, inner=0.45, rot=-math.pi / 2):
        """Star with `points` spikes, alternating outer radius s and inner radius s*inner."""
        pts = []
        for i in range(points * 2):
            r = s if i % 2 == 0 else s * inner
            a = rot + i * math.pi / points
            pts.append((cx + r * math.cos(a), cy + r * math.sin(a)))
        return pts

    def _shape_points(self, shape, cx, cy, s):
        """Return polygon points for a named shape, or None to draw a circle."""
        # Regular polygons
        if shape == 'triangle':
            return self._regular_polygon(cx, cy, s, 3)
        if shape == 'inv_triangle':
            return self._regular_polygon(cx, cy, s, 3, rot=math.pi / 2)
        if shape == 'square':
            return self._regular_polygon(cx, cy, s, 4, rot=math.pi / 4)
        if shape == 'diamond':
            return self._regular_polygon(cx, cy, s, 4)
        if shape == 'pentagon':
            return self._regular_polygon(cx, cy, s, 5)
        if shape == 'hexagon':
            return self._regular_polygon(cx, cy, s, 6)
        if shape == 'heptagon':
            return self._regular_polygon(cx, cy, s, 7)
        if shape == 'octagon':
            return self._regular_polygon(cx, cy, s, 8)

        # Stars
        if shape == 'star4':
            return self._star_polygon(cx, cy, s, 4, inner=0.38)
        if shape == 'star5':
            return self._star_polygon(cx, cy, s, 5, inner=0.45)
        if shape == 'star6':
            return self._star_polygon(cx, cy, s, 6, inner=0.55)
        if shape == 'star8':
            return self._star_polygon(cx, cy, s, 8, inner=0.5)
        if shape == 'star12':
            return self._star_polygon(cx, cy, s, 12, inner=0.7)

        # Bespoke silhouettes
        if shape == 'arrow':          # chevron pointing forward
            return [(cx + s, cy), (cx - s * 0.3, cy + s * 0.9),
                    (cx - s * 0.1, cy), (cx - s * 0.3, cy - s * 0.9)]
        if shape == 'hourglass':
            return [(cx - s * 0.8, cy - s), (cx + s * 0.8, cy - s),
                    (cx - s * 0.8, cy + s), (cx + s * 0.8, cy + s)]
        if shape == 'bowtie':
            return [(cx - s, cy - s * 0.8), (cx - s, cy + s * 0.8),
                    (cx + s, cy - s * 0.8), (cx + s, cy + s * 0.8)]
        if shape == 'trapezoid':
            return [(cx - s * 0.55, cy - s * 0.8), (cx + s * 0.55, cy - s * 0.8),
                    (cx + s, cy + s * 0.8), (cx - s, cy + s * 0.8)]
        if shape == 'teardrop':
            pts = [(cx, cy - s)]
            for i in range(1, 12):
                a = -math.pi / 2 + i * (2 * math.pi * 0.82) / 12
                pts.append((cx + s * 0.85 * math.cos(a), cy + s * 0.85 * math.sin(a)))
            return pts
        if shape == 'shield':
            return [(cx - s * 0.85, cy - s * 0.9), (cx + s * 0.85, cy - s * 0.9),
                    (cx + s * 0.7, cy + s * 0.4), (cx, cy + s),
                    (cx - s * 0.7, cy + s * 0.4)]
        if shape == 'cross':          # plus sign
            a, b = s * 0.36, s
            return [(cx - a, cy - b), (cx + a, cy - b), (cx + a, cy - a),
                    (cx + b, cy - a), (cx + b, cy + a), (cx + a, cy + a),
                    (cx + a, cy + b), (cx - a, cy + b), (cx - a, cy + a),
                    (cx - b, cy + a), (cx - b, cy - a), (cx - a, cy - a)]
        if shape == 'x_cross':        # plus rotated 45 degrees
            plus = self._shape_points('cross', 0, 0, s)
            ca, sa = math.cos(math.pi / 4), math.sin(math.pi / 4)
            return [(cx + px * ca - py * sa, cy + px * sa + py * ca) for px, py in plus]

        return None  # circle

    def _draw_mob_shape(self, surface, name, cx, cy, s, facing=1, tint=0.0):
        """Draw a mob as one flat silhouette. Shape alone tells you what it is.

        `tint` 0..1 blends the body toward white, used to flash a mob that has
        just been hit.
        """
        cx, cy, s = int(cx), int(cy), int(s)
        f = 1 if facing >= 0 else -1

        def flash(col):
            if tint <= 0:
                return col
            k = tint * 0.8
            return tuple(min(255, int(c + (255 - c) * k)) for c in col)

        if name == FINAL_BOSS_NAME:
            # The final boss gets the only shape nothing else uses: a big spiked star.
            pts = self._star_polygon(cx, cy, int(s * 1.3), 10, inner=0.5)
            pygame.draw.polygon(surface, flash((30, 15, 45)), pts)
            pygame.draw.polygon(surface, flash((210, 70, 230)), pts, max(2, s // 7))
            return

        shape, body = self.MOB_LOOKS.get(name, ('hexagon', (180, 60, 60)))
        dark = flash((max(0, body[0] - 70), max(0, body[1] - 70), max(0, body[2] - 70)))
        body = flash(body)

        pts = self._shape_points(shape, cx, cy, s)
        if pts is None:
            pygame.draw.circle(surface, body, (cx, cy), s)
            pygame.draw.circle(surface, dark, (cx, cy), s, max(1, s // 6))
        else:
            # Mirror so directional silhouettes point the way the mob is heading
            pts = [(cx + f * (px - cx), py) for px, py in pts]
            pygame.draw.polygon(surface, body, pts)
            pygame.draw.polygon(surface, dark, pts, max(1, s // 6))

    def draw_enemy(self, surface, enemy, screen_x, screen_y):
        """Draw an enemy"""
        cx = screen_x + 12
        cy = screen_y + 12
        if getattr(enemy, 'is_final_boss', False):
            bfacing = getattr(enemy, 'facing', 1)
            bax, bay = self.anim_offset(enemy, bfacing, reach=30)
            self._draw_mob_shape(surface, enemy.name, cx + bax, cy + bay, 30, bfacing,
                                 tint=self.hurt_phase(enemy))
            self.draw_swing_arc(surface, enemy, cx + bax, cy + bay, 52, bfacing,
                                color=(255, 150, 220))
            # Boss name + permanent health bar
            self._draw_nametag(surface, enemy.name, cx, screen_y - 46, (255, 220, 120))
            bar_width, bar_height = 80, 7
            ratio = max(0.0, enemy.hp / enemy.max_hp)
            bx = cx - bar_width // 2
            pygame.draw.rect(surface, (60, 0, 0), (bx, screen_y - 28, bar_width, bar_height))
            pygame.draw.rect(surface, (220, 40, 40), (bx, screen_y - 28, int(bar_width * ratio), bar_height))
            pygame.draw.rect(surface, (255, 220, 120), (bx, screen_y - 28, bar_width, bar_height), 1)
            return
        size = 16
        # Angled drop shadow — leans southeast, wider than tall for a low-sun look
        pygame.draw.ellipse(surface, (16, 12, 18),
                            (cx - size, cy + size - 2, size * 2 + 8, size))
        # Threat outline ring colored by level
        ring_col = (255, 80, 80) if enemy.level >= 5 else (255, 200, 80) if enemy.level >= 3 else (235, 235, 235)
        pygame.draw.circle(surface, ring_col, (cx, cy), size + 4, 2)
        efacing = getattr(enemy, 'facing', 1)
        eax, eay = self.anim_offset(enemy, efacing, reach=size)
        self._draw_mob_shape(surface, enemy.name, cx + eax, cy + eay, size, efacing,
                             tint=self.hurt_phase(enemy))
        self.draw_swing_arc(surface, enemy, cx + eax, cy + eay,
                            int(size * 1.7), efacing, color=(255, 190, 150))
        # Always-on HP bar with backdrop so it's never lost in busy terrain
        bar_width, bar_height = 38, 5
        ratio = max(0.0, enemy.hp / enemy.max_hp)
        bx = cx - bar_width // 2
        by = cy - size - 10
        pygame.draw.rect(surface, (0, 0, 0), (bx - 1, by - 1, bar_width + 2, bar_height + 2))
        pygame.draw.rect(surface, (50, 0, 0), (bx, by, bar_width, bar_height))
        hp_col = (60, 200, 60) if ratio > 0.5 else (220, 180, 40) if ratio > 0.25 else (220, 60, 40)
        pygame.draw.rect(surface, hp_col, (bx, by, int(bar_width * ratio), bar_height))
        # Nametag above the HP bar, tinted by threat level
        self._draw_nametag(surface, f"{enemy.name} Lv{enemy.level}", cx, by - 15, ring_col)

    def draw_interactables_infinite(self, surface):
        """Draw interactable objects in infinite world"""
        t = pygame.time.get_ticks()
        for chunk_data in self.world_chunks.values():
            for obj in chunk_data.get('interactables', []):
                screen_x = obj['x'] - self.camera_x
                screen_y = obj['y'] - self.camera_y

                if (-50 <= screen_x <= SCREEN_WIDTH and -50 <= screen_y <= SCREEN_HEIGHT):
                    if obj['type'] == 'door':
                        pygame.draw.rect(surface, GREEN, (screen_x, screen_y, obj['width'], obj['height']), 1)
                        door_label = self.small_font.render(f"{obj['building_type'].title()}", True, WHITE)
                        label_x = screen_x + obj['width']//2 - door_label.get_width()//2
                        label_y = screen_y - 30
                        label_bg = pygame.Surface((door_label.get_width() + 6, door_label.get_height() + 4))
                        label_bg.fill(BLACK)
                        label_bg.set_alpha(180)
                        surface.blit(label_bg, (label_x - 3, label_y - 2))
                        surface.blit(door_label, (label_x, label_y))

                    elif obj['type'] == 'portal':
                        pw, ph = obj['width'], obj['height']
                        col = obj['color']
                        pulse = int(4 + 3 * math.sin(t * 0.004 + obj['x'] * 0.002))
                        # Outer glow
                        glow = pygame.Surface((pw + 20, ph + 20), pygame.SRCALPHA)
                        pygame.draw.ellipse(glow, (*col, 50), (0, 0, pw + 20, ph + 20))
                        surface.blit(glow, (screen_x - 10, screen_y - 10))
                        # Portal frame
                        pygame.draw.ellipse(surface, col, (screen_x, screen_y, pw, ph), 4)
                        # Portal interior shimmer
                        inner = pygame.Surface((pw - 8, ph - 8), pygame.SRCALPHA)
                        ic = (*col, 80 + pulse * 10)
                        pygame.draw.ellipse(inner, ic, (0, 0, pw - 8, ph - 8))
                        surface.blit(inner, (screen_x + 4, screen_y + 4))
                        # Label above
                        lbl = self.small_font.render(obj['label'], True, col)
                        surface.blit(lbl, (screen_x + pw//2 - lbl.get_width()//2, screen_y - 18))
                        # Prompt
                        pcx = self.player.x + 16
                        pcy = self.player.y + 16
                        ocx = obj['x'] + pw // 2
                        ocy = obj['y'] + ph // 2
                        if math.sqrt((pcx-ocx)**2 + (pcy-ocy)**2) < 60:
                            pr = self.small_font.render("E - Enter Portal", True, YELLOW)
                            surface.blit(pr, (screen_x + pw//2 - pr.get_width()//2, screen_y - 34))

                    elif obj['type'] == 'chest':
                        w, h = obj['width'], obj['height']
                        rarity = obj.get('rarity', 'common')
                        # Draw the chest centered on its (x, y) — that's where the
                        # interaction check measures from, so the visual now matches.
                        cx = screen_x - w // 2
                        cy = screen_y - h // 2
                        # Skip drawing if the chest or its hover prompt is under the HUD panel
                        hud_x, hud_y, hud_w, hud_h = 5, 5, 220, 200
                        if (cx < hud_x + hud_w and cx + w > hud_x and
                                cy - 22 < hud_y + hud_h and cy + h > hud_y):
                            continue

                        if obj['opened']:
                            # Dark open chest
                            pygame.draw.rect(surface, (60, 35, 10), (cx, cy + h // 3, w, h - h // 3))
                            pygame.draw.rect(surface, (90, 60, 20), (cx, cy, w, h // 3 + 2))
                            pygame.draw.rect(surface, (40, 25, 5), (cx, cy, w, h), 1)
                        else:
                            # Pick colours by rarity
                            if rarity == 'rare':
                                body_col  = (160, 120, 10)
                                band_col  = (220, 180, 30)
                                lock_col  = (255, 220, 60)
                            elif rarity == 'uncommon':
                                body_col  = (50, 80, 140)
                                band_col  = (80, 120, 200)
                                lock_col  = (140, 180, 255)
                            else:
                                body_col  = (101, 67, 33)
                                band_col  = (130, 90, 40)
                                lock_col  = (200, 160, 60)

                            # Glow for rare/uncommon
                            if rarity != 'common':
                                pulse = int(3 + 2 * math.sin(t * 0.005 + obj['x']))
                                glow = pygame.Surface((w + pulse * 4, h + pulse * 4), pygame.SRCALPHA)
                                gc = (255, 200, 0, 60) if rarity == 'rare' else (80, 120, 255, 50)
                                pygame.draw.rect(glow, gc, (0, 0, w + pulse * 4, h + pulse * 4), border_radius=4)
                                surface.blit(glow, (cx - pulse * 2, cy - pulse * 2))

                            # Body
                            pygame.draw.rect(surface, body_col, (cx, cy, w, h))
                            # Lid split line
                            lid_h = h // 3
                            pygame.draw.rect(surface, (max(0, body_col[0]-20),
                                                        max(0, body_col[1]-20),
                                                        max(0, body_col[2]-20)),
                                             (cx, cy, w, lid_h))
                            pygame.draw.line(surface, band_col,
                                             (cx, cy + lid_h),
                                             (cx + w, cy + lid_h), 2)
                            # Metal bands (vertical)
                            for bx in [cx + w // 4, cx + 3 * w // 4]:
                                pygame.draw.line(surface, band_col, (bx, cy), (bx, cy + h), 2)
                            # Lock
                            lx, ly = cx + w // 2, cy + lid_h + 1
                            pygame.draw.rect(surface, lock_col, (lx - 3, ly, 6, 5))
                            pygame.draw.circle(surface, lock_col, (lx, ly), 3)
                            # Outline
                            pygame.draw.rect(surface, band_col, (cx, cy, w, h), 1)

                            # "E to open" prompt when player is near
                            player_cx = self.player.x + 16
                            player_cy = self.player.y + 16
                            if math.sqrt((player_cx-obj['x'])**2 + (player_cy-obj['y'])**2) < 50:
                                prompt = self.small_font.render("E - Open", True, YELLOW)
                                surface.blit(prompt, (cx + w // 2 - prompt.get_width() // 2, cy - 16))

                    elif obj['type'] == 'npc':
                        w, h = obj['width'], obj['height']
                        col = obj.get('color', (180, 180, 180))
                        # Drop shadow
                        shadow = pygame.Surface((w + 6, 8), pygame.SRCALPHA)
                        pygame.draw.ellipse(shadow, (0, 0, 0, 130), shadow.get_rect())
                        surface.blit(shadow, (screen_x - 3, screen_y + h - 4))
                        # Body
                        pygame.draw.rect(surface, col, (screen_x + 6, screen_y + 14, w - 12, h - 18))
                        pygame.draw.rect(surface, (40, 40, 40), (screen_x + 6, screen_y + 14, w - 12, h - 18), 1)
                        # Head
                        pygame.draw.circle(surface, (240, 200, 170), (screen_x + w // 2, screen_y + 10), 7)
                        pygame.draw.circle(surface, (40, 30, 20), (screen_x + w // 2, screen_y + 10), 7, 1)
                        # Quest marker above head
                        q = obj.get('quest', {})
                        st = q.get('state', 'not_started')
                        marker = "!" if st == 'not_started' else ("?" if st == 'complete' else "")
                        if marker:
                            pulse = int(math.sin(t * 0.005) * 3)
                            m_col = (255, 230, 80) if marker == "!" else (140, 230, 120)
                            mfont = pygame.font.Font(None, 28)
                            ms = mfont.render(marker, True, m_col)
                            surface.blit(ms, (screen_x + w // 2 - ms.get_width() // 2, screen_y - 24 + pulse))
                        # Name plate
                        name_lbl = self.small_font.render(obj.get('name', 'NPC'), True, WHITE)
                        bg = pygame.Surface((name_lbl.get_width() + 6, name_lbl.get_height() + 4))
                        bg.fill(BLACK); bg.set_alpha(180)
                        surface.blit(bg, (screen_x + w // 2 - name_lbl.get_width() // 2 - 3, screen_y - 14))
                        surface.blit(name_lbl, (screen_x + w // 2 - name_lbl.get_width() // 2, screen_y - 12))

                    elif obj['type'] == 'altar':
                        w, h = obj['width'], obj['height']
                        pulse = int(4 + 4 * math.sin(t * 0.006 + obj['x'] * 0.01))
                        # Stone altar block
                        pygame.draw.rect(surface, (50, 30, 60),
                                         (screen_x, screen_y + h - 20, w, 20))
                        pygame.draw.rect(surface, (180, 80, 200),
                                         (screen_x, screen_y + h - 20, w, 20), 2)
                        # Cracked rune face on top
                        pygame.draw.rect(surface, (90, 0, 120),
                                         (screen_x + 4, screen_y + h - 16, w - 8, 12))
                        pygame.draw.line(surface, (255, 120, 220),
                                         (screen_x + w // 2, screen_y + h - 16),
                                         (screen_x + w // 2, screen_y + h - 4), 2)
                        # Hovering blood-red orb with halo
                        ox = screen_x + w // 2
                        oy = screen_y + 16 - pulse
                        pygame.draw.circle(surface, (40, 0, 0), (ox, oy), 14 + pulse // 2)
                        pygame.draw.circle(surface, (220, 30, 60), (ox, oy), 8 + pulse // 3)
                        pygame.draw.circle(surface, (255, 200, 200), (ox, oy), 3)
                        # Label
                        lbl = self.small_font.render("Resurrection Altar", True, (255, 120, 200))
                        bg = pygame.Surface((lbl.get_width() + 6, lbl.get_height() + 4))
                        bg.fill(BLACK); bg.set_alpha(180)
                        surface.blit(bg, (ox - lbl.get_width() // 2 - 3, screen_y - 24))
                        surface.blit(lbl, (ox - lbl.get_width() // 2, screen_y - 22))

                    elif obj['type'] == 'beacon':
                        w, h = obj['width'], obj['height']
                        col = obj.get('color', (180, 220, 255))
                        pulse = int(4 + 3 * math.sin(t * 0.005 + obj['x'] * 0.01))
                        # Stone base
                        pygame.draw.rect(surface, (70, 70, 80), (screen_x - 4, screen_y + h - 10, w + 8, 10))
                        pygame.draw.rect(surface, (110, 110, 130), (screen_x - 4, screen_y + h - 10, w + 8, 10), 2)
                        # Pillar
                        pygame.draw.rect(surface, (50, 55, 70), (screen_x, screen_y + 10, w, h - 18))
                        pygame.draw.rect(surface, col, (screen_x, screen_y + 10, w, h - 18), 2)
                        # Glowing orb at the top with halo
                        ox = screen_x + w // 2
                        oy = screen_y + 10
                        glow = pygame.Surface((40 + pulse * 2, 40 + pulse * 2), pygame.SRCALPHA)
                        pygame.draw.circle(glow, (*col, 80), (glow.get_width() // 2, glow.get_height() // 2),
                                           18 + pulse)
                        surface.blit(glow, (ox - glow.get_width() // 2, oy - glow.get_height() // 2))
                        pygame.draw.circle(surface, col, (ox, oy), 8 + pulse // 2)
                        pygame.draw.circle(surface, (255, 255, 255), (ox, oy), 4)
                        # Vertical light beam shooting upward
                        beam = pygame.Surface((6, 60), pygame.SRCALPHA)
                        beam.fill((*col, 90))
                        surface.blit(beam, (ox - 3, oy - 60))
                        # Label
                        lbl = self.small_font.render("Beacon", True, col)
                        bg = pygame.Surface((lbl.get_width() + 6, lbl.get_height() + 4))
                        bg.fill(BLACK); bg.set_alpha(180)
                        surface.blit(bg, (ox - lbl.get_width() // 2 - 3, screen_y - 24))
                        surface.blit(lbl, (ox - lbl.get_width() // 2, screen_y - 22))

                    elif obj['type'] == 'dungeon':
                        w, h = obj['width'], obj['height']
                        pulse = int(3 + 2 * math.sin(t * 0.005 + obj['x'] * 0.01))
                        # Stone block base
                        pygame.draw.rect(surface, (60, 55, 70), (screen_x, screen_y + h // 2, w, h // 2))
                        pygame.draw.rect(surface, (90, 80, 100), (screen_x, screen_y + h // 2, w, h // 2), 2)
                        # Dark archway
                        arch_w = w - 12
                        arch_h = h - 14
                        pygame.draw.rect(surface, (10, 8, 14), (screen_x + 6, screen_y + 6, arch_w, arch_h))
                        pygame.draw.rect(surface, (180, 160, 90), (screen_x + 6, screen_y + 6, arch_w, arch_h), 2)
                        # Inner shimmer (entrance glow)
                        glow = pygame.Surface((arch_w, arch_h), pygame.SRCALPHA)
                        glow.fill((180, 100, 220, 30 + pulse * 8))
                        surface.blit(glow, (screen_x + 6, screen_y + 6))
                        # Skull mark
                        sx = screen_x + w // 2
                        sy = screen_y + h // 2 - 2
                        pygame.draw.circle(surface, (220, 220, 220), (sx, sy), 4)
                        pygame.draw.rect(surface, (10, 8, 14), (sx - 3, sy - 1, 2, 3))
                        pygame.draw.rect(surface, (10, 8, 14), (sx + 1, sy - 1, 2, 3))
                        # Label, theme name, tier, and remaining entries
                        tier = obj.get('tier', 0)
                        dtype = obj.get('dtype', 'kingdom')
                        theme = DUNGEON_TYPES.get(dtype, DUNGEON_TYPES['kingdom'])
                        remaining = max(0, 3 - obj.get('entries_used', 0))
                        if remaining == 0:
                            label_text = f"{theme['name']} T{tier} [COLLAPSED]"
                            label_col = (140, 140, 140)
                        else:
                            label_text = f"{theme['name']} T{tier}  ({remaining}/3 left)"
                            label_col = theme['accent']
                        lbl = self.small_font.render(label_text, True, label_col)
                        bg = pygame.Surface((lbl.get_width() + 6, lbl.get_height() + 4))
                        bg.fill(BLACK); bg.set_alpha(180)
                        surface.blit(bg, (screen_x + w // 2 - lbl.get_width() // 2 - 3, screen_y - 22))
                        surface.blit(lbl, (screen_x + w // 2 - lbl.get_width() // 2, screen_y - 20))

    async def run(self):
        running = True
        while running:
            running = self.handle_events()
            self.update()
            self.draw()
            self.clock.tick(FPS)
            await asyncio.sleep(0)  # hands control back to the browser each frame (no-op on desktop)

        pygame.quit()
        if not IS_WEB:
            sys.exit()

async def main():
    await web_restore_saves()
    game = Game()
    await game.run()

# Run the game
if __name__ == "__main__":
    asyncio.run(main())
