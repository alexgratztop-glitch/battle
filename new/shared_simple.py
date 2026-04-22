# shared_simple.py
from enum import Enum


class Team(Enum):
    TEAM_A = "Team A"
    TEAM_B = "Team B"
    TEAM_C = "Team C"


class ActionType(Enum):
    MOVE = "move"
    SHOOT = "shoot"
    # Новые действия для спец-способностей (advanced-режим).
    HEAL = "heal"          # Факел: AoE-лечение союзников в heal_range.
    PHASE = "phase"        # Тишина: переключить фазу (неуязвимость).
    HOLOGRAM = "hologram"  # Провокатор: разместить голограмму в клетке.
    MINE = "mine"          # Паук: поставить мину в клетку.


class ShipType(Enum):
    CRUISER = "Крейсер"
    ARTILLERY = "Артиллерия"
    RADIO = "Радиовышка"
    BASE = "Базовый"
    # Новые типы (advanced-режим). Статы заданы ниже в Ship.__init__,
    # но сами способности (прыжок, лечение, фаза, бурав,
    # голограмма, мина) ещё не реализованы в логике сервера.
    JUMPER = "Прыгун"
    TORCH = "Факел"
    SILENCE = "Тишина"
    DRILL = "Бурав"
    PROVOCATEUR = "Провокатор"
    SPIDER = "Паук"


class Ship:
    def __init__(self, ship_id, name, team, x, y, z, ship_type=ShipType.BASE):
        self.id = ship_id
        self.name = name
        self.team = team
        self.x = x
        self.y = y
        self.z = z
        self.alive = True
        self.ship_type = ship_type
        # Флаг «фазы» для Тишины. Когда True, корабль неуязвим и не виден
        # противникам. Переключается действием PHASE.
        self.is_phased = False
        # Баланс v6: Тишина больше не может бесконечно сидеть в фазе.
        # Одна активация фазы = 1 ход неуязвимости, затем 3-ходовый кулдаун
        # (фаза автоматически спадает, корабль снова уязвим).
        self.phase_cooldown = 0

        # Безопасные значения по умолчанию — заданы для ВСЕХ типов,
        # чтобы не было AttributeError при обращении к атрибуту вне зависимости
        # от типа корабля.
        self.max_hits = 2
        self.move_range = 1
        self.can_shoot = True
        self.shoot_range = 5
        self.shoot_anywhere = False
        self.scan_whole_z = False
        # Базовый урон от выстрела. Сейчас сервер всегда снимает 1 хит,
        # но поле задано, чтобы свериться с столбцом «Атака» из ттз и легко
        # подключить, когда будем реализовывать способности.
        self.damage = 1
        # Флаги способностей (дефолты — все выключены). Их логику
        # будем добавлять следующими итерациями (по техзаданию).
        self.jump_range = 0          # Прыгун: прыгок на N клеток сквозь корабли.
        self.heal_range = 0          # Факел: радиус лечения союзников.
        self.can_phase = False       # Тишина: входит в фазу — неуязвима.
        self.drill_range = 0         # Бурав: прямая на N клеток сквозь всех.
        self.can_create_hologram = False  # Провокатор.
        self.can_place_mine = False  # Паук.
        self.mine_damage = 0

        if ship_type == ShipType.CRUISER:
            # Баланс v2: hp 1→2, чтобы крейсер не сносился с одного залпа
            # другого крейсера/артиллерии (damage=2).
            self.max_hits = 2
            # Баланс v5: +1 к move_range всем мобильным кораблям.
            self.move_range = 2
            self.can_shoot = True
            self.shoot_range = 5
            self.shoot_anywhere = False  # Только по прямой
            self.damage = 2

        elif ship_type == ShipType.ARTILLERY:
            # Баланс v2: hp 3→1. Артиллерия стреляет куда угодно и наносит 2
            # урона — даём ей «стекло» как цену мобильности=0.
            self.max_hits = 1
            self.move_range = 0  # Артиллерия не двигается
            self.can_shoot = True
            self.shoot_range = 10  # Вся карта (макс 10 клеток)
            self.shoot_anywhere = True  # Может стрелять в любую точку
            self.damage = 2

        elif ship_type == ShipType.RADIO:
            # По тз (03.дополнения): hp=3 для радиовышки.
            self.max_hits = 3
            # Баланс v5: +1 к move_range всем мобильным.
            self.move_range = 2
            self.can_shoot = False  # Радиовышка не стреляет
            self.shoot_range = 0
            self.scan_whole_z = True  # Сканирует всю плоскость Z
            self.damage = 0

        elif ship_type == ShipType.JUMPER:
            # Прыгун: hp=2, прыжок на 2 клетки сквозь корабли,
            # разрушает корабль в конечной точке. Атака 1.
            self.max_hits = 2
            # Баланс v3: jump_range 3→2 (был имбой: убивал артиллерию в 1
            # таран на дальность 3). Авторитетная дальность — jump_range;
            # держим move_range синхронно, чтобы обычные (не таранные)
            # перемещения тоже ограничивались jump_range (иначе move_range
            # оставался «мёртвым кодом» — Devin Review #4).
            self.jump_range = 2
            self.move_range = self.jump_range
            self.can_shoot = True
            self.shoot_range = 5
            self.shoot_anywhere = False
            self.damage = 1

        elif ship_type == ShipType.TORCH:
            # Факел: hp=6 (баланс v2, было 5), лечит союзников в радиусе 1,
            # move=1, атака 1. Главный корабль-саппорт — держим его живым
            # дольше, иначе в прошлых прогонах он получал dmg 367 против 47.
            self.max_hits = 6
            # Баланс v5: +1 move_range.
            self.move_range = 2
            # Баланс v3: heal_range 1→2, чтобы Факел реально успевал лечить
            # больше союзников за один AoE.
            self.heal_range = 2
            self.can_shoot = True
            self.shoot_range = 5
            self.shoot_anywhere = False
            self.damage = 1

        elif ship_type == ShipType.SILENCE:
            # Тишина: hp=2, move=1, атака 0,
            # уходит в «фазу» — неатакуемая, но сама не стреляет.
            self.max_hits = 2
            # Баланс v5: +1 move_range.
            self.move_range = 2
            self.can_shoot = False
            self.shoot_range = 0
            self.can_phase = True
            self.damage = 0

        elif ship_type == ShipType.DRILL:
            # Бурав: hp=4, move=2, атака 0,
            # прямое движение на 3 клетки сквозь всех,
            # мгновенно убивает корабль, на котором закончил движение.
            self.max_hits = 4
            # Баланс v5: +1 move_range.
            self.move_range = 3
            self.drill_range = 3
            self.can_shoot = False
            self.shoot_range = 0
            self.damage = 0

        elif ship_type == ShipType.PROVOCATEUR:
            # Провокатор: hp=2, move=1, атака 1,
            # создаёт голограмму в соседней пустой клетке.
            self.max_hits = 2
            # Баланс v5: +1 move_range.
            self.move_range = 2
            self.can_shoot = True
            self.shoot_range = 5
            self.shoot_anywhere = False
            self.can_create_hologram = True
            self.damage = 1

        elif ship_type == ShipType.SPIDER:
            # Паук: hp=1, move=1, атака 0, ставит мины в соседние клетки,
            # которые дают 2 единицы урона.
            self.max_hits = 1
            # Баланс v5: +1 move_range.
            self.move_range = 2
            self.can_shoot = False
            self.shoot_range = 0
            self.can_place_mine = True
            self.mine_damage = 2
            self.damage = 0

        else:  # Базовый
            self.max_hits = 2
            # Баланс v5: +1 move_range.
            self.move_range = 2
            self.can_shoot = True
            self.shoot_range = 5
            self.shoot_anywhere = False
            self.damage = 1

        self.hits = 0

    def move(self, x, y, z):
        """Переместить корабль в (x, y, z). Не проверяет коллизии —
        этим занимается сервер (`GameServer.process_turn`). Проверяет
        только границы карты и дальность хода."""
        if self.move_range <= 0:
            return False

        dx = abs(x - self.x)
        dy = abs(y - self.y)
        dz = abs(z - self.z)

        if max(dx, dy, dz) > self.move_range:
            return False

        # Проверка границ карты
        if not (0 <= x <= 9 and 0 <= y <= 9 and 0 <= z <= 9):
            return False

        self.x = x
        self.y = y
        self.z = z
        return True

    def can_shoot_at(self, target_x, target_y, target_z):
        """Проверяет, может ли корабль выстрелить в указанную точку."""
        if not self.can_shoot or not self.alive:
            return False

        # Для артиллерии - стрельба в любую точку в пределах диапазона
        if self.ship_type == ShipType.ARTILLERY:
            dx = abs(target_x - self.x)
            dy = abs(target_y - self.y)
            dz = abs(target_z - self.z)
            return dx <= self.shoot_range and dy <= self.shoot_range and dz <= self.shoot_range

        # Для обычных кораблей - только по одной оси (прямая линия)
        changed_axes = 0
        if target_x != self.x:
            changed_axes += 1
        if target_y != self.y:
            changed_axes += 1
        if target_z != self.z:
            changed_axes += 1

        if changed_axes != 1:
            return False

        distance = max(
            abs(target_x - self.x),
            abs(target_y - self.y),
            abs(target_z - self.z),
        )

        return distance <= self.shoot_range

    def take_hit(self, damage=1):
        """Наносит кораблю ``damage`` единиц урона. По умолчанию 1 — так
        ведут себя все базовые выстрелы в старом коде. Если damage > 0
        и это приводит к ``hits >= max_hits``, корабль помечается как
        мёртвый (``alive=False``).
        """
        if damage <= 0:
            return self.alive
        self.hits += damage
        if self.hits >= self.max_hits:
            self.alive = False
        return self.alive

    def heal(self, amount=1):
        """Восстанавливает кораблю до ``amount`` единиц здоровья
        (уменьшает ``hits``, не ниже нуля). Мёртвых кораблей не оживляет.
        """
        if not self.alive or amount <= 0:
            return False
        new_hits = max(0, self.hits - amount)
        if new_hits == self.hits:
            return False
        self.hits = new_hits
        return True

    def to_dict(self):
        return {
            'id': self.id,
            'name': f"{self.ship_type.value} {self.id}",
            'team': self.team.value,
            'type': self.ship_type.value,
            'x': self.x, 'y': self.y, 'z': self.z,
            'alive': self.alive,
            'hits': self.hits,
            'max_hits': self.max_hits,
            'can_shoot': self.can_shoot,
            'move_range': self.move_range,
            'shoot_range': self.shoot_range,
            'shoot_anywhere': getattr(self, 'shoot_anywhere', False),
            'scan_whole_z': getattr(self, 'scan_whole_z', False),
            'ship_type': self.ship_type.value,
            'is_phased': getattr(self, 'is_phased', False),
            'phase_cooldown': getattr(self, 'phase_cooldown', 0),
            # Статы новых типов — для клиента и будущей логики.
            'damage': getattr(self, 'damage', 1),
            'jump_range': getattr(self, 'jump_range', 0),
            'heal_range': getattr(self, 'heal_range', 0),
            'can_phase': getattr(self, 'can_phase', False),
            'drill_range': getattr(self, 'drill_range', 0),
            'can_create_hologram': getattr(self, 'can_create_hologram', False),
            'can_place_mine': getattr(self, 'can_place_mine', False),
            'mine_damage': getattr(self, 'mine_damage', 0),
        }


class Action:
    def __init__(self, ship_id, action_type, target_x=None, target_y=None, target_z=None):
        self.ship_id = ship_id
        self.action_type = action_type
        self.target_x = target_x
        self.target_y = target_y
        self.target_z = target_z

    def to_dict(self):
        return {
            'ship_id': self.ship_id,
            'action_type': self.action_type.value,
            'target_x': self.target_x,
            'target_y': self.target_y,
            'target_z': self.target_z,
        }

    @classmethod
    def from_dict(cls, data):
        """Десериализация из словаря (обратная операция к to_dict)."""
        return cls(
            ship_id=data['ship_id'],
            action_type=ActionType(data['action_type']),
            target_x=data.get('target_x'),
            target_y=data.get('target_y'),
            target_z=data.get('target_z'),
        )
